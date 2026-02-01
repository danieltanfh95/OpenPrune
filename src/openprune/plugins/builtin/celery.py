"""Celery framework plugin.

Detects entrypoints in Celery applications:
- @app.task and @celery.task decorators
- @shared_task decorator
- Signal handlers (@task_success.connect, etc.)
"""

from __future__ import annotations

import ast
from pathlib import Path

from openprune.models.archetype import EntrypointType, FrameworkType
from openprune.plugins.protocol import (
    DecoratorScoringRule,
    DetectedEntrypoint,
    ImplicitName,
)


# Decorator patterns for Celery
DECORATOR_PATTERNS: dict[tuple[str, ...], EntrypointType] = {
    # Celery tasks
    ("celery", "task"): EntrypointType.CELERY_TASK,
    ("app", "task"): EntrypointType.CELERY_TASK,
    # Celery signals
    ("task_success", "connect"): EntrypointType.CELERY_SIGNAL,
    ("task_failure", "connect"): EntrypointType.CELERY_SIGNAL,
    ("task_prerun", "connect"): EntrypointType.CELERY_SIGNAL,
    ("task_postrun", "connect"): EntrypointType.CELERY_SIGNAL,
    ("worker_ready", "connect"): EntrypointType.CELERY_SIGNAL,
    ("celeryd_init", "connect"): EntrypointType.CELERY_SIGNAL,
    ("beat_init", "connect"): EntrypointType.CELERY_SIGNAL,
}

# Factory function names for Celery apps
FACTORY_FUNCTIONS = {"make_celery", "create_celery", "celery_factory"}


class CeleryPlugin:
    """Plugin for Celery applications."""

    @property
    def name(self) -> str:
        return "celery"

    @property
    def framework_type(self) -> FrameworkType:
        return FrameworkType.CELERY

    @property
    def import_indicators(self) -> list[str]:
        return ["celery", "Celery"]

    @property
    def factory_functions(self) -> list[str]:
        return list(FACTORY_FUNCTIONS)

    @property
    def implicit_names(self) -> list[ImplicitName]:
        """Celery has no implicit method names."""
        return []

    @property
    def decorator_scoring_rules(self) -> list[DecoratorScoringRule]:
        return [
            DecoratorScoringRule(
                pattern=".task",
                score_adjustment=-40,
                description="Celery task decorator",
            ),
            DecoratorScoringRule(
                pattern="shared_task",
                score_adjustment=-40,
                description="Celery shared_task decorator",
            ),
            DecoratorScoringRule(
                pattern=".connect",
                score_adjustment=-40,
                description="Celery signal handler",
            ),
        ]

    def detect_entrypoints(
        self,
        tree: ast.AST,
        file_path: Path,
    ) -> list[DetectedEntrypoint]:
        """Detect Celery entrypoints in an AST."""
        visitor = _CeleryVisitor(file_path)
        visitor.visit(tree)
        return visitor.entrypoints

    def is_implicit_name(
        self,
        name: str,
        parent_classes: list[str],
        decorators: list[str],
    ) -> bool:
        """Celery doesn't have implicit method names."""
        return False


class _CeleryVisitor(ast.NodeVisitor):
    """AST visitor to find Celery entrypoint patterns."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.entrypoints: list[DetectedEntrypoint] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Check for factory functions
        if node.name in FACTORY_FUNCTIONS:
            self.entrypoints.append(
                DetectedEntrypoint(
                    name=node.name,
                    type=EntrypointType.FACTORY_FUNCTION,
                    line=node.lineno,
                    file=self.file_path,
                )
            )

        # Check decorators
        for decorator in node.decorator_list:
            ep = self._parse_decorator(decorator, node)
            if ep:
                self.entrypoints.append(ep)

        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        # Same logic as FunctionDef
        self.visit_FunctionDef(node)  # type: ignore

    def _parse_decorator(
        self, decorator: ast.expr, func: ast.FunctionDef
    ) -> DetectedEntrypoint | None:
        """Parse a decorator and check if it's a Celery entrypoint pattern."""
        match decorator:
            # @app.task() or @celery.task() - Call with attribute
            case ast.Call(func=ast.Attribute(value=ast.Name(id=obj), attr=attr)):
                pattern = (obj, attr)
                if pattern in DECORATOR_PATTERNS:
                    return DetectedEntrypoint(
                        name=func.name,
                        type=DECORATOR_PATTERNS[pattern],
                        line=func.lineno,
                        file=self.file_path,
                        decorator=f"@{obj}.{attr}",
                        arguments=self._extract_decorator_args(decorator),
                    )
                # Check for signal handlers like @task_success.connect
                return self._check_signal_decorator(decorator, func, obj, attr)

            # @app.task without call - Attribute only
            case ast.Attribute(value=ast.Name(id=obj), attr=attr):
                pattern = (obj, attr)
                if pattern in DECORATOR_PATTERNS:
                    return DetectedEntrypoint(
                        name=func.name,
                        type=DECORATOR_PATTERNS[pattern],
                        line=func.lineno,
                        file=self.file_path,
                        decorator=f"@{obj}.{attr}",
                    )

            # @shared_task() - Call with name
            case ast.Call(func=ast.Name(id="shared_task")):
                return DetectedEntrypoint(
                    name=func.name,
                    type=EntrypointType.CELERY_SHARED_TASK,
                    line=func.lineno,
                    file=self.file_path,
                    decorator="@shared_task",
                    arguments=self._extract_decorator_args(decorator),
                )

            # @shared_task - Name only
            case ast.Name(id="shared_task"):
                return DetectedEntrypoint(
                    name=func.name,
                    type=EntrypointType.CELERY_SHARED_TASK,
                    line=func.lineno,
                    file=self.file_path,
                    decorator="@shared_task",
                )

        return None

    def _check_signal_decorator(
        self, decorator: ast.Call, func: ast.FunctionDef, obj: str, attr: str
    ) -> DetectedEntrypoint | None:
        """Check for signal decorator patterns like @task_success.connect."""
        # Check if pattern looks like signal.connect
        if attr == "connect":
            pattern = (obj, attr)
            if pattern in DECORATOR_PATTERNS:
                return DetectedEntrypoint(
                    name=func.name,
                    type=DECORATOR_PATTERNS[pattern],
                    line=func.lineno,
                    file=self.file_path,
                    decorator=f"@{obj}.{attr}",
                    arguments=self._extract_decorator_args(decorator),
                )
        return None

    def _extract_decorator_args(self, call: ast.Call) -> dict:
        """Extract arguments from decorator call."""
        args: dict = {}

        if call.args:
            positional = []
            for a in call.args:
                try:
                    if isinstance(a, ast.Constant):
                        positional.append(a.value)
                    else:
                        positional.append(ast.unparse(a))
                except Exception:
                    pass
            if positional:
                args["positional"] = positional

        for kw in call.keywords:
            if kw.arg:
                try:
                    if isinstance(kw.value, ast.Constant):
                        args[kw.arg] = kw.value.value
                    else:
                        args[kw.arg] = ast.unparse(kw.value)
                except Exception:
                    pass

        return args if args else {}


def create_plugin() -> CeleryPlugin:
    """Factory function for plugin discovery."""
    return CeleryPlugin()
