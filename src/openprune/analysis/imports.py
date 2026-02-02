"""Import graph building and resolution."""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from openprune.analysis.visitor import FileAnalysisResult
from openprune.models.dependency import ImportInfo, ModuleInfo


@dataclass
class ImportGraph:
    """Graph of module dependencies."""

    # module_name -> ModuleInfo
    modules: dict[str, ModuleInfo] = field(default_factory=dict)

    # module_name -> list of imported module names
    edges: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    # module_name -> list of modules that import it
    reverse_edges: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add_module(self, name: str, path: Path | None, is_package: bool = False) -> None:
        """Add a module to the graph."""
        self.modules[name] = ModuleInfo(
            name=name,
            path=path,
            is_package=is_package,
            is_external=path is None,
        )

    def add_edge(self, from_module: str, to_module: str) -> None:
        """Add an import edge from one module to another."""
        if to_module not in self.edges[from_module]:
            self.edges[from_module].append(to_module)
            self.reverse_edges[to_module].append(from_module)

    def get_orphaned_modules(self, entrypoints: list[str]) -> list[str]:
        """Find modules not reachable from any entrypoint."""
        reachable: set[str] = set()

        def visit(module: str) -> None:
            if module in reachable:
                return
            reachable.add(module)
            for imported in self.edges.get(module, []):
                if imported in self.modules:
                    visit(imported)

        for ep in entrypoints:
            visit(ep)

        all_internal = {name for name, info in self.modules.items() if not info.is_external}

        return list(all_internal - reachable)

    def get_import_chain(self, module: str) -> list[str]:
        """Get the chain of modules that import the given module."""
        chain: list[str] = []
        visited: set[str] = set()

        def visit(m: str) -> None:
            if m in visited:
                return
            visited.add(m)
            chain.append(m)
            for importer in self.reverse_edges.get(m, []):
                visit(importer)

        visit(module)
        return chain

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return {
            "modules": {
                name: {
                    "path": str(info.path) if info.path else None,
                    "imports": self.edges.get(name, []),
                    "imported_by": self.reverse_edges.get(name, []),
                    "is_entrypoint": False,  # Will be set by caller
                }
                for name, info in self.modules.items()
                if not info.is_external
            },
            "orphaned_modules": [],  # Will be set by caller
        }


