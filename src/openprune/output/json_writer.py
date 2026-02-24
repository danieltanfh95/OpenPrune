"""JSON output writers for config and results."""

import json
from pathlib import Path

from openprune.models.archetype import ArchetypeResult
from openprune.models.deletion import DeletionResults
from openprune.models.results import AnalysisResults
from openprune.models.verification import VerificationResults


def write_config(result: ArchetypeResult, output_path: Path) -> None:
    """Write the open-prune.json configuration file."""
    config = {
        "$schema": "https://openprune.dev/schema/config.json",
        "version": "1.0",
        "project": {
            "name": result.project_root.name,
            "python_version": result.python_version,
            "root": str(result.project_root),
        },
        "frameworks": [
            {
                "type": fw.framework,  # Now a string from plugin
                "confidence": fw.confidence,
                "evidence": fw.evidence[:5],  # Limit to 5 examples
            }
            for fw in result.frameworks
        ],
        "entry_points": {
            "rules": _build_entrypoint_rules(result),
            "detected": _build_detected_entrypoints(result),
        },
        "analysis": {
            "include": ["**/*.py"],
            "exclude": [
                "**/__pycache__/**",
                "**/tests/**",
                "**/test_*.py",
                "**/*_test.py",
                "**/conftest.py",
                "**/migrations/**",
                "**/.venv/**",
                "**/venv/**",
                "**/node_modules/**",
            ],
        },
        "linting": _build_linting_section(result),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _build_linting_section(result: ArchetypeResult) -> dict:
    """Build linting configuration from detected config."""
    detected = result.linting_config

    # Default noqa patterns
    default_noqa = ["# noqa", "# type: ignore"]

    # Merge detected noqa patterns with defaults
    noqa_patterns = list(set(default_noqa + (detected.noqa_patterns or [])))

    return {
        "respect_noqa": True,
        "noqa_patterns": noqa_patterns,
        "ignore_decorators": [
            "@pytest.fixture",
            "@pytest.mark.*",
            "@override",
            "@abstractmethod",
            "@property",
        ],
        "sources": detected.sources,
    }


def _build_entrypoint_rules(result: ArchetypeResult) -> list[dict]:
    """Build entrypoint rules/patterns from detected entrypoints."""
    # Group entrypoints by type
    type_patterns: dict[str, dict] = {}

    # Add detected patterns
    for ep in result.entrypoints:
        type_name = ep.type.name.lower()
        if type_name not in type_patterns:
            type_patterns[type_name] = {
                "type": type_name,
                "mark_as_used": True,
            }

    # Add default patterns for detected frameworks
    framework_types = {fw.framework for fw in result.frameworks}

    # Check for flask-related frameworks (flask, flask_restplus, etc.)
    has_flask = any(fw.startswith("flask") for fw in framework_types)
    has_celery = "celery" in framework_types

    if has_flask:
        type_patterns.setdefault(
            "flask_route",
            {"type": "flask_route", "pattern": "@*.route", "mark_as_used": True},
        )
        type_patterns.setdefault(
            "flask_cli",
            {"type": "flask_cli", "pattern": "@*.cli.command", "mark_as_used": True},
        )

    if has_celery:
        type_patterns.setdefault(
            "celery_task",
            {"type": "celery_task", "pattern": "@*.task", "mark_as_used": True},
        )
        type_patterns.setdefault(
            "celery_shared_task",
            {"type": "celery_shared_task", "pattern": "@shared_task", "mark_as_used": True},
        )

    # Always include these
    type_patterns.setdefault(
        "main_block",
        {"type": "main_block", "pattern": 'if __name__ == "__main__"', "mark_as_used": True},
    )
    type_patterns.setdefault(
        "factory_function",
        {
            "type": "factory_function",
            "names": ["create_app", "make_celery"],
            "mark_as_used": True,
        },
    )

    return list(type_patterns.values())


def _build_detected_entrypoints(result: ArchetypeResult) -> list[dict]:
    """Build list of actual detected entrypoints for use by analysis phase."""
    return [
        {
            "name": ep.name,
            "type": ep.type.name.lower(),
            "file": str(ep.file),
            "line": ep.line,
            "decorator": ep.decorator,
            "arguments": ep.arguments,
        }
        for ep in result.entrypoints
    ]


def write_results(results: AnalysisResults, output_path: Path) -> None:
    """Write the openprune-results.json file."""
    data = results.to_dict()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_config(config_path: Path) -> dict:
    """Load an open-prune.json configuration file."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_results(results_path: Path) -> dict:
    """Load an openprune-results.json file."""
    with open(results_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_verification_results(results: VerificationResults, output_path: Path) -> None:
    """Write the verified.json file."""
    data = results.to_dict()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_verification_results(results_path: Path) -> dict:
    """Load a verified.json file."""
    with open(results_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_deletion_results(results: DeletionResults, output_path: Path) -> None:
    """Write the removals.json file."""
    data = results.to_dict()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_deletion_results(results_path: Path) -> dict:
    """Load a removals.json file."""
    with open(results_path, "r", encoding="utf-8") as f:
        return json.load(f)
