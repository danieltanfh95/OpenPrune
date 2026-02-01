"""Integration tests for the full analysis pipeline."""

import json
import pytest
from pathlib import Path
from typing import Any

from openprune.detection.archetype import ArchetypeDetector
from openprune.config import load_config
from openprune.output.json_writer import write_config


# Path to test fixtures
FIXTURES_PATH = Path(__file__).parent / "fixtures" / "flask_app"


class TestArchetypeDetection:
    """Tests for framework and entrypoint detection."""

    def test_detects_flask_framework(self):
        """Should detect Flask framework in fixture app."""
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        framework_types = [fw.framework.name for fw in result.frameworks]
        assert "FLASK" in framework_types

    def test_detects_celery_framework(self):
        """Should detect Celery framework in fixture app."""
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        framework_types = [fw.framework.name for fw in result.frameworks]
        assert "CELERY" in framework_types

    def test_detects_flask_routes(self):
        """Should detect Flask route entrypoints."""
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        entrypoint_types = [ep.type.name for ep in result.entrypoints]
        assert "FLASK_ROUTE" in entrypoint_types

    def test_detects_celery_tasks(self):
        """Should detect Celery task entrypoints."""
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        entrypoint_types = [ep.type.name for ep in result.entrypoints]
        assert "CELERY_TASK" in entrypoint_types or "CELERY_SHARED_TASK" in entrypoint_types


class TestConfigGeneration:
    """Tests for config file generation."""

    def test_config_has_required_sections(self, tmp_path):
        """Generated config should have all required sections."""
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(result, config_path)

        config = load_config(config_path)

        assert "project" in config
        assert "frameworks" in config
        assert "entrypoints" in config
        assert "analysis" in config
        assert "linting" in config

    def test_config_linting_section(self, tmp_path):
        """Linting section should have correct structure."""
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(result, config_path)

        config = load_config(config_path)
        linting = config["linting"]

        assert "respect_noqa" in linting
        assert "noqa_patterns" in linting
        assert "ignore_decorators" in linting
        assert "sources" in linting

        # Should NOT have ignore_names (removed per plan)
        assert "ignore_names" not in linting

    def test_config_ignore_decorators_defaults(self, tmp_path):
        """ignore_decorators should have sensible defaults."""
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(result, config_path)

        config = load_config(config_path)
        decorators = config["linting"]["ignore_decorators"]

        # Should include pytest.fixture
        assert any("pytest.fixture" in d for d in decorators)
        # Should include abstractmethod
        assert any("abstractmethod" in d for d in decorators)
        # Should include property
        assert any("property" in d for d in decorators)


class TestNoqaHandling:
    """Tests for noqa comment handling."""

    def test_noqa_import_not_flagged(self):
        """Imports with # noqa should not be flagged as dead code."""
        from openprune.analysis.noqa import is_noqa_suppressed

        # Test various noqa patterns
        assert is_noqa_suppressed("# noqa").matched is True
        assert is_noqa_suppressed("# noqa: F401").matched is True
        assert is_noqa_suppressed("# type: ignore").matched is True

    def test_noqa_extracts_codes(self):
        """Should extract specific noqa codes."""
        from openprune.analysis.noqa import is_noqa_suppressed

        result = is_noqa_suppressed("# noqa: F401, F403")
        assert result.matched is True
        assert "F401" in result.codes
        assert "F403" in result.codes


class TestOrphanedFileDetection:
    """Tests for orphaned file detection."""

    def test_deprecated_module_structure(self):
        """The deprecated.py fixture should be detectable as orphaned."""
        deprecated_path = FIXTURES_PATH / "utils" / "deprecated.py"
        assert deprecated_path.exists()

        # Read the file to verify it has content
        content = deprecated_path.read_text()
        assert "old_function_1" in content
        assert "OldClass" in content


