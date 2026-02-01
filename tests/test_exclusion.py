"""Tests for the exclusion module."""

from pathlib import Path

import pytest

from openprune.exclusion import DEFAULT_EXCLUDES, FileExcluder


class TestDefaultExcludes:
    """Tests for default exclusion patterns."""

    def test_default_excludes_list(self) -> None:
        """Verify DEFAULT_EXCLUDES contains expected patterns."""
        assert "__pycache__" in DEFAULT_EXCLUDES
        assert ".venv" in DEFAULT_EXCLUDES
        assert "venv" in DEFAULT_EXCLUDES
        assert ".git" in DEFAULT_EXCLUDES
        assert "node_modules" in DEFAULT_EXCLUDES

    def test_excludes_pycache(self, tmp_path: Path) -> None:
        """Should exclude __pycache__ directories."""
        excluder = FileExcluder(tmp_path)

        pycache_file = tmp_path / "__pycache__" / "module.cpython-311.pyc"
        assert excluder.should_exclude(pycache_file)

    def test_excludes_venv(self, tmp_path: Path) -> None:
        """Should exclude .venv and venv directories."""
        excluder = FileExcluder(tmp_path)

        venv_file = tmp_path / ".venv" / "lib" / "python3.11" / "site.py"
        assert excluder.should_exclude(venv_file)

        venv_file2 = tmp_path / "venv" / "lib" / "python3.11" / "site.py"
        assert excluder.should_exclude(venv_file2)

    def test_excludes_git(self, tmp_path: Path) -> None:
        """Should exclude .git directories."""
        excluder = FileExcluder(tmp_path)

        git_file = tmp_path / ".git" / "objects" / "pack" / "something"
        assert excluder.should_exclude(git_file)

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        """Should exclude node_modules directories."""
        excluder = FileExcluder(tmp_path)

        node_file = tmp_path / "node_modules" / "package" / "index.js"
        assert excluder.should_exclude(node_file)

    def test_does_not_exclude_source_files(self, tmp_path: Path) -> None:
        """Should not exclude normal source files."""
        excluder = FileExcluder(tmp_path)

        src_file = tmp_path / "src" / "app.py"
        assert not excluder.should_exclude(src_file)

        root_file = tmp_path / "main.py"
        assert not excluder.should_exclude(root_file)


class TestGitignorePatterns:
    """Tests for .gitignore pattern parsing."""

    def test_loads_gitignore_patterns(self, tmp_path: Path) -> None:
        """Should load patterns from .gitignore."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.log\nbuild/\n# comment\n\n")

        excluder = FileExcluder(tmp_path)

        assert excluder.should_exclude(tmp_path / "app.log")
        assert excluder.should_exclude(tmp_path / "build" / "output.py")

    def test_gitignore_comments_ignored(self, tmp_path: Path) -> None:
        """Should ignore comments in .gitignore."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("# *.py\nactual_pattern/\n")

        excluder = FileExcluder(tmp_path)

        # *.py should not be excluded (it's a comment)
        assert not excluder.should_exclude(tmp_path / "app.py")
        # actual_pattern should be excluded
        assert excluder.should_exclude(tmp_path / "actual_pattern" / "file.txt")

    def test_gitignore_glob_patterns(self, tmp_path: Path) -> None:
        """Should handle glob patterns in .gitignore."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("**/temp/\n*.tmp\n")

        excluder = FileExcluder(tmp_path)

        assert excluder.should_exclude(tmp_path / "deep" / "nested" / "temp" / "file.txt")
        assert excluder.should_exclude(tmp_path / "data.tmp")

    def test_missing_gitignore_ok(self, tmp_path: Path) -> None:
        """Should work without .gitignore file."""
        excluder = FileExcluder(tmp_path)

        # Should still apply default excludes
        assert excluder.should_exclude(tmp_path / "__pycache__" / "foo.pyc")
        # Normal files should not be excluded
        assert not excluder.should_exclude(tmp_path / "app.py")


class TestPyprojectExcludes:
    """Tests for pyproject.toml exclusion patterns."""

    def test_loads_ruff_excludes(self, tmp_path: Path) -> None:
        """Should load exclude patterns from [tool.ruff]."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[tool.ruff]
exclude = ["migrations", "generated"]
""")

        excluder = FileExcluder(tmp_path)

        assert excluder.should_exclude(tmp_path / "migrations" / "001_initial.py")
        assert excluder.should_exclude(tmp_path / "generated" / "api.py")

    def test_loads_mypy_excludes(self, tmp_path: Path) -> None:
        """Should load exclude patterns from [tool.mypy]."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[tool.mypy]
exclude = ["legacy"]
""")

        excluder = FileExcluder(tmp_path)

        assert excluder.should_exclude(tmp_path / "legacy" / "old_code.py")

    def test_merges_ruff_and_mypy_excludes(self, tmp_path: Path) -> None:
        """Should merge excludes from both ruff and mypy."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[tool.ruff]
exclude = ["ruff_exclude"]

[tool.mypy]
exclude = ["mypy_exclude"]
""")

        excluder = FileExcluder(tmp_path)

        assert excluder.should_exclude(tmp_path / "ruff_exclude" / "file.py")
        assert excluder.should_exclude(tmp_path / "mypy_exclude" / "file.py")

    def test_mypy_string_exclude(self, tmp_path: Path) -> None:
        """Should handle mypy exclude as string (not list)."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[tool.mypy]
