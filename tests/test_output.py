"""Tests for the output module."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from openprune.models.archetype import (
    ArchetypeResult,
    Entrypoint,
    EntrypointType,
    FrameworkDetection,
    LintingConfig,
)
from openprune.models.results import (
    AnalysisMetadata,
    AnalysisResults,
    AnalysisSummary,
    DeadCodeItem,
    OrphanedFile,
)
from openprune.models.verification import (
    LLMVerdict,
    VerificationResults,
    VerificationSummary,
    VerifiedItem,
)
from openprune.output.json_writer import (
    load_config,
    load_results,
    load_verification_results,
    write_config,
    write_results,
    write_verification_results,
)


class TestWriteConfig:
    """Tests for write_config function."""

    def test_writes_valid_json(self, tmp_path: Path):
        """Should write valid JSON file."""
        result = ArchetypeResult(
            project_root=tmp_path,
            frameworks=[],
            entrypoints=[],
            linting_config=LintingConfig(),
        )
        output = tmp_path / "config.json"

        write_config(result, output)

        assert output.exists()
        with open(output) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_includes_required_sections(self, tmp_path: Path):
        """Should include all required sections."""
        result = ArchetypeResult(
            project_root=tmp_path,
            frameworks=[],
            entrypoints=[],
            linting_config=LintingConfig(),
        )
        output = tmp_path / "config.json"

        write_config(result, output)

        with open(output) as f:
            data = json.load(f)

        assert "$schema" in data
        assert "version" in data
        assert "project" in data
        assert "frameworks" in data
        assert "entrypoints" in data
        assert "analysis" in data
        assert "linting" in data

    def test_includes_project_info(self, tmp_path: Path):
        """Should include project information."""
        result = ArchetypeResult(
            project_root=tmp_path,
            python_version="3.11",
            frameworks=[],
            entrypoints=[],
            linting_config=LintingConfig(),
        )
        output = tmp_path / "config.json"

        write_config(result, output)

        with open(output) as f:
            data = json.load(f)

        assert data["project"]["name"] == tmp_path.name
        assert data["project"]["python_version"] == "3.11"

    def test_includes_detected_frameworks(self, tmp_path: Path):
        """Should include detected frameworks."""
        result = ArchetypeResult(
            project_root=tmp_path,
            frameworks=[
                FrameworkDetection(
                    framework="flask",
                    confidence=0.95,
                    evidence=["app.py"],
                ),
                FrameworkDetection(
                    framework="celery",
                    confidence=0.85,
                    evidence=["tasks.py"],
                ),
            ],
            entrypoints=[],
            linting_config=LintingConfig(),
        )
        output = tmp_path / "config.json"

        write_config(result, output)

        with open(output) as f:
            data = json.load(f)

        assert len(data["frameworks"]) == 2
        assert data["frameworks"][0]["type"] == "flask"
        assert data["frameworks"][1]["type"] == "celery"

    def test_includes_linting_config(self, tmp_path: Path):
        """Should include linting configuration."""
        result = ArchetypeResult(
            project_root=tmp_path,
            frameworks=[],
            entrypoints=[],
            linting_config=LintingConfig(sources=["pyproject.toml"]),
        )
        output = tmp_path / "config.json"

        write_config(result, output)

        with open(output) as f:
            data = json.load(f)

        linting = data["linting"]
        assert "respect_noqa" in linting
        assert "noqa_patterns" in linting
        assert "ignore_decorators" in linting
        assert "@pytest.fixture" in linting["ignore_decorators"]

    def test_includes_default_entrypoints(self, tmp_path: Path):
        """Should include default entrypoint patterns."""
        result = ArchetypeResult(
            project_root=tmp_path,
            frameworks=[],
            entrypoints=[],
            linting_config=LintingConfig(),
        )
        output = tmp_path / "config.json"

        write_config(result, output)

        with open(output) as f:
            data = json.load(f)

        ep_types = [ep["type"] for ep in data["entrypoints"]]
        assert "main_block" in ep_types
        assert "factory_function" in ep_types

    def test_includes_flask_entrypoints_when_detected(self, tmp_path: Path):
        """Should include Flask entrypoints when Flask is detected."""
        result = ArchetypeResult(
            project_root=tmp_path,
            frameworks=[
                FrameworkDetection(framework="flask", confidence=0.9, evidence=[]),
            ],
            entrypoints=[],
            linting_config=LintingConfig(),
        )
        output = tmp_path / "config.json"

        write_config(result, output)

        with open(output) as f:
            data = json.load(f)

        ep_types = [ep["type"] for ep in data["entrypoints"]]
        assert "flask_route" in ep_types
        assert "flask_cli" in ep_types

    def test_includes_celery_entrypoints_when_detected(self, tmp_path: Path):
        """Should include Celery entrypoints when Celery is detected."""
        result = ArchetypeResult(
            project_root=tmp_path,
            frameworks=[
                FrameworkDetection(framework="celery", confidence=0.9, evidence=[]),
            ],
            entrypoints=[],
            linting_config=LintingConfig(),
        )
        output = tmp_path / "config.json"

        write_config(result, output)

        with open(output) as f:
            data = json.load(f)

        ep_types = [ep["type"] for ep in data["entrypoints"]]
        assert "celery_task" in ep_types
        assert "celery_shared_task" in ep_types


class TestWriteResults:
    """Tests for write_results function."""

    def test_writes_valid_json(self, tmp_path: Path):
        """Should write valid JSON file."""
        results = AnalysisResults(version="1.0")
        output = tmp_path / "results.json"

        write_results(results, output)

        assert output.exists()
        with open(output) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_includes_dead_code_items(self, tmp_path: Path):
        """Should include dead code items."""
        results = AnalysisResults(
            version="1.0",
            dead_code=[
                DeadCodeItem(
                    qualified_name="module.func",
                    name="func",
                    type="unused_function",
                    file=Path("module.py"),
                    line=10,
                    confidence=85,
                    reasons=["No references"],
                )
            ],
        )
        output = tmp_path / "results.json"

        write_results(results, output)

        with open(output) as f:
            data = json.load(f)

        assert len(data["dead_code"]) == 1
        assert data["dead_code"][0]["name"] == "func"
        assert data["dead_code"][0]["confidence"] == 85

    def test_includes_orphaned_files(self, tmp_path: Path):
        """Should include orphaned files."""
        results = AnalysisResults(
            version="1.0",
            orphaned_files=[
                OrphanedFile(
                    file="deprecated.py",
                    module_name="deprecated",
                    symbols=5,
                    lines=100,
                )
            ],
        )
        output = tmp_path / "results.json"

        write_results(results, output)

        with open(output) as f:
            data = json.load(f)

        assert len(data["orphaned_files"]) == 1
        assert data["orphaned_files"][0]["module_name"] == "deprecated"

    def test_includes_metadata(self, tmp_path: Path):
        """Should include metadata when present."""
        results = AnalysisResults(
            version="1.0",
            metadata=AnalysisMetadata(
                project="test-project",
                analyzed_at=datetime.now(),
                openprune_version="1.0.0",
                files_analyzed=10,
                total_symbols=50,
                analysis_duration_ms=1000,
            ),
        )
        output = tmp_path / "results.json"

        write_results(results, output)

        with open(output) as f:
            data = json.load(f)

        assert data["metadata"]["project"] == "test-project"
        assert data["metadata"]["files_analyzed"] == 10


class TestLoadConfig:
    """Tests for load_config function."""

    def test_loads_valid_config(self, tmp_path: Path):
        """Should load valid config file."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "version": "1.0",
            "project": {"name": "test"},
        }))

        result = load_config(config_path)

        assert result["version"] == "1.0"
        assert result["project"]["name"] == "test"

    def test_raises_on_missing_file(self, tmp_path: Path):
        """Should raise error for missing file."""
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.json")

    def test_raises_on_invalid_json(self, tmp_path: Path):
        """Should raise error for invalid JSON."""
        config_path = tmp_path / "config.json"
        config_path.write_text("not valid json {{{")

        with pytest.raises(json.JSONDecodeError):
            load_config(config_path)