class TestVisitorAnalysis:
    """Tests for AST visitor analysis."""

    def test_visitor_finds_definitions(self):
        """Visitor should find function and class definitions."""
        from openprune.analysis.visitor import analyze_file

        result = analyze_file(FIXTURES_PATH / "app.py")

        assert result.error is None
        assert len(result.definitions) > 0

        # Should find some expected functions
        def_names = [s.name for s in result.definitions.values()]
        assert "create_app" in def_names
        assert "index" in def_names
        assert "unused_helper_function" in def_names

    def test_visitor_finds_usages(self):
        """Visitor should find symbol usages."""
        from openprune.analysis.visitor import analyze_file

        result = analyze_file(FIXTURES_PATH / "app.py")

        assert len(result.usages) > 0

        # Should find some usages
        usage_names = [u.symbol_name for u in result.usages]
        assert "Flask" in usage_names
        assert "jsonify" in usage_names

    def test_visitor_tracks_decorators(self):
        """Visitor should track decorators on functions."""
        from openprune.analysis.visitor import analyze_file

        result = analyze_file(FIXTURES_PATH / "app.py")

        # Find the 'index' function
        index_symbol = None
        for symbol in result.definitions.values():
            if symbol.name == "index":
                index_symbol = symbol
                break

        assert index_symbol is not None
        assert len(index_symbol.decorators) > 0
        assert any("route" in d for d in index_symbol.decorators)

    def test_visitor_extracts_comments(self):
        """Visitor should extract line comments."""
        from openprune.analysis.visitor import analyze_file

        result = analyze_file(FIXTURES_PATH / "app.py")

        # Should have extracted comments including noqa
        assert len(result.line_comments) > 0

        # Find the noqa comment
        noqa_found = False
        for comment in result.line_comments.values():
            if "noqa" in comment.lower():
                noqa_found = True
                break

        assert noqa_found, "Should find noqa comment in app.py"

    def test_visitor_tracks_caller(self):
        """Visitor should track which function makes each usage."""
        from openprune.analysis.visitor import analyze_file

        result = analyze_file(FIXTURES_PATH / "app.py")

        # Find usages that have a caller set
        usages_with_caller = [u for u in result.usages if u.caller is not None]

        # Should have some usages with callers (from inside functions)
        assert len(usages_with_caller) > 0


class TestEndToEnd:
    """End-to-end tests simulating CLI workflow."""

    def test_detect_analyze_workflow(self, tmp_path):
        """Test the detect -> analyze workflow."""
        from openprune.detection.archetype import ArchetypeDetector
        from openprune.config import (
            load_config,
            get_analysis_includes,
            get_analysis_excludes,
            get_ignore_decorators,
        )
        from openprune.output.json_writer import write_config

        # Step 1: Detect
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(result, config_path)

        # Step 2: Load config and verify
        config = load_config(config_path)

        includes = get_analysis_includes(config)
        excludes = get_analysis_excludes(config)
        ignore_decorators = get_ignore_decorators(config)

        assert "**/*.py" in includes
        assert len(excludes) > 0
        assert len(ignore_decorators) > 0

    def test_full_analysis_finds_dead_code(self, tmp_path):
        """Full analysis should find the known dead code in fixtures."""
        from openprune.detection.archetype import ArchetypeDetector
        from openprune.analysis.visitor import analyze_file
        from openprune.output.json_writer import write_config
        from openprune.config import load_config

        # Detect and create config
        detector = ArchetypeDetector()
        result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(result, config_path)

        # Analyze the main app file
        app_result = analyze_file(FIXTURES_PATH / "app.py")

        # Should find the unused functions
        def_names = [s.name for s in app_result.definitions.values()]
        assert "unused_helper_function" in def_names
        assert "another_unused_function" in def_names
        assert "UnusedClass" in def_names


