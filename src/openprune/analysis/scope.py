"""Scope tracking utilities for dead code analysis."""

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class NestedScope:
    """
    Tracks which names are defined and used in each scope.
    Based on deadcode's approach but simplified.
    """

    # scope_name -> set of defined names
    definitions: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    # scope_name -> set of used names
    usages: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    # name -> list of scopes where it's defined
    name_to_scopes: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def define(self, name: str, scope: str) -> None:
        """Record a name definition in a scope."""
        self.definitions[scope].add(name)
        self.name_to_scopes[name].append(scope)

    def use(self, name: str, scope: str) -> None:
        """Record a name usage in a scope."""
        self.usages[scope].add(name)

    def is_used_in_scope(self, name: str, scope: str) -> bool:
        """Check if a name is used in a specific scope."""
        return name in self.usages[scope]

    def is_used_anywhere(self, name: str) -> bool:
        """Check if a name is used in any scope."""
        return any(name in uses for uses in self.usages.values())

    def get_unused_in_scope(self, scope: str) -> set[str]:
        """Get names defined but not used in a scope."""
        defined = self.definitions[scope]

        # Find names that are not used anywhere
        unused = set()
        for name in defined:
            if not self.is_used_anywhere(name):
                unused.add(name)

        return unused

    def resolve_name(self, name: str, current_scope: str) -> str | None:
        """
        Resolve a name to its defining scope.
        Follows Python's LEGB rule (Local, Enclosing, Global, Built-in).
        """
        scopes = self.name_to_scopes.get(name, [])

        if not scopes:
            return None  # Built-in or undefined

        # Check if defined in current scope
        if current_scope in scopes:
            return current_scope

        # Check enclosing scopes
        scope_parts = current_scope.split(".")
        for i in range(len(scope_parts) - 1, -1, -1):
            enclosing = ".".join(scope_parts[:i]) if i > 0 else ""
            if enclosing in scopes:
                return enclosing

        # Check module scope
        if "" in scopes:
            return ""

        return scopes[0] if scopes else None

    def get_all_unused(self) -> dict[str, set[str]]:
        """Get all unused names grouped by scope."""
        result: dict[str, set[str]] = {}
        for scope in self.definitions:
            unused = self.get_unused_in_scope(scope)
            if unused:
                result[scope] = unused
        return result
