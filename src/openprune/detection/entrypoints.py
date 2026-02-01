"""Entrypoint discovery via AST analysis and plugins."""

from __future__ import annotations

import ast
from pathlib import Path

from openprune.models.archetype import Entrypoint, EntrypointType
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


class EntrypointVisitor(ast.NodeVisitor):
    """AST visitor to find framework entrypoints.

    Note: This class is kept for backwards compatibility.
    New code should use detect_entrypoints() which uses plugins.
    """

    # Legacy patterns kept for compatibility
    DECORATOR_PATTERNS: dict[tuple[str, ...], EntrypointType] = {
        # Flask routes
        ("app", "route"): EntrypointType.FLASK_ROUTE,
        ("bp", "route"): EntrypointType.FLASK_BLUEPRINT,
        ("blueprint", "route"): EntrypointType.FLASK_BLUEPRINT,
        # Flask HTTP methods
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
        # Flask error handlers
        ("app", "errorhandler"): EntrypointType.FLASK_ERRORHANDLER,
        ("bp", "errorhandler"): EntrypointType.FLASK_ERRORHANDLER,
        # Celery tasks
        ("celery", "task"): EntrypointType.CELERY_TASK,
        ("app", "task"): EntrypointType.CELERY_TASK,
        ("shared_task",): EntrypointType.CELERY_SHARED_TASK,
        # Celery signals
        ("task_success", "connect"): EntrypointType.CELERY_SIGNAL,
        ("task_failure", "connect"): EntrypointType.CELERY_SIGNAL,
        ("task_prerun", "connect"): EntrypointType.CELERY_SIGNAL,
        ("task_postrun", "connect"): EntrypointType.CELERY_SIGNAL,
        # FastAPI
        ("router", "get"): EntrypointType.FASTAPI_ROUTE,
        ("router", "post"): EntrypointType.FASTAPI_ROUTE,
        ("router", "put"): EntrypointType.FASTAPI_ROUTE,
        ("router", "delete"): EntrypointType.FASTAPI_ROUTE,
        ("router", "patch"): EntrypointType.FASTAPI_ROUTE,
        # Click/Typer
        ("cli", "command"): EntrypointType.CLICK_COMMAND,
    }

    # Factory function names that are entrypoints
    FACTORY_FUNCTIONS = {"create_app", "make_app", "make_celery", "create_celery", "app_factory"}

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.entrypoints: list[Entrypoint] = []

    def detect_all(self, tree: ast.AST) -> list[Entrypoint]:
        """Detect entrypoints using plugins and legacy visitor.

        This method uses the plugin system for detection while
        maintaining backwards compatibility.
        """
        # Use plugin-based detection
        self.entrypoints = detect_entrypoints(tree, self.file_path)
        return self.entrypoints

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Check for factory functions
        if node.name in self.FACTORY_FUNCTIONS:
            self.entrypoints.append(
                Entrypoint(
                    type=EntrypointType.FACTORY_FUNCTION,
                    name=node.name,
                    file=self.file_path,
                    line=node.lineno,
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
                Entrypoint(
                    type=EntrypointType.MAIN_BLOCK,
                    name="__main__",
                    file=self.file_path,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    def _parse_decorator(
        self, decorator: ast.expr, func: ast.FunctionDef
    ) -> Entrypoint | None:
        """Parse a decorator and check if it's an entrypoint pattern."""
        match decorator:
            # @app.route("/path") - Call with attribute
            case ast.Call(func=ast.Attribute(value=ast.Name(id=obj), attr=attr)):
                pattern = (obj, attr)
                if pattern in self.DECORATOR_PATTERNS:
                    return Entrypoint(
                        type=self.DECORATOR_PATTERNS[pattern],
                        name=func.name,
                        file=self.file_path,
                        line=func.lineno,
                        decorator=f"@{obj}.{attr}",
                        arguments=self._extract_decorator_args(decorator),
                    )
                # Also check for patterns like @app.cli.command
                return self._check_nested_decorator(decorator, func, obj)

            # @app.route without call - Attribute only
            case ast.Attribute(value=ast.Name(id=obj), attr=attr):
                pattern = (obj, attr)
                if pattern in self.DECORATOR_PATTERNS:
                    return Entrypoint(
                        type=self.DECORATOR_PATTERNS[pattern],
                        name=func.name,
                        file=self.file_path,
                        line=func.lineno,
                        decorator=f"@{obj}.{attr}",
                    )

            # @shared_task() - Call with name
            case ast.Call(func=ast.Name(id=name)):
                if (name,) in self.DECORATOR_PATTERNS:
                    return Entrypoint(
                        type=self.DECORATOR_PATTERNS[(name,)],
                        name=func.name,
                        file=self.file_path,
                        line=func.lineno,
                        decorator=f"@{name}",
                        arguments=self._extract_decorator_args(decorator),
                    )

            # @shared_task - Name only
            case ast.Name(id=name):
                if (name,) in self.DECORATOR_PATTERNS:
                    return Entrypoint(
                        type=self.DECORATOR_PATTERNS[(name,)],
                        name=func.name,
                        file=self.file_path,
                        line=func.lineno,
                        decorator=f"@{name}",
                    )

        return None

    def _check_nested_decorator(
        self, decorator: ast.Call, func: ast.FunctionDef, base_obj: str
    ) -> Entrypoint | None:
        """Check for nested decorator patterns like @app.cli.command()."""
        dec_func = decorator.func
        if not isinstance(dec_func, ast.Attribute):
            return None

        # Build the full decorator path
        parts = self._get_attribute_parts(dec_func)
        if len(parts) >= 2:
            # Check last two parts as pattern
            pattern = (parts[-2], parts[-1])
            if pattern in self.DECORATOR_PATTERNS:
                return Entrypoint(
                    type=self.DECORATOR_PATTERNS[pattern],
                    name=func.name,
                    file=self.file_path,
                    line=func.lineno,
                    decorator=f"@{'.'.join(parts)}",
                    arguments=self._extract_decorator_args(decorator),
                )

            # Check for CLI commands
            if "cli" in parts and "command" in parts:
                return Entrypoint(
                    type=EntrypointType.FLASK_CLI,
                    name=func.name,
                    file=self.file_path,
                    line=func.lineno,
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