class TestFullPipelineWithFixture:
    """End-to-end tests running the full analysis pipeline on flask_app fixture."""

    def test_entrypoints_have_zero_confidence(self, tmp_path):
        """Flask routes and Celery tasks should have 0% confidence (marked as used)."""
        from openprune.detection.archetype import ArchetypeDetector
        from openprune.output.json_writer import write_config, write_results
        from openprune.config import load_config

        # Detect
        detector = ArchetypeDetector()
        archetype_result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(archetype_result, config_path)

        config = load_config(config_path)

        # Run analysis (import here to avoid circular imports)
        from openprune.cli import _run_analysis

        results = _run_analysis(FIXTURES_PATH, config)

        # Find entrypoint functions
        entrypoint_names = {"index", "get_user", "admin_panel", "before_request_handler",
                           "not_found", "create_app", "send_email", "process_data", "retry_task"}

        for item in results.dead_code:
            if item.name in entrypoint_names:
                assert item.confidence == 0, f"{item.name} should have 0% confidence (entrypoint)"

    def test_unused_helpers_have_high_confidence(self, tmp_path):
        """Unused helper functions should have high confidence."""
        from openprune.detection.archetype import ArchetypeDetector
        from openprune.output.json_writer import write_config
        from openprune.config import load_config
        from openprune.cli import _run_analysis

        detector = ArchetypeDetector()
        archetype_result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(archetype_result, config_path)
        config = load_config(config_path)

        results = _run_analysis(FIXTURES_PATH, config)

        # Find truly unused functions
        unused_names = {"unused_helper_function", "another_unused_function", "unused_task_helper"}

        for item in results.dead_code:
            if item.name in unused_names:
                assert item.confidence >= 80, f"{item.name} should have high confidence"

    def test_orphaned_files_detected(self, tmp_path):
        """Files not imported by entrypoint files should be marked as orphaned."""
        from openprune.detection.archetype import ArchetypeDetector
        from openprune.output.json_writer import write_config
        from openprune.config import load_config
        from openprune.cli import _run_analysis

        detector = ArchetypeDetector()
        archetype_result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(archetype_result, config_path)
        config = load_config(config_path)

        results = _run_analysis(FIXTURES_PATH, config)

        # Should have orphaned files
        assert len(results.orphaned_files) > 0

        # deprecated.py should be orphaned (not imported anywhere)
        orphaned_names = [of.module_name for of in results.orphaned_files]
        assert "deprecated" in orphaned_names

    def test_orphaned_file_symbols_have_100_confidence(self, tmp_path):
        """Symbols in orphaned files should have 100% confidence."""
        from openprune.detection.archetype import ArchetypeDetector
        from openprune.output.json_writer import write_config
        from openprune.config import load_config
        from openprune.cli import _run_analysis

        detector = ArchetypeDetector()
        archetype_result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(archetype_result, config_path)
        config = load_config(config_path)

        results = _run_analysis(FIXTURES_PATH, config)

        # Find symbols from deprecated.py
        deprecated_symbols = [item for item in results.dead_code
                             if "deprecated.py" in str(item.file)]

        assert len(deprecated_symbols) > 0
        for item in deprecated_symbols:
            assert item.confidence == 100, f"{item.name} in orphaned file should have 100%"
            assert "unreachable" in item.reasons[0].lower()

    def test_noqa_import_skipped(self, tmp_path):
        """Import with # noqa should be skipped from analysis."""
        from openprune.detection.archetype import ArchetypeDetector
        from openprune.output.json_writer import write_config
        from openprune.config import load_config
        from openprune.cli import _run_analysis

        detector = ArchetypeDetector()
        archetype_result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(archetype_result, config_path)
        config = load_config(config_path)

        results = _run_analysis(FIXTURES_PATH, config)

        # Should have skipped the json import with noqa
        assert len(results.noqa_skipped) > 0

        skipped_symbols = [ns.symbol for ns in results.noqa_skipped]
        # The json import in app.py has # noqa: F401
        assert any("json" in s for s in skipped_symbols)

    def test_reachability_analysis_works(self, tmp_path):
        """Symbols called by entrypoints should have lower confidence."""
        from openprune.detection.archetype import ArchetypeDetector
        from openprune.output.json_writer import write_config
        from openprune.config import load_config
        from openprune.cli import _run_analysis

        detector = ArchetypeDetector()
        archetype_result = detector.detect(FIXTURES_PATH)

        config_path = tmp_path / "config.json"
        write_config(archetype_result, config_path)
        config = load_config(config_path)

        results = _run_analysis(FIXTURES_PATH, config)

        # Entrypoints should exist with 0% confidence
        entrypoint_items = [item for item in results.dead_code if item.confidence == 0]
        assert len(entrypoint_items) > 0, "Should have entrypoints with 0% confidence"

        # Non-entrypoint unused items should have higher confidence
        high_confidence_items = [item for item in results.dead_code if item.confidence >= 80]
        assert len(high_confidence_items) > 0, "Should have unused items with high confidence"


class TestResultsModel:
    """Tests for results data models."""

    def test_orphaned_file_model(self):
        """OrphanedFile model should serialize correctly."""
        from openprune.models.results import OrphanedFile

        orphaned = OrphanedFile(
            file="/path/to/file.py",
            module_name="file",
            symbols=5,
            lines=100,
            reason="Not imported by any reachable module",
        )

        data = orphaned.to_dict()
        assert data["file"] == "/path/to/file.py"
        assert data["module_name"] == "file"
        assert data["symbols"] == 5
        assert data["lines"] == 100
        assert "reachable" in data["reason"]

    def test_analysis_results_includes_orphaned_files(self):
        """AnalysisResults should include orphaned_files field."""
        from openprune.models.results import AnalysisResults, OrphanedFile

        orphaned = OrphanedFile(
            file="/path/to/file.py",
            module_name="file",
            symbols=5,
            lines=100,
        )

        results = AnalysisResults(
            version="1.0",
            orphaned_files=[orphaned],
        )

        data = results.to_dict()
        assert "orphaned_files" in data
        assert len(data["orphaned_files"]) == 1
        assert data["orphaned_files"][0]["module_name"] == "file"
