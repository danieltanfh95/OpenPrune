"""Tests for the grep-based import verification layer.

These tests verify the verification module that uses grep to detect
false positives in orphaned file detection.
"""

from pathlib import Path

import pytest

from openprune.analysis.verification import (
    VerificationReport,
    VerificationResult,
    file_to_module_path,
    find_import_patterns,
    search_for_imports,
    verify_all_orphans,
    verify_orphaned_module,
)


class TestFileToModulePath:
    """Tests for file_to_module_path conversion."""

    def test_simple_file(self, tmp_path: Path):
        """Should convert simple file path to module."""
        result = file_to_module_path(tmp_path / "app.py", tmp_path)
        assert result == "app"

    def test_nested_file(self, tmp_path: Path):
        """Should convert nested file path to dotted module."""
        result = file_to_module_path(tmp_path / "src/utils/helpers.py", tmp_path)
        assert result == "src.utils.helpers"

    def test_init_file(self, tmp_path: Path):
        """Should handle __init__.py as package."""
        result = file_to_module_path(tmp_path / "mypackage/__init__.py", tmp_path)
        assert result == "mypackage"

    def test_absolute_path(self, tmp_path: Path):
        """Should handle absolute paths correctly."""
        result = file_to_module_path(str(tmp_path / "app.py"), str(tmp_path))
        assert result == "app"


class TestFindImportPatterns:
    """Tests for import pattern generation."""

    def test_simple_module(self):
        """Should generate patterns for simple module."""
        patterns = find_import_patterns("utils")
        assert "from utils import" in patterns
        assert "import utils" in patterns

    def test_nested_module(self):
        """Should generate regex-escaped patterns for nested module.

        Security: Patterns are escaped to prevent regex injection from
        malicious module paths (e.g., filenames with regex metacharacters).
        """
        patterns = find_import_patterns("src.utils.helpers")
        # Dots are escaped for safe regex matching
        assert r"from src\.utils\.helpers import" in patterns
        assert r"import src\.utils\.helpers" in patterns
        assert r"from src\.utils import helpers" in patterns