exclude = "single_exclude"
""")

        excluder = FileExcluder(tmp_path)

        assert excluder.should_exclude(tmp_path / "single_exclude" / "file.py")

    def test_missing_pyproject_ok(self, tmp_path: Path) -> None:
        """Should work without pyproject.toml file."""
        excluder = FileExcluder(tmp_path)

        # Should still apply default excludes
        assert excluder.should_exclude(tmp_path / "__pycache__" / "foo.pyc")


class TestIncludeIgnoredFlag:
    """Tests for the include_ignored flag."""

    def test_include_ignored_bypasses_all_exclusions(self, tmp_path: Path) -> None:
        """When include_ignored=True, nothing should be excluded."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.log\n")

        excluder = FileExcluder(tmp_path, include_ignored=True)

        # Should NOT exclude even with patterns
        assert not excluder.should_exclude(tmp_path / "app.log")
        assert not excluder.should_exclude(tmp_path / "__pycache__" / "foo.pyc")
        assert not excluder.should_exclude(tmp_path / ".venv" / "lib" / "site.py")

    def test_include_ignored_filter_files_returns_all(self, tmp_path: Path) -> None:
        """filter_files should return all files when include_ignored=True."""
        excluder = FileExcluder(tmp_path, include_ignored=True)

        files = [
            tmp_path / "src" / "app.py",
            tmp_path / "__pycache__" / "app.cpython-311.pyc",
            tmp_path / ".venv" / "site.py",
        ]

        filtered = excluder.filter_files(files)
        assert filtered == files


class TestExtraExcludes:
    """Tests for extra_excludes parameter."""

    def test_extra_excludes_added(self, tmp_path: Path) -> None:
        """Should add extra patterns to exclusions."""
        excluder = FileExcluder(tmp_path, extra_excludes=["custom_exclude"])

        assert excluder.should_exclude(tmp_path / "custom_exclude" / "file.py")

    def test_extra_excludes_merge_with_defaults(self, tmp_path: Path) -> None:
        """Extra excludes should merge with defaults."""
        excluder = FileExcluder(tmp_path, extra_excludes=["custom"])

        # Default should still work
        assert excluder.should_exclude(tmp_path / "__pycache__" / "foo.pyc")
        # Extra should also work
        assert excluder.should_exclude(tmp_path / "custom" / "file.py")


class TestFilterFiles:
    """Tests for the filter_files method."""

    def test_filter_files_removes_excluded(self, tmp_path: Path) -> None:
        """filter_files should remove excluded files."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("excluded/\n")

        excluder = FileExcluder(tmp_path)

        files = [
            tmp_path / "src" / "app.py",
            tmp_path / "excluded" / "hidden.py",
            tmp_path / "tests" / "test_app.py",
        ]

        filtered = excluder.filter_files(files)

        assert len(filtered) == 2
        assert tmp_path / "src" / "app.py" in filtered
        assert tmp_path / "tests" / "test_app.py" in filtered
        assert tmp_path / "excluded" / "hidden.py" not in filtered

    def test_filter_files_empty_list(self, tmp_path: Path) -> None:
        """filter_files should handle empty list."""
        excluder = FileExcluder(tmp_path)

        filtered = excluder.filter_files([])
        assert filtered == []


class TestSourcesAndPatterns:
    """Tests for sources and patterns properties."""

    def test_sources_includes_defaults(self, tmp_path: Path) -> None:
        """sources should include 'defaults'."""
        excluder = FileExcluder(tmp_path)

        assert "defaults" in excluder.sources

    def test_sources_includes_gitignore(self, tmp_path: Path) -> None:
        """sources should include .gitignore path when present."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.log\n")

        excluder = FileExcluder(tmp_path)

        assert str(gitignore) in excluder.sources

    def test_sources_includes_pyproject(self, tmp_path: Path) -> None:
        """sources should include pyproject.toml path when excludes present."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[tool.ruff]
exclude = ["migrations"]
""")

        excluder = FileExcluder(tmp_path)

        assert str(pyproject) in excluder.sources

    def test_patterns_returns_all_patterns(self, tmp_path: Path) -> None:
        """patterns property should return all loaded patterns."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.log\n")

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[tool.ruff]
exclude = ["migrations"]
""")

        excluder = FileExcluder(tmp_path)

        patterns = excluder.patterns
        assert "*.log" in patterns
        assert "migrations" in patterns
        assert "__pycache__" in patterns  # From defaults


class TestEdgeCases:
    """Tests for edge cases."""

    def test_file_outside_project_root(self, tmp_path: Path) -> None:
        """Files outside project root should not raise errors."""
        excluder = FileExcluder(tmp_path)

        # File outside project root
        outside_file = tmp_path.parent / "outside.py"
        # Should return False (not excluded) without raising
        assert not excluder.should_exclude(outside_file)

    def test_invalid_gitignore_encoding(self, tmp_path: Path) -> None:
        """Should handle .gitignore with invalid encoding gracefully."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_bytes(b"\xff\xfe*.log\n")  # Invalid UTF-8

        # Should not raise, just skip the file
        excluder = FileExcluder(tmp_path)
        assert not excluder.should_exclude(tmp_path / "app.py")

    def test_invalid_pyproject_toml(self, tmp_path: Path) -> None:
        """Should handle invalid pyproject.toml gracefully."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("invalid toml content [[[")

        # Should not raise, just skip the file
        excluder = FileExcluder(tmp_path)
        assert not excluder.should_exclude(tmp_path / "app.py")
