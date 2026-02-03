"""Pydantic model framework plugin.

Detects Pydantic BaseModel classes and reduces false positives from:
- Model fields used via type composition (nested models)
- Validators and computed fields
- Config class attributes
- Model fields that are accessed at runtime via Pydantic magic
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


# Pydantic base class patterns
PYDANTIC_BASES = {
    "BaseModel",
    "pydantic.BaseModel",
    "BaseSettings",
    "pydantic.BaseSettings",
    "pydantic_settings.BaseSettings",
}

# Pydantic decorators that indicate a method is used
PYDANTIC_DECORATORS = {
    "field_validator",
    "model_validator",
    "field_serializer",
    "model_serializer",
    "computed_field",
    "validator",  # Pydantic v1
    "root_validator",  # Pydantic v1
}


class PydanticPlugin:
    """Plugin for Pydantic model detection."""

    @property
    def name(self) -> str:
        return "pydantic"

    @property
    def framework_type(self) -> str:
        return "pydantic"

    @property
    def import_indicators(self) -> list[str]:
        return [
            "pydantic",
            "BaseModel",
            "pydantic_settings",
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
                pattern="field_validator",
                score_adjustment=-30,
                description="Pydantic field validator",
            ),
            DecoratorScoringRule(
                pattern="model_validator",
                score_adjustment=-30,
                description="Pydantic model validator",
            ),
            DecoratorScoringRule(
                pattern="field_serializer",
                score_adjustment=-30,
                description="Pydantic field serializer",
            ),
            DecoratorScoringRule(
                pattern="model_serializer",
                score_adjustment=-30,
                description="Pydantic model serializer",
            ),
            DecoratorScoringRule(
                pattern="computed_field",
                score_adjustment=-30,
                description="Pydantic computed field",
            ),
            DecoratorScoringRule(
                pattern="validator",
                score_adjustment=-30,
                description="Pydantic v1 validator",
            ),
            DecoratorScoringRule(
                pattern="root_validator",
                score_adjustment=-30,
                description="Pydantic v1 root validator",
            ),
        ]

    def detect_entrypoints(
        self,
        tree: ast.AST,
        file_path: Path,
    ) -> list[DetectedEntrypoint]:
        """Pydantic models are NOT marked as entrypoints.

        Previously we marked BaseModel subclasses as entrypoints, but this
        prevented detecting truly unused Pydantic models. Instead:
        - TYPE_HINT tracking captures nested model composition (user: UserModel)
        - CALL tracking captures direct instantiation (User())
        - IMPORT tracking captures imports from other modules

        Used models will have their confidence reduced by name_used_penalty.
        Unused models will be flagged as dead code (which is correct).
        """
        return []

    def is_implicit_name(
        self,
        name: str,
        parent_classes: list[str],
        decorators: list[str],
    ) -> bool:
        """Check if name is implicitly used by Pydantic.

        Returns True for:
        - Attributes on BaseModel classes (field definitions)
        - Methods with Pydantic decorators
        - Config inner class
        - model_config attribute
        """
        # Check for Pydantic decorators
        for decorator in decorators:
            dec_lower = decorator.lower()
            for pattern in PYDANTIC_DECORATORS:
                if pattern in dec_lower:
                    return True

        # Check if this is a BaseModel class attribute
        for parent in parent_classes:
            if self._is_pydantic_model(parent):
                # Special Pydantic names that are always used
                if name in {"Config", "model_config", "__validators__", "__fields__"}:
                    return True
                # Any non-dunder, non-private attribute on a model is a field
                if not name.startswith("_"):
                    return True

        return False

    def _is_pydantic_model(self, class_name: str) -> bool:
        """Check if class name indicates a Pydantic model."""
        return class_name in PYDANTIC_BASES


class _PydanticVisitor(ast.NodeVisitor):
    """AST visitor to find Pydantic model classes."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.entrypoints: list[DetectedEntrypoint] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Detect BaseModel subclasses."""
        for base in node.bases:
            base_name = self._get_base_name(base)
            if base_name in PYDANTIC_BASES:
                self.entrypoints.append(
                    DetectedEntrypoint(
                        name=node.name,
                        type=EntrypointType.INFRA_ENTRYPOINT,
                        line=node.lineno,
                        file=self.file_path,
                        arguments={
                            "reason": "Pydantic Model",
                        },
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


def create_plugin() -> PydanticPlugin:
    """Factory function for plugin discovery."""
    return PydanticPlugin()
