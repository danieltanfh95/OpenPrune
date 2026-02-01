"""Centralized path management for OpenPrune output files."""

from pathlib import Path

# Directory name for OpenPrune outputs
OPENPRUNE_DIR = ".openprune"

# File names within the .openprune directory
CONFIG_FILE = "config.json"
RESULTS_FILE = "results.json"
VERIFIED_FILE = "verified.json"  # Step 3: LLM-verified results
REMOVALS_FILE = "removals.json"  # Step 4: Planned removals


def get_openprune_dir(project_path: Path) -> Path:
    """Get the .openprune directory path for a project."""
    return project_path / OPENPRUNE_DIR


def ensure_openprune_dir(project_path: Path) -> Path:
    """Ensure .openprune directory exists and return its path."""
    openprune_dir = get_openprune_dir(project_path)
    openprune_dir.mkdir(parents=True, exist_ok=True)
    return openprune_dir


def get_config_path(project_path: Path) -> Path:
    """Get the config.json path for a project."""
    return get_openprune_dir(project_path) / CONFIG_FILE


def get_results_path(project_path: Path) -> Path:
    """Get the results.json path for a project."""
    return get_openprune_dir(project_path) / RESULTS_FILE


def get_verified_path(project_path: Path) -> Path:
    """Get the verified.json path for a project (Step 3: LLM verification)."""
    return get_openprune_dir(project_path) / VERIFIED_FILE


def get_removals_path(project_path: Path) -> Path:
    """Get the removals.json path for a project (Step 4: Planned removals)."""
    return get_openprune_dir(project_path) / REMOVALS_FILE
