"""Flask-RESTPlus and Flask-RESTX framework plugin.

Detects entrypoints in Flask-RESTPlus/RESTX applications:
- Resource subclasses with HTTP method handlers (get, post, put, delete, etc.)
- api.add_resource() registrations
- api.add_namespace() registrations
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


# HTTP methods that are implicit entrypoints on Resource subclasses
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}

# Resource base classes to detect
RESOURCE_BASES = {"Resource", "flask_restplus.Resource", "flask_restx.Resource"}


class FlaskRestPlusPlugin:
    """Plugin for Flask-RESTPlus and Flask-RESTX detection."""

    @property
    def name(self) -> str:
        return "flask-restplus"

    @property
    def framework_type(self) -> str:
        return "flask_restplus"

    @property
    def import_indicators(self) -> list[str]:
        return [
            "flask_restplus",
            "flask_restx",
            "flask_restful",
        ]

    @property
    def factory_functions(self) -> list[str]:
        return ["create_api", "make_api"]

    @property
    def implicit_names(self) -> list[ImplicitName]:
        """HTTP methods are implicit on Resource subclasses."""
        return [
            ImplicitName(
                name=method,
                context="Flask-RESTPlus Resource method",
                parent_classes=list(RESOURCE_BASES),
                score_adjustment=-40,
            )
            for method in HTTP_METHODS
        ]

    @property
    def decorator_scoring_rules(self) -> list[DecoratorScoringRule]:
        return [
            DecoratorScoringRule(
                pattern="api.expect",
                score_adjustment=-20,
                description="API input expectation decorator",
            ),
            DecoratorScoringRule(
                pattern="api.marshal",
                score_adjustment=-20,
                description="API output marshalling decorator",
            ),
            DecoratorScoringRule(
                pattern="api.doc",
                score_adjustment=-10,
                description="API documentation decorator",
            ),
            DecoratorScoringRule(
                pattern="ns.expect",
                score_adjustment=-20,
                description="Namespace input expectation decorator",
            ),
            DecoratorScoringRule(
                pattern="ns.marshal",
                score_adjustment=-20,
                description="Namespace output marshalling decorator",
            ),
        ]

    def detect_entrypoints(
        self,
        tree: ast.AST,
        file_path: Path,
    ) -> list[DetectedEntrypoint]:
        """Detect Flask-RESTPlus entrypoints in an AST."""
        visitor = _RestPlusVisitor(file_path)
        visitor.visit(tree)
        return visitor.entrypoints

    def is_implicit_name(
        self,
        name: str,
        parent_classes: list[str],
        decorators: list[str],
    ) -> bool:
        """Check if name is an HTTP method on a Resource subclass."""
        if name not in HTTP_METHODS:
            return False

        # Check if any parent class is a Resource
        for parent in parent_classes:
            # Direct match
            if parent in RESOURCE_BASES or parent.endswith(".Resource"):
                return True
            # Handle indirect inheritance: BaseResource, MyResource, etc.
            # Any class ending in "Resource" is likely a Resource subclass
            parent_lower = parent.lower()
            if parent_lower.endswith("resource") or "resource" in parent_lower:
                return True

        return False


class _RestPlusVisitor(ast.NodeVisitor):
    """AST visitor to find Flask-RESTPlus patterns."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.entrypoints: list[DetectedEntrypoint] = []
        self._resource_classes: dict[str, int] = {}  # class_name -> line
        self._api_resources: dict[str, str] = {}  # class_name -> route

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Detect Resource subclasses."""
        for base in node.bases:
            base_name = self._get_base_name(base)
            if base_name in RESOURCE_BASES:
                self._resource_classes[node.name] = node.lineno
                self._register_resource_methods(node)
                break

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Detect api.add_resource() and api.add_namespace() calls."""
        if self._is_add_resource_call(node):
            self._parse_add_resource(node)
        elif self._is_add_namespace_call(node):
            self._parse_add_namespace(node)

        self.generic_visit(node)

    def _is_add_resource_call(self, node: ast.Call) -> bool:
        """Check if this is an api.add_resource() call."""
        match node.func:
            case ast.Attribute(attr="add_resource"):
                return True
        return False

    def _is_add_namespace_call(self, node: ast.Call) -> bool:
        """Check if this is an api.add_namespace() call."""
        match node.func:
            case ast.Attribute(attr="add_namespace"):
                return True
        return False

    def _parse_add_resource(self, node: ast.Call) -> None:
        """Parse api.add_resource(ClassName, "/route") call."""
        if len(node.args) >= 2:
            # First arg is the class
            class_arg = node.args[0]
            if isinstance(class_arg, ast.Name):
                class_name = class_arg.id

                # Second arg is the route
                route_arg = node.args[1]
                route = ""
                if isinstance(route_arg, ast.Constant):
                    route = str(route_arg.value)

                self._api_resources[class_name] = route

                # Register the class itself as an entrypoint
                self.entrypoints.append(
                    DetectedEntrypoint(
                        name=class_name,
                        type=EntrypointType.FLASK_ROUTE,
                        line=node.lineno,
                        file=self.file_path,
                        arguments={"route": route} if route else {},
                    )
                )

    def _parse_add_namespace(self, node: ast.Call) -> None:
        """Parse api.add_namespace(ns, path="/prefix") call."""
        if node.args:
            ns_arg = node.args[0]
            if isinstance(ns_arg, ast.Name):
                ns_name = ns_arg.id

                # Get path from kwargs or second positional arg
                path = ""
                for kw in node.keywords:
                    if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                        path = str(kw.value.value)
                        break

                if not path and len(node.args) >= 2:
                    if isinstance(node.args[1], ast.Constant):
                        path = str(node.args[1].value)

                self.entrypoints.append(
                    DetectedEntrypoint(
                        name=ns_name,
                        type=EntrypointType.FLASK_BLUEPRINT,
                        line=node.lineno,
                        file=self.file_path,
                        arguments={"path": path} if path else {},
                    )
                )

    def _register_resource_methods(self, class_node: ast.ClassDef) -> None:
        """Register HTTP method implementations as entrypoints."""
        for item in class_node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name in HTTP_METHODS:
                    self.entrypoints.append(
                        DetectedEntrypoint(
                            name=item.name,
                            type=EntrypointType.FLASK_ROUTE,
                            line=item.lineno,
                            file=self.file_path,
                            parent_class=class_node.name,
                        )
                    )

    def _get_base_name(self, node: ast.expr) -> str:
        """Get the name of a base class."""
        match node:
            case ast.Name(id=name):
                return name
            case ast.Attribute():
                # Get full dotted name
                parts: list[str] = []
                current: ast.expr = node
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                return ".".join(reversed(parts))
        return ""


def create_plugin() -> FlaskRestPlusPlugin:
    """Factory function for plugin discovery."""
    return FlaskRestPlusPlugin()
