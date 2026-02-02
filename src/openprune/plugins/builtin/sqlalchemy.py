"""SQLAlchemy ORM framework plugin.

Detects SQLAlchemy model classes and reduces false positives from:
- db.Column attributes (replaced by descriptors at runtime)
- db.relationship() calls (dynamic string references)
- Model classes imported for side-effects (Flask-Migrate metadata)
- ORM decorators: @validates, @hybrid_property, @hybrid_method
"""

from __future__ import annotations

import ast
from pathlib import Path

from openprune.models.archetype import EntrypointType
from openprune.plugins.protocol import (
    DecoratorScoringRule,
    DetectedEntrypoint,
    ImplicitName,
)


# SQLAlchemy base class patterns
SQLALCHEMY_BASES = {
    "Model",  # Flask-SQLAlchemy: db.Model
    "db.Model",
    "Base",  # SQLAlchemy: Base = declarative_base()
    "DeclarativeBase",  # SQLAlchemy 2.0
    "AbstractConcreteBase",
}

# SQLAlchemy decorators
SQLALCHEMY_DECORATORS = {
    "validates",
    "hybrid_property",
    "hybrid_method",
    "reconstructor",
    "listens_for",
}


class SQLAlchemyPlugin:
    """Plugin for SQLAlchemy ORM detection."""

    @property
    def name(self) -> str:
        return "sqlalchemy"

    @property
    def framework_type(self) -> str:
        return "sqlalchemy"

    @property
    def import_indicators(self) -> list[str]:
        return [
            "sqlalchemy",
            "flask_sqlalchemy",
            "SQLAlchemy",
        ]

    @property
    def factory_functions(self) -> list[str]:
        return []

    @property
    def implicit_names(self) -> list[ImplicitName]:
        return []

    @property
    def decorator_scoring_rules(self) -> list[DecoratorScoringRule]:
        return [
            DecoratorScoringRule(
                pattern="validates",
                score_adjustment=-30,
                description="SQLAlchemy validation decorator",
            ),
            DecoratorScoringRule(
                pattern="hybrid_property",
                score_adjustment=-30,
                description="SQLAlchemy hybrid property",
            ),
            DecoratorScoringRule(
                pattern="hybrid_method",
                score_adjustment=-30,
                description="SQLAlchemy hybrid method",
            ),
            DecoratorScoringRule(
                pattern="reconstructor",
                score_adjustment=-30,
                description="SQLAlchemy reconstructor",
            ),
            DecoratorScoringRule(
                pattern="listens_for",
                score_adjustment=-30,
                description="SQLAlchemy event listener",
            ),
        ]

    def detect_entrypoints(
        self,
        tree: ast.AST,
        file_path: Path,
    ) -> list[DetectedEntrypoint]:
        """Detect SQLAlchemy model classes.

        Note: We mark Model classes as entrypoints because they're used
        indirectly via SQLAlchemy metadata introspection (especially with
        Flask-Migrate), even if not explicitly called in Python code.
        """
        visitor = _SQLAlchemyVisitor(file_path)
        visitor.visit(tree)
        return visitor.entrypoints

    def is_implicit_name(
        self,
        name: str,
        parent_classes: list[str],
        decorators: list[str],
    ) -> bool:
        """Check if name is implicitly used by SQLAlchemy ORM.

        Returns True for:
        - Attributes on Model classes (e.g., id = db.Column(...))
        - Methods with SQLAlchemy decorators
        """
        # Check if this is a Model class attribute
        for parent in parent_classes:
            if self._is_model_class(parent):
                # Any non-dunder attribute on a Model is implicitly used by ORM
                if not (name.startswith("__") and name.endswith("__")):
                    return True

        # Check for SQLAlchemy decorators
        for decorator in decorators:
            for pattern in SQLALCHEMY_DECORATORS:
                if pattern in decorator.lower():
                    return True

        return False

    def _is_model_class(self, class_name: str) -> bool:
        """Check if class name indicates a SQLAlchemy Model."""
        return class_name in SQLALCHEMY_BASES


class _SQLAlchemyVisitor(ast.NodeVisitor):
    """AST visitor to find SQLAlchemy model classes."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.entrypoints: list[DetectedEntrypoint] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Detect Model subclasses."""
        for base in node.bases:
            base_name = self._get_base_name(base)
            if base_name in SQLALCHEMY_BASES:
                self.entrypoints.append(
                    DetectedEntrypoint(
                        name=node.name,
                        type=EntrypointType.INFRA_ENTRYPOINT,
                        line=node.lineno,
                        file=self.file_path,
                        arguments={"reason": "SQLAlchemy Model"},
                    )
                )
                break

        self.generic_visit(node)

    def _get_base_name(self, node: ast.expr) -> str:
        """Get the name of a base class."""
        match node:
            case ast.Name(id=name):
                return name
            case ast.Attribute():
                parts: list[str] = []
                current: ast.expr = node
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                return ".".join(reversed(parts))
        return ""


def create_plugin() -> SQLAlchemyPlugin:
    """Factory function for plugin discovery."""
    return SQLAlchemyPlugin()
