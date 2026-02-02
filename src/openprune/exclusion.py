"""Centralized file exclusion logic for OpenPrune.

Handles .gitignore patterns, pyproject.toml excludes, and default patterns
using the pathspec library for proper gitignore-style matching.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pathspec
import tomli


@dataclass
class ExclusionConfig:
    """Configuration for file exclusion."""

    gitignore_patterns: list[str] = field(default_factory=list)
    pyproject_patterns: list[str] = field(default_factory=list)
    default_patterns: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # For debugging/logging


# Default patterns that are always excluded (consolidates 3 hardcoded locations)
DEFAULT_EXCLUDES = [
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    "node_modules",
    ".tox",
    ".eggs",
    "*.egg-info",
    "build",
    "dist",
]


class FileExcluder:
    """Handles file exclusion with gitignore-style pattern matching."""

    def __init__(
        self,
        project_root: Path,
        include_ignored: bool = False,
        extra_excludes: list[str] | None = None,
    ) -> None:
        """Initialize the file excluder.

        Args:
            project_root: Root directory of the project.
            include_ignored: If True, don't exclude any files (bypass all patterns).
            extra_excludes: Additional patterns to exclude.
        """
        self.project_root = project_root
        self.include_ignored = include_ignored
        self._config = ExclusionConfig()
        self._spec: pathspec.PathSpec | None = None

        if not include_ignored:
            self._load_patterns(extra_excludes or [])
            self._build_spec()

    def _load_patterns(self, extra_excludes: list[str]) -> None:
        """Load patterns from all sources."""
        # 1. Default patterns
        self._config.default_patterns = list(DEFAULT_EXCLUDES)
        self._config.sources.append("defaults")

        # 2. .gitignore patterns
        self._load_gitignore()

        # 3. pyproject.toml patterns (ruff, mypy)
        self._load_pyproject_excludes()

        # 4. Extra excludes from caller
        if extra_excludes:
            self._config.default_patterns.extend(extra_excludes)

    def _load_gitignore(self) -> None:
        """Load .gitignore patterns."""
        gitignore_path = self.project_root / ".gitignore"
        if gitignore_path.exists():
            try:
                content = gitignore_path.read_text(encoding="utf-8")
                patterns = [
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.startswith("#")
                ]
                self._config.gitignore_patterns = patterns
                self._config.sources.append(str(gitignore_path))
            except Exception:
                pass

    def _load_pyproject_excludes(self) -> None:
        """Load exclude patterns from pyproject.toml."""
        pyproject_path = self.project_root / "pyproject.toml"
        if not pyproject_path.exists():
            return

        try:
            with open(pyproject_path, "rb") as f:
                data = tomli.load(f)
        except Exception:
            return

        patterns: list[str] = []

        # Ruff excludes
        ruff = data.get("tool", {}).get("ruff", {})
        if exclude := ruff.get("exclude"):
            if isinstance(exclude, list):
                patterns.extend(exclude)
            else:
                patterns.append(str(exclude))

        # Mypy excludes
        mypy = data.get("tool", {}).get("mypy", {})
        if exclude := mypy.get("exclude"):
            if isinstance(exclude, list):
                patterns.extend(exclude)
            else:
                patterns.append(str(exclude))

        if patterns:
            self._config.pyproject_patterns = patterns
            self._config.sources.append(str(pyproject_path))

    def _build_spec(self) -> None:
        """Build the pathspec matcher from all patterns."""
        all_patterns = (
            self._config.default_patterns
            + self._config.gitignore_patterns
            + self._config.pyproject_patterns
        )
        self._spec = pathspec.PathSpec.from_lines("gitignore", all_patterns)

    def should_exclude(self, file_path: Path) -> bool:
        """Check if a file should be excluded.

        Args:
            file_path: Path to the file to check.

        Returns:
            True if the file should be excluded, False otherwise.
        """
        if self.include_ignored:
            return False

        if self._spec is None:
            return False

        try:
            rel_path = file_path.relative_to(self.project_root)
        except ValueError:
            return False

        # Check the path and all its parent directories
        # This handles patterns like "__pycache__" matching "__pycache__/foo.py"
        path_str = str(rel_path)
        if self._spec.match_file(path_str):
            return True

        # Also check each directory component
        for part in rel_path.parts[:-1]:  # Exclude the filename itself
            if self._spec.match_file(part):
                return True

        return False

    def filter_files(self, files: list[Path]) -> list[Path]:
        """Filter a list of files, removing excluded ones.

        Args:
            files: List of file paths to filter.

        Returns:
            List of files that are not excluded.
        """
        if self.include_ignored:
            return files
        return [f for f in files if not self.should_exclude(f)]

    @property
    def sources(self) -> list[str]:
        """Return list of config sources used."""
        return self._config.sources

    @property
    def patterns(self) -> list[str]:
        """Return all loaded patterns (for debugging)."""
        return (
            self._config.default_patterns
            + self._config.gitignore_patterns
            + self._config.pyproject_patterns
        )
