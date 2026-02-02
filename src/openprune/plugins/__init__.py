"""Plugin registry and discovery for framework detection."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Iterator

from openprune.plugins.protocol import FrameworkPlugin


class PluginRegistry:
    """Registry for framework detection plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, FrameworkPlugin] = {}
        self._by_framework: dict[str, list[FrameworkPlugin]] = {}

    def register(self, plugin: FrameworkPlugin) -> None:
        """Register a plugin instance."""
        self._plugins[plugin.name] = plugin

        fw_type = plugin.framework_type
        if fw_type not in self._by_framework:
            self._by_framework[fw_type] = []
        self._by_framework[fw_type].append(plugin)

    def get(self, name: str) -> FrameworkPlugin | None:
        """Get a plugin by name."""
        return self._plugins.get(name)

    def get_by_framework(self, framework: str) -> list[FrameworkPlugin]:
        """Get all plugins for a framework type."""
        return self._by_framework.get(framework, [])

    def all_plugins(self) -> Iterator[FrameworkPlugin]:
        """Iterate over all registered plugins."""
        yield from self._plugins.values()

    def get_all_import_indicators(self) -> dict[str, str]:
        """Get combined import indicators from all plugins."""
        indicators: dict[str, str] = {}
        for plugin in self._plugins.values():
            for indicator in plugin.import_indicators:
                indicators[indicator] = plugin.framework_type
        return indicators

    def get_all_factory_functions(self) -> set[str]:
        """Get all factory function names from all plugins."""
        functions: set[str] = set()
        for plugin in self._plugins.values():
            functions.update(plugin.factory_functions)
        return functions

    def get_all_implicit_names(self) -> set[str]:
        """Get all implicit names from all plugins (for basic matching)."""
        names: set[str] = set()
        for plugin in self._plugins.values():
            for implicit in plugin.implicit_names:
                names.add(implicit.name)
        return names


# Global registry instance
_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """Get the global plugin registry, initializing if needed."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        _discover_builtin_plugins(_registry)
    return _registry


def reset_registry() -> None:
    """Reset the global registry. Useful for testing."""
    global _registry
    _registry = None


def _discover_builtin_plugins(registry: PluginRegistry) -> None:
    """Discover and register all builtin plugins."""
    from openprune.plugins import builtin

    for _, module_name, _ in pkgutil.iter_modules(builtin.__path__):
        module = importlib.import_module(f"openprune.plugins.builtin.{module_name}")

        # Look for a create_plugin() function or a Plugin class
        if hasattr(module, "create_plugin"):
            plugin = module.create_plugin()
            registry.register(plugin)
        elif hasattr(module, "Plugin"):
            plugin = module.Plugin()
            registry.register(plugin)