class ImportResolver:
    """Resolves import statements to actual module paths."""

    def __init__(self, project_root: Path, src_dirs: list[Path] | None = None) -> None:
        self.project_root = project_root
        self.src_dirs = src_dirs or [project_root]

        # Cache of module name -> path
        self._cache: dict[str, Path | None] = {}

        # Standard library modules (Python 3.10+)
        self._stdlib = set(sys.stdlib_module_names)

        # Common third-party packages that should be treated as external
        self._common_external = {
            "flask",
            "celery",
            "django",
            "fastapi",
            "sqlalchemy",
            "requests",
            "pytest",
            "numpy",
            "pandas",
            "redis",
            "boto3",
            "pydantic",
            "typer",
            "click",
            "rich",
        }

    def resolve(self, module_name: str, from_file: Path | None = None) -> Path | None:
        """Resolve a module name to its file path."""
        if module_name in self._cache:
            return self._cache[module_name]

        # Skip stdlib and common external packages
        if self._is_external(module_name):
            self._cache[module_name] = None
            return None

        # Try to find in source directories
        path = self._find_module(module_name)
        self._cache[module_name] = path
        return path

    def _is_external(self, module_name: str) -> bool:
        """Check if module is external (stdlib or third-party)."""
        top_level = module_name.split(".")[0]
        return top_level in self._stdlib or top_level in self._common_external

    def _find_module(self, module_name: str) -> Path | None:
        """Find a module in the source directories."""
        parts = module_name.split(".")

        for src_dir in self.src_dirs:
            # Try as a package (directory with __init__.py)
            package_path = src_dir / Path(*parts) / "__init__.py"
            if package_path.exists():
                return package_path

            # Try as a module (.py file)
            if len(parts) > 1:
                module_path = src_dir / Path(*parts[:-1]) / f"{parts[-1]}.py"
                if module_path.exists():
                    return module_path

            # Try without subdirectory for top-level
            direct_path = src_dir / f"{parts[0]}.py"
            if direct_path.exists():
                return direct_path

            # Try as top-level package
            if len(parts) == 1:
                pkg_path = src_dir / parts[0] / "__init__.py"
                if pkg_path.exists():
                    return pkg_path

        return None

    def build_graph(
        self,
        files: list[Path],
        file_results: dict[Path, FileAnalysisResult] | None = None,
    ) -> ImportGraph:
        """Build an import graph from a list of files and their analysis results.

        Args:
            files: List of Python file paths to include in the graph
            file_results: Optional mapping of file paths to their analysis results.
                         If provided, import edges will be added to the graph.
        """
        graph = ImportGraph()

        # First pass: add all modules
        for file in files:
            module_name = self._path_to_module(file)
            if module_name:
                is_package = file.name == "__init__.py"
                graph.add_module(module_name, file, is_package)

        # Second pass: add import edges
        if file_results:
            known_modules = set(graph.modules.keys())
            for py_file, result in file_results.items():
                from_module = self._path_to_module(py_file)
                if not from_module:
                    continue

                for imp in result.imports:
                    to_module = self._resolve_import_to_module(
                        imp, py_file, known_modules
                    )
                    if to_module and to_module in graph.modules:
                        graph.add_edge(from_module, to_module)

        return graph

    def _resolve_import_to_module(
        self,
        imp: ImportInfo,
        from_file: Path,
        known_modules: Iterable[str],
    ) -> str | None:
        """Resolve an ImportInfo to a known internal module name.

        Args:
            imp: The import info to resolve
            from_file: The file containing the import statement
            known_modules: Set of known internal module names

        Returns:
            The resolved module name if it's a known internal module, None otherwise
        """
        if not imp.module:
            return None

        # Handle relative imports
        if imp.is_relative and imp.level > 0:
            base_module = self._path_to_module(from_file)
            if not base_module:
                return None

            # For relative imports, calculate the base package
            # level=1 means current package, level=2 means parent, etc.
            parts = base_module.split(".")

            # For a file like src/foo/bar.py (module: src.foo.bar),
            # a relative import "from . import X" should resolve relative to src.foo
            # (the parent directory of bar.py)
            if len(parts) >= imp.level:
                base_parts = parts[: -imp.level] if imp.level <= len(parts) else []
                if imp.module:
                    full_module = (
                        ".".join(base_parts + [imp.module])
                        if base_parts
                        else imp.module
                    )
                else:
                    full_module = ".".join(base_parts) if base_parts else ""
            else:
                return None
        else:
            full_module = imp.module

        if not full_module:
            return None

        # Skip external modules early
        if self._is_external(full_module):
            return None

        # Check if the full module or any of its parents exist in known modules
        # This handles cases like "from utils.helpers import X"
        # where we want to mark "utils.helpers" as imported
        known_set = set(known_modules)
        parts = full_module.split(".")
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in known_set:
                return candidate

        return None

    def _path_to_module(self, file: Path) -> str | None:
        """Convert a file path to a module name."""
        try:
            # Try to make path relative to each src dir
            for src_dir in self.src_dirs:
                try:
                    rel_path = file.relative_to(src_dir)
                    parts = list(rel_path.parts)

                    # Remove .py extension
                    if parts and parts[-1].endswith(".py"):
                        parts[-1] = parts[-1][:-3]

                    # Remove __init__ suffix
                    if parts and parts[-1] == "__init__":
                        parts = parts[:-1]

                    if parts:
                        return ".".join(parts)
                except ValueError:
                    continue

            # Fall back to relative to project root
            rel_path = file.relative_to(self.project_root)
            parts = list(rel_path.parts)
            if parts and parts[-1].endswith(".py"):
                parts[-1] = parts[-1][:-3]
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts) if parts else None
        except ValueError:
            return None