class TestLoadResults:
    """Tests for load_results function."""

    def test_loads_valid_results(self, tmp_path: Path):
        """Should load valid results file."""
        results_path = tmp_path / "results.json"
        results_path.write_text(json.dumps({
            "version": "1.0",
            "dead_code": [{"name": "func"}],
        }))

        result = load_results(results_path)

        assert result["version"] == "1.0"
        assert len(result["dead_code"]) == 1


class TestWriteVerificationResults:
    """Tests for write_verification_results function."""

    def test_writes_valid_json(self, tmp_path: Path):
        """Should write valid JSON file."""
        results = VerificationResults(
            version="1.0",
            metadata={"llm_tool": "claude"},
            summary=VerificationSummary(delete_count=5),
        )
        output = tmp_path / "verified.json"

        write_verification_results(results, output)

        assert output.exists()
        with open(output) as f:
            data = json.load(f)
        assert data["version"] == "1.0"

    def test_includes_verified_items(self, tmp_path: Path):
        """Should include verified items."""
        results = VerificationResults(
            version="1.0",
            metadata={},
            summary=VerificationSummary(),
            verified_items=[
                VerifiedItem(
                    qualified_name="module.func",
                    name="func",
                    type="unused_function",
                    file=Path("module.py"),
                    line=10,
                    original_confidence=85,
                    reasons=[],
                    verdict=LLMVerdict.DELETE,
                    llm_reasoning="Dead code",
                    verified_at=datetime.now(),
                )
            ],
        )
        output = tmp_path / "verified.json"

        write_verification_results(results, output)

        with open(output) as f:
            data = json.load(f)

        assert len(data["verified_items"]) == 1
        # Verdict is serialized as lowercase value
        assert data["verified_items"][0]["verdict"] == "delete"


