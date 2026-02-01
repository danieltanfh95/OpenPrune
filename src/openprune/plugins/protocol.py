"""Plugin protocol for framework detection."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openprune.models.archetype import EntrypointType, FrameworkType


@dataclass
class DetectedEntrypoint:
    """An entrypoint detected by a plugin."""

    name: str
    type: EntrypointType
    line: int
    file: Path
    decorator: str | None = None
    arguments: dict = field(default_factory=dict)
    parent_class: str | None = None  # For class-based entrypoints (e.g., Resource)


@dataclass
class ImplicitName:
    """A name that shouldn't be flagged as dead under certain conditions."""

    name: str
    context: str  # Description of when this is implicit
    parent_classes: list[str] = field(default_factory=list)
    score_adjustment: int = -40


@dataclass
class DecoratorScoringRule:
    """Rule for adjusting confidence score based on decorators."""

    pattern: str  # Pattern to match (e.g., "api.expect*")
    score_adjustment: int  # Negative = less suspicious
    description: str


@runtime_checkable
class FrameworkPlugin(Protocol):
    """Protocol for framework detection plugins.

    Plugins can detect entrypoints via AST analysis and define
    implicit names that should not be flagged as dead code.
    """

    @property
    def name(self) -> str:
        """Human-readable plugin name."""
        ...

    @property
    def framework_type(self) -> FrameworkType:
        """The framework type this plugin handles."""
        ...

    @property
    def import_indicators(self) -> list[str]:
        """Import names that indicate this framework is in use.

        E.g., ["flask_restplus", "flask_restx"] for Flask-RESTPlus.
        """
        ...

    @property
    def factory_functions(self) -> list[str]:
        """Function names that are app factories.

        E.g., ["create_app", "make_api"]
        """
        ...

    @property
    def implicit_names(self) -> list[ImplicitName]:
        """Names that should not be flagged as dead code.

        E.g., HTTP methods on Resource subclasses.
        """
        ...

    @property
    def decorator_scoring_rules(self) -> list[DecoratorScoringRule]:
        """Rules for adjusting confidence scores based on decorators."""
        ...

    def detect_entrypoints(
        self,
        tree: ast.AST,
        file_path: Path,
    ) -> list[DetectedEntrypoint]:
        """Analyze an AST tree and return detected entrypoints.

        This is the main detection method. Plugins have full AST access
        to detect complex patterns like class inheritance.

        Args:
            tree: Parsed AST of a Python file
            file_path: Path to the file being analyzed

        Returns:
            List of detected entrypoints
        """
        ...

    def is_implicit_name(
        self,
        name: str,
        parent_classes: list[str],
        decorators: list[str],
    ) -> bool:
        """Check if a name should be considered implicitly used.

        Args:
            name: The symbol name to check
            parent_classes: Classes this symbol's class inherits from
            decorators: Decorators on this symbol

        Returns:
            True if the name should not be flagged as dead code
        """
        ...
