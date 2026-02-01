"""Flask framework plugin.

Detects entrypoints in Flask applications:
- @app.route() and @bp.route() decorators
- HTTP method decorators (@app.get, @app.post, etc.)
- Hooks (@before_request, @after_request, etc.)
- Error handlers (@app.errorhandler)
- CLI commands (@app.cli.command)
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


# Decorator patterns that indicate Flask entrypoints
# Maps (object, attribute) tuples to EntrypointType
DECORATOR_PATTERNS: dict[tuple[str, ...], EntrypointType] = {
    # Flask routes
    ("app", "route"): EntrypointType.FLASK_ROUTE,
    ("bp", "route"): EntrypointType.FLASK_BLUEPRINT,
    ("blueprint", "route"): EntrypointType.FLASK_BLUEPRINT,
    # Flask HTTP methods (Flask 2.0+)
    ("app", "get"): EntrypointType.FLASK_ROUTE,
    ("app", "post"): EntrypointType.FLASK_ROUTE,
    ("app", "put"): EntrypointType.FLASK_ROUTE,
    ("app", "delete"): EntrypointType.FLASK_ROUTE,
    ("app", "patch"): EntrypointType.FLASK_ROUTE,
    ("bp", "get"): EntrypointType.FLASK_BLUEPRINT,
    ("bp", "post"): EntrypointType.FLASK_BLUEPRINT,
    # Flask hooks
    ("app", "before_request"): EntrypointType.FLASK_HOOK,
    ("app", "after_request"): EntrypointType.FLASK_HOOK,
    ("app", "teardown_request"): EntrypointType.FLASK_HOOK,
    ("app", "before_first_request"): EntrypointType.FLASK_HOOK,
    ("bp", "before_request"): EntrypointType.FLASK_HOOK,
    ("bp", "after_request"): EntrypointType.FLASK_HOOK,
    ("blueprint", "before_request"): EntrypointType.FLASK_HOOK,
    ("blueprint", "after_request"): EntrypointType.FLASK_HOOK,
    # Flask error handlers
    ("app", "errorhandler"): EntrypointType.FLASK_ERRORHANDLER,
    ("bp", "errorhandler"): EntrypointType.FLASK_ERRORHANDLER,
    # Flask context processor
    ("app", "context_processor"): EntrypointType.FLASK_HOOK,
    ("app", "shell_context_processor"): EntrypointType.FLASK_HOOK,
}

# Factory function names that create Flask apps
FACTORY_FUNCTIONS = {"create_app", "make_app", "app_factory"}


class FlaskPlugin:
    """Plugin for Flask applications."""

    @property
    def name(self) -> str:
        return "flask"

    @property
    def framework_type(self) -> FrameworkType:
        return FrameworkType.FLASK

    @property
    def import_indicators(self) -> list[str]:
        return ["flask", "Flask"]

    @property
    def factory_functions(self) -> list[str]:
        return list(FACTORY_FUNCTIONS)

    @property
    def implicit_names(self) -> list[ImplicitName]:
        """Flask has no implicit method names like RESTPlus."""
        return []

    @property
    def decorator_scoring_rules(self) -> list[DecoratorScoringRule]:
        return [
            DecoratorScoringRule(
                pattern="route",
                score_adjustment=-40,
                description="Flask route decorator",
            ),
            DecoratorScoringRule(
                pattern="before_request",
                score_adjustment=-40,
                description="Flask before_request hook",
            ),
            DecoratorScoringRule(
                pattern="after_request",
                score_adjustment=-40,
                description="Flask after_request hook",
            ),
            DecoratorScoringRule(
                pattern="errorhandler",
                score_adjustment=-40,
                description="Flask error handler",
            ),
        ]

    def detect_entrypoints(
        self,
        tree: ast.AST,
        file_path: Path,
    ) -> list[DetectedEntrypoint]:
        """Detect Flask entrypoints in an AST."""
        visitor = _FlaskVisitor(file_path)
        visitor.visit(tree)
        return visitor.entrypoints

    def is_implicit_name(
        self,
        name: str,
        parent_classes: list[str],
        decorators: list[str],
    ) -> bool:
        """Flask doesn't have implicit method names."""
        return False


class _FlaskVisitor(ast.NodeVisitor):
    """AST visitor to find Flask entrypoint patterns."""

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

    def visit_If(self, node: ast.If) -> None:
        # Detect if __name__ == "__main__"
        if self._is_main_block(node):
            self.entrypoints.append(
                DetectedEntrypoint(
                    name="__main__",
                    type=EntrypointType.MAIN_BLOCK,
                    line=node.lineno,
                    file=self.file_path,
                )
            )
        self.generic_visit(node)

    def _parse_decorator(
        self, decorator: ast.expr, func: ast.FunctionDef
    ) -> DetectedEntrypoint | None:
        """Parse a decorator and check if it's a Flask entrypoint pattern."""
        match decorator:
            # @app.route("/path") - Call with attribute
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
                # Also check for patterns like @app.cli.command
                return self._check_nested_decorator(decorator, func, obj)

            # @app.route without call - Attribute only
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

        return None

    def _check_nested_decorator(
        self, decorator: ast.Call, func: ast.FunctionDef, base_obj: str
    ) -> DetectedEntrypoint | None:
        """Check for nested decorator patterns like @app.cli.command()."""
        dec_func = decorator.func
        if not isinstance(dec_func, ast.Attribute):
            return None

        # Build the full decorator path
        parts = self._get_attribute_parts(dec_func)
        if len(parts) >= 2:
            # Check last two parts as pattern
            pattern = (parts[-2], parts[-1])
            if pattern in DECORATOR_PATTERNS:
                return DetectedEntrypoint(
                    name=func.name,
                    type=DECORATOR_PATTERNS[pattern],
                    line=func.lineno,
                    file=self.file_path,
                    decorator=f"@{'.'.join(parts)}",
                    arguments=self._extract_decorator_args(decorator),
                )

            # Check for CLI commands
            if "cli" in parts and "command" in parts:
                return DetectedEntrypoint(
                    name=func.name,
                    type=EntrypointType.FLASK_CLI,
                    line=func.lineno,
                    file=self.file_path,
                    decorator=f"@{'.'.join(parts)}",
                    arguments=self._extract_decorator_args(decorator),
                )

        return None

    def _get_attribute_parts(self, node: ast.expr) -> list[str]:
        """Get all parts of a nested attribute access."""
        parts: list[str] = []
        current = node

        while isinstance(current, ast.Attribute):
            parts.insert(0, current.attr)
            current = current.value

        if isinstance(current, ast.Name):
            parts.insert(0, current.id)

        return parts

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
                    elif isinstance(kw.value, ast.List):
                        args[kw.arg] = [
                            elt.value if isinstance(elt, ast.Constant) else ast.unparse(elt)
                            for elt in kw.value.elts
                        ]
                    else:
                        args[kw.arg] = ast.unparse(kw.value)
                except Exception:
                    pass

        return args if args else {}

    def _is_main_block(self, node: ast.If) -> bool:
        """Check if this is 'if __name__ == "__main__"'."""
        match node.test:
            case ast.Compare(
                left=ast.Name(id="__name__"),
                ops=[ast.Eq()],
                comparators=[ast.Constant(value="__main__")],
            ):
                return True
        return False


def create_plugin() -> FlaskPlugin:
    """Factory function for plugin discovery."""
    return FlaskPlugin()
