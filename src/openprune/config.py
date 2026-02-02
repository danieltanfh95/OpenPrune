"""Configuration loading and saving for OpenPrune."""

import json
from pathlib import Path



def load_config(config_path: Path) -> dict:
    """Load an open-prune.json configuration file."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict, config_path: Path) -> None:
    """Save configuration to open-prune.json."""
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_analysis_includes(config: dict) -> list[str]:
    """Get include patterns from config."""
    return config.get("analysis", {}).get("include", ["**/*.py"])


def get_analysis_excludes(config: dict) -> list[str]:
    """Get exclude patterns from config."""
    return config.get("analysis", {}).get(
        "exclude",
        [
            "**/__pycache__/**",
            "**/tests/**",
            "**/test_*.py",
            "**/*_test.py",
            "**/conftest.py",
            "**/migrations/**",
            "**/alembic/**",
            "**/.venv/**",
            "**/venv/**",
        ],
    )


def get_ignore_decorators(config: dict) -> list[str]:
    """Get decorator patterns to ignore from config."""
    return config.get("linting", {}).get(
        "ignore_decorators",
        ["@pytest.fixture", "@pytest.mark.*", "@property", "@abstractmethod"],
    )


def get_ignore_names(config: dict) -> list[str]:
    """Get name patterns to ignore from config."""
    return config.get("linting", {}).get(
        "ignore_names",
        ["_*", "__*__", "test_*", "setUp", "tearDown"],
    )


def should_respect_noqa(config: dict) -> bool:
    """Check if noqa comments should be respected."""
    return config.get("linting", {}).get("respect_noqa", True)


def get_noqa_patterns(config: dict) -> list[str]:
    """Get noqa patterns from config."""
    return config.get("linting", {}).get(
        "noqa_patterns",
        ["# noqa", "# type: ignore"],
    )


def get_entrypoint_types_to_mark(config: dict) -> set[str]:
    """Get entrypoint types that should be marked as used."""
    entry_points = config.get("entry_points", {})
    rules = entry_points.get("rules", [])
    return {
        ep.get("type", "")
        for ep in rules
        if ep.get("mark_as_used", True)
    }