class TestLoadVerificationResults:
    """Tests for load_verification_results function."""

    def test_loads_valid_verification(self, tmp_path: Path):
        """Should load valid verification file."""
        path = tmp_path / "verified.json"
        path.write_text(json.dumps({
            "version": "1.0",
            "verified_items": [
                {"qualified_name": "a", "verdict": "DELETE"}
            ],
        }))

        result = load_verification_results(path)

        assert result["version"] == "1.0"
        assert len(result["verified_items"]) == 1


class TestRoundTrip:
    """Tests for round-trip serialization."""

    def test_config_round_trip(self, tmp_path: Path):
        """Should preserve data through write/load cycle."""
        original = ArchetypeResult(
            project_root=tmp_path,
            python_version="3.11",
            frameworks=[
                FrameworkDetection(framework="flask", confidence=0.9, evidence=["app.py"]),
            ],
            entrypoints=[
                Entrypoint(
                    type=EntrypointType.FLASK_ROUTE,
                    name="index",
                    file="app.py",
                    line=10,
                )
            ],
            linting_config=LintingConfig(sources=["pyproject.toml"]),
        )
        output = tmp_path / "config.json"

        write_config(original, output)
        loaded = load_config(output)

        assert loaded["project"]["python_version"] == "3.11"
        assert len(loaded["frameworks"]) == 1
        assert loaded["frameworks"][0]["type"] == "flask"

    def test_results_round_trip(self, tmp_path: Path):
        """Should preserve data through write/load cycle."""
        original = AnalysisResults(
            version="1.0",
            metadata=AnalysisMetadata(
                project="test",
                analyzed_at=datetime.now(),
                openprune_version="1.0.0",
                files_analyzed=5,
                total_symbols=25,
                analysis_duration_ms=500,
            ),
            dead_code=[
                DeadCodeItem(
                    qualified_name="module.func",
                    name="func",
                    type="unused_function",
                    file=Path("module.py"),
                    line=10,
                    confidence=85,
                    reasons=["No references"],
                )
            ],
            orphaned_files=[
                OrphanedFile(file="old.py", module_name="old", symbols=3, lines=50)
            ],
        )
        output = tmp_path / "results.json"

        write_results(original, output)
        loaded = load_results(output)

        assert loaded["version"] == "1.0"
        assert loaded["metadata"]["files_analyzed"] == 5
        assert len(loaded["dead_code"]) == 1
        assert loaded["dead_code"][0]["confidence"] == 85
        assert len(loaded["orphaned_files"]) == 1