class TestSearchForImports:
    """Tests for grep-based import searching."""

    def test_finds_direct_import(self, tmp_path: Path):
        """Should find direct import statements."""
        # Create a file that imports utils
        (tmp_path / "app.py").write_text("from utils import helper\nhelper()")
        (tmp_path / "utils.py").write_text("def helper(): pass")

        importers, patterns = search_for_imports("utils", tmp_path)

        assert len(importers) >= 1
        assert any("app.py" in imp for imp in importers)

    def test_finds_import_statement(self, tmp_path: Path):
        """Should find 'import X' statements."""
        (tmp_path / "app.py").write_text("import utils\nutils.helper()")
        (tmp_path / "utils.py").write_text("def helper(): pass")

        importers, patterns = search_for_imports("utils", tmp_path)

        assert len(importers) >= 1
        assert any("app.py" in imp for imp in importers)

    def test_excludes_venv(self, tmp_path: Path):
        """Should exclude .venv directory from search."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "bad.py").write_text("from utils import helper")
        (tmp_path / "good.py").write_text("from utils import helper")

        importers, patterns = search_for_imports("utils", tmp_path)

        # Should only find good.py, not .venv/bad.py
        assert not any(".venv" in imp for imp in importers)

    def test_returns_empty_for_no_imports(self, tmp_path: Path):
        """Should return empty list when no imports found."""
        (tmp_path / "app.py").write_text("# No imports here")

        importers, patterns = search_for_imports("utils", tmp_path)

        assert importers == []
        assert patterns == []


class TestVerifyOrphanedModule:
    """Tests for single module verification."""

    def test_detects_false_positive(self, tmp_path: Path):
        """Should detect when a module is actually imported (false positive)."""
        (tmp_path / "app.py").write_text("from utils import helper\nhelper()")
        (tmp_path / "utils.py").write_text("def helper(): pass")

        result = verify_orphaned_module("utils", tmp_path / "utils.py", tmp_path)

        assert result.is_orphaned is False
        assert result.importer_count >= 1
        assert result.confidence == "likely_false_positive"

    def test_confirms_true_orphan(self, tmp_path: Path):
        """Should confirm when a module is truly orphaned."""
        (tmp_path / "app.py").write_text("# No imports")
        (tmp_path / "orphan.py").write_text("def unused(): pass")

        result = verify_orphaned_module("orphan", tmp_path / "orphan.py", tmp_path)

        assert result.is_orphaned is True
        assert result.importer_count == 0
        assert result.confidence == "confirmed"

    def test_excludes_self_import(self, tmp_path: Path):
        """Should not count self-imports as evidence of usage."""
        (tmp_path / "utils.py").write_text("from utils import helper")  # Self-import

        result = verify_orphaned_module("utils", tmp_path / "utils.py", tmp_path)

        # Should be orphaned since only the file itself imports it
        assert result.is_orphaned is True


class TestVerifyAllOrphans:
    """Tests for batch verification of orphaned files."""

    def test_separates_false_positives_from_orphans(self, tmp_path: Path):
        """Should correctly categorize false positives and true orphans."""
        # Setup: app.py imports utils, but orphan.py is not imported
        (tmp_path / "app.py").write_text("from utils import helper")
        (tmp_path / "utils.py").write_text("def helper(): pass")
        (tmp_path / "orphan.py").write_text("def unused(): pass")

        orphaned_files = [
            {"file": str(tmp_path / "utils.py"), "module_name": "utils"},
            {"file": str(tmp_path / "orphan.py"), "module_name": "orphan"},
        ]

        report = verify_all_orphans(orphaned_files, tmp_path)

        assert len(report.false_positives) == 1
        assert len(report.confirmed_orphans) == 1
        assert report.false_positives[0].module_name == "utils"
        assert report.confirmed_orphans[0].module_name == "orphan"

    def test_calculates_false_positive_rate(self, tmp_path: Path):
        """Should calculate correct false positive rate."""
        (tmp_path / "app.py").write_text("from a import x\nfrom b import y")
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.py").write_text("y = 2")
        (tmp_path / "c.py").write_text("z = 3")  # Truly orphaned

        orphaned_files = [
            {"file": str(tmp_path / "a.py"), "module_name": "a"},
            {"file": str(tmp_path / "b.py"), "module_name": "b"},
            {"file": str(tmp_path / "c.py"), "module_name": "c"},
        ]

        report = verify_all_orphans(orphaned_files, tmp_path)

        # 2 false positives out of 3 = 66.67%
        assert report.false_positive_rate == pytest.approx(2 / 3, rel=0.01)


class TestVerificationResultSerialization:
    """Tests for serialization of verification results."""

    def test_verification_result_to_dict(self):
        """Should serialize VerificationResult to dict."""
        result = VerificationResult(
            module_name="utils",
            file_path="/src/utils.py",
            is_orphaned=False,
            importer_count=5,
            sample_importers=["/src/app.py", "/src/main.py"],
            import_patterns_found=["from utils import"],
            confidence="likely_false_positive",
        )

        d = result.to_dict()

        assert d["module_name"] == "utils"
        assert d["is_orphaned"] is False
        assert d["importer_count"] == 5
        assert d["confidence"] == "likely_false_positive"

    def test_verification_report_to_dict(self, tmp_path: Path):
        """Should serialize VerificationReport to dict."""
        report = VerificationReport(
            total_reported_orphaned=10,
            false_positives=[
                VerificationResult(
                    module_name="utils",
                    file_path="/src/utils.py",
                    is_orphaned=False,
                    importer_count=5,
                    sample_importers=[],
                    import_patterns_found=[],
                    confidence="likely_false_positive",
                )
            ],
            confirmed_orphans=[],
            false_positive_rate=0.1,
        )

        d = report.to_dict()

        assert d["summary"]["total_reported_orphaned"] == 10
        assert d["summary"]["false_positives_count"] == 1
        assert d["summary"]["false_positive_rate"] == "10.0%"
        assert len(d["false_positives"]) == 1
