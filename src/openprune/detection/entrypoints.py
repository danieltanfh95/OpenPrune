"""Entrypoint discovery via AST analysis and plugins."""

from __future__ import annotations

import ast
from pathlib import Path

from openprune.models.archetype import Entrypoint
from openprune.plugins import get_registry
from openprune.plugins.protocol import DetectedEntrypoint


def detect_entrypoints(tree: ast.AST, file_path: Path) -> list[Entrypoint]:
    """Detect all entrypoints in a file using registered plugins.

    Args:
        tree: Parsed AST of the file
        file_path: Path to the file

    Returns:
        List of detected entrypoints
    """
    entrypoints: list[Entrypoint] = []
    registry = get_registry()

    # Run each plugin's detection
    for plugin in registry.all_plugins():
        detected = plugin.detect_entrypoints(tree, file_path)
        for d in detected:
            entrypoints.append(_convert_to_entrypoint(d))

    return entrypoints


def _convert_to_entrypoint(detected: DetectedEntrypoint) -> Entrypoint:
    """Convert a plugin DetectedEntrypoint to the model Entrypoint."""
    return Entrypoint(
        type=detected.type,
        name=detected.name,
        file=detected.file,
        line=detected.line,
        decorator=detected.decorator,
        arguments=detected.arguments if detected.arguments else None,
    )
