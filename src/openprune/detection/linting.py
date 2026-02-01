"""Linting configuration detection."""

import configparser
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from openprune.models.archetype import LintingConfig


class LintingDetector:
    """Detect linting configuration from various config files."""

    def detect(self, project_path: Path) -> LintingConfig:
        """Detect and aggregate linting configuration."""
        config = LintingConfig()

        # Check pyproject.toml
        pyproject = project_path / "pyproject.toml"
        if pyproject.exists():
            self._parse_pyproject(pyproject, config)

        # Check ruff.toml
        for ruff_file in ["ruff.toml", ".ruff.toml"]:
            ruff_path = project_path / ruff_file
            if ruff_path.exists():
                self._parse_ruff(ruff_path, config)

        # Check .flake8
        flake8_path = project_path / ".flake8"
        if flake8_path.exists():
            self._parse_flake8(flake8_path, config)

        # Check setup.cfg
        setup_cfg = project_path / "setup.cfg"
        if setup_cfg.exists():
            self._parse_setup_cfg(setup_cfg, config)

        return config

    def _parse_pyproject(self, path: Path, config: LintingConfig) -> None:
        """Extract linting config from pyproject.toml."""
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            return

        config.sources.append(str(path))

        # Ruff config in pyproject.toml
        if ruff := data.get("tool", {}).get("ruff"):
            if exclude := ruff.get("exclude"):
                config.excluded_paths.extend(exclude)
            if ignore := ruff.get("ignore"):
                config.ignore_patterns.extend(ignore)
            if per_file_ignores := ruff.get("per-file-ignores"):
                for pattern, codes in per_file_ignores.items():
                    if isinstance(codes, list):
                        config.ignore_patterns.extend(f"{pattern}:{code}" for code in codes)

            # Ruff lint section
            if lint := ruff.get("lint"):
                if ignore := lint.get("ignore"):
                    config.ignore_patterns.extend(ignore)
                if per_file_ignores := lint.get("per-file-ignores"):
                    for pattern, codes in per_file_ignores.items():
                        if isinstance(codes, list):
                            config.ignore_patterns.extend(f"{pattern}:{code}" for code in codes)

        # Mypy config
        if mypy := data.get("tool", {}).get("mypy"):
            if exclude := mypy.get("exclude"):
                if isinstance(exclude, list):
                    config.excluded_paths.extend(exclude)
                else:
                    config.excluded_paths.append(exclude)

        # Vulture config (if they're migrating)
        if vulture := data.get("tool", {}).get("vulture"):
            if exclude := vulture.get("exclude"):
                config.excluded_paths.extend(exclude)
            if ignore_decorators := vulture.get("ignore_decorators"):
                config.ignore_patterns.extend(ignore_decorators)
            if ignore_names := vulture.get("ignore_names"):
                config.ignore_patterns.extend(ignore_names)

    def _parse_ruff(self, path: Path, config: LintingConfig) -> None:
        """Parse ruff.toml configuration."""
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            return

        config.sources.append(str(path))

        if exclude := data.get("exclude"):
            config.excluded_paths.extend(exclude)
        if ignore := data.get("ignore"):
            config.ignore_patterns.extend(ignore)

        # Lint section
        if lint := data.get("lint"):
            if ignore := lint.get("ignore"):
                config.ignore_patterns.extend(ignore)

    def _parse_flake8(self, path: Path, config: LintingConfig) -> None:
        """Parse .flake8 configuration."""
        parser = configparser.ConfigParser()
        try:
            parser.read(path)
        except Exception:
            return

        config.sources.append(str(path))

        if parser.has_section("flake8"):
            if exclude := parser.get("flake8", "exclude", fallback=None):
                config.excluded_paths.extend(p.strip() for p in exclude.split(",") if p.strip())
            if ignore := parser.get("flake8", "ignore", fallback=None):
                config.ignore_patterns.extend(p.strip() for p in ignore.split(",") if p.strip())
            if per_file_ignores := parser.get("flake8", "per-file-ignores", fallback=None):
                # Format: filename:CODE,CODE\nfilename:CODE
                for line in per_file_ignores.split("\n"):
                    line = line.strip()
                    if line:
                        config.ignore_patterns.append(line)

    def _parse_setup_cfg(self, path: Path, config: LintingConfig) -> None:
        """Parse setup.cfg for linting configuration."""
        parser = configparser.ConfigParser()
        try:
            parser.read(path)
        except Exception:
            return

        # Check for flake8 section
        if parser.has_section("flake8"):
            config.sources.append(f"{path}[flake8]")
            if exclude := parser.get("flake8", "exclude", fallback=None):
                config.excluded_paths.extend(p.strip() for p in exclude.split(",") if p.strip())
            if ignore := parser.get("flake8", "ignore", fallback=None):
                config.ignore_patterns.extend(p.strip() for p in ignore.split(",") if p.strip())

        # Check for mypy section
        if parser.has_section("mypy"):
            if exclude := parser.get("mypy", "exclude", fallback=None):
                config.excluded_paths.extend(p.strip() for p in exclude.split(",") if p.strip())
