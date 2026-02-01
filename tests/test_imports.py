"""Tests for the imports module."""

from pathlib import Path

import pytest

from openprune.analysis.imports import ImportGraph, ImportResolver


class TestImportGraph:
    """Tests for ImportGraph class."""

    def test_add_module(self):
        """Should add a module to the graph."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))

        assert "app" in graph.modules
        assert graph.modules["app"].name == "app"
        assert graph.modules["app"].path == Path("/src/app.py")
        assert graph.modules["app"].is_external is False

    def test_add_module_as_package(self):
        """Should mark module as package."""
        graph = ImportGraph()
        graph.add_module("utils", Path("/src/utils/__init__.py"), is_package=True)

        assert graph.modules["utils"].is_package is True

    def test_add_module_external(self):
        """Should mark module as external when path is None."""
        graph = ImportGraph()
        graph.add_module("flask", None)

        assert graph.modules["flask"].is_external is True

    def test_add_edge(self):
        """Should add import edge between modules."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))
        graph.add_module("utils", Path("/src/utils.py"))
        graph.add_edge("app", "utils")

        assert "utils" in graph.edges["app"]
        assert "app" in graph.reverse_edges["utils"]

    def test_add_edge_no_duplicates(self):
        """Should not add duplicate edges."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))
        graph.add_module("utils", Path("/src/utils.py"))
        graph.add_edge("app", "utils")
        graph.add_edge("app", "utils")

        assert graph.edges["app"].count("utils") == 1
        assert graph.reverse_edges["utils"].count("app") == 1

    def test_get_orphaned_modules_none(self):
        """Should return empty list when all modules are reachable."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))
        graph.add_module("utils", Path("/src/utils.py"))
        graph.add_edge("app", "utils")

        orphaned = graph.get_orphaned_modules(["app"])

        assert orphaned == []

    def test_get_orphaned_modules_with_orphan(self):
        """Should return modules not reachable from entrypoints."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))
        graph.add_module("utils", Path("/src/utils.py"))
        graph.add_module("deprecated", Path("/src/deprecated.py"))
        graph.add_edge("app", "utils")
        # deprecated is not imported by anyone

        orphaned = graph.get_orphaned_modules(["app"])

        assert "deprecated" in orphaned
        assert "app" not in orphaned
        assert "utils" not in orphaned

    def test_get_orphaned_modules_transitive(self):
        """Should handle transitive imports."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))
        graph.add_module("utils", Path("/src/utils.py"))
        graph.add_module("helpers", Path("/src/helpers.py"))
        graph.add_module("orphan", Path("/src/orphan.py"))
        graph.add_edge("app", "utils")
        graph.add_edge("utils", "helpers")

        orphaned = graph.get_orphaned_modules(["app"])

        assert "orphan" in orphaned
        assert "helpers" not in orphaned

    def test_get_orphaned_modules_excludes_external(self):
        """Should not include external modules in orphaned list."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))
        graph.add_module("flask", None)  # External

        orphaned = graph.get_orphaned_modules(["app"])

        assert "flask" not in orphaned

    def test_get_import_chain(self):
        """Should return chain of modules that import the given module."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))
        graph.add_module("utils", Path("/src/utils.py"))
        graph.add_module("helpers", Path("/src/helpers.py"))
        graph.add_edge("app", "utils")
        graph.add_edge("utils", "helpers")

        chain = graph.get_import_chain("helpers")

        assert "helpers" in chain
        assert "utils" in chain

    def test_get_import_chain_single_module(self):
        """Should return just the module if nothing imports it."""
        graph = ImportGraph()
        graph.add_module("orphan", Path("/src/orphan.py"))

        chain = graph.get_import_chain("orphan")

        assert chain == ["orphan"]

    def test_to_dict(self):
        """Should convert graph to dictionary for JSON serialization."""
        graph = ImportGraph()
        graph.add_module("app", Path("/src/app.py"))
        graph.add_module("utils", Path("/src/utils.py"))
        graph.add_module("flask", None)  # External, should be excluded
        graph.add_edge("app", "utils")

        result = graph.to_dict()

        assert "modules" in result
        assert "app" in result["modules"]
        assert "utils" in result["modules"]
        assert "flask" not in result["modules"]  # External excluded
        assert result["modules"]["app"]["imports"] == ["utils"]
        assert result["modules"]["utils"]["imported_by"] == ["app"]


class TestImportResolver:
    """Tests for ImportResolver class."""

    def test_resolve_stdlib_module(self, tmp_path: Path):
        """Should return None for stdlib modules."""
        resolver = ImportResolver(tmp_path)

        result = resolver.resolve("os")

        assert result is None

    def test_resolve_common_external(self, tmp_path: Path):
        """Should return None for common third-party packages."""
        resolver = ImportResolver(tmp_path)

        assert resolver.resolve("flask") is None
        assert resolver.resolve("celery") is None
        assert resolver.resolve("django") is None
        assert resolver.resolve("pytest") is None

    def test_resolve_top_level_module(self, tmp_path: Path):
        """Should resolve top-level module to .py file."""
        app_file = tmp_path / "app.py"
        app_file.write_text("# app")

        resolver = ImportResolver(tmp_path)
        result = resolver.resolve("app")

        assert result == app_file

    def test_resolve_package_module(self, tmp_path: Path):
        """Should resolve package module to __init__.py."""
        utils_dir = tmp_path / "utils"
        utils_dir.mkdir()
        init_file = utils_dir / "__init__.py"
        init_file.write_text("# utils")

        resolver = ImportResolver(tmp_path)
        result = resolver.resolve("utils")

        assert result == init_file

    def test_resolve_submodule(self, tmp_path: Path):
        """Should resolve submodule to .py file."""
        utils_dir = tmp_path / "utils"
        utils_dir.mkdir()
        (utils_dir / "__init__.py").write_text("")
        helpers_file = utils_dir / "helpers.py"
        helpers_file.write_text("# helpers")

        resolver = ImportResolver(tmp_path)
        result = resolver.resolve("utils.helpers")

        assert result == helpers_file

    def test_resolve_nonexistent_module(self, tmp_path: Path):
        """Should return None for nonexistent module."""
        resolver = ImportResolver(tmp_path)

        result = resolver.resolve("nonexistent")

        assert result is None

    def test_resolve_caching(self, tmp_path: Path):
        """Should cache resolution results."""
        app_file = tmp_path / "app.py"
        app_file.write_text("# app")

        resolver = ImportResolver(tmp_path)
        result1 = resolver.resolve("app")
        result2 = resolver.resolve("app")

        assert result1 is result2
        assert "app" in resolver._cache

    def test_build_graph(self, tmp_path: Path):
        """Should build import graph from file list."""
        app_file = tmp_path / "app.py"
        app_file.write_text("# app")
        utils_file = tmp_path / "utils.py"
        utils_file.write_text("# utils")

        resolver = ImportResolver(tmp_path)
        graph = resolver.build_graph([app_file, utils_file])

        assert "app" in graph.modules
        assert "utils" in graph.modules

    def test_build_graph_with_package(self, tmp_path: Path):
        """Should correctly identify packages by __init__.py."""
        pkg_dir = tmp_path / "mypackage"
        pkg_dir.mkdir()
        init_file = pkg_dir / "__init__.py"
        init_file.write_text("# package")

        resolver = ImportResolver(tmp_path)
        graph = resolver.build_graph([init_file])

        assert "mypackage" in graph.modules
        assert graph.modules["mypackage"].is_package is True

    def test_path_to_module_simple(self, tmp_path: Path):
        """Should convert simple file path to module name."""
        app_file = tmp_path / "app.py"

        resolver = ImportResolver(tmp_path)
        result = resolver._path_to_module(app_file)

        assert result == "app"

    def test_path_to_module_nested(self, tmp_path: Path):
        """Should convert nested file path to dotted module name."""
        utils_dir = tmp_path / "utils"
        utils_dir.mkdir()
        helpers_file = utils_dir / "helpers.py"

        resolver = ImportResolver(tmp_path)
        result = resolver._path_to_module(helpers_file)

        assert result == "utils.helpers"

    def test_path_to_module_init(self, tmp_path: Path):
        """Should remove __init__ from module name."""
        pkg_dir = tmp_path / "mypackage"
        pkg_dir.mkdir()
        init_file = pkg_dir / "__init__.py"

        resolver = ImportResolver(tmp_path)
        result = resolver._path_to_module(init_file)

        assert result == "mypackage"

    def test_multiple_src_dirs(self, tmp_path: Path):
        """Should search multiple source directories."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        app_file = src_dir / "app.py"
        app_file.write_text("# app")

        resolver = ImportResolver(tmp_path, src_dirs=[src_dir])
        result = resolver.resolve("app")

        assert result == app_file

    def test_is_external_stdlib(self, tmp_path: Path):
        """Should recognize stdlib modules as external."""
        resolver = ImportResolver(tmp_path)

        assert resolver._is_external("os") is True
        assert resolver._is_external("sys") is True
        assert resolver._is_external("json") is True
        assert resolver._is_external("pathlib") is True

    def test_is_external_common_packages(self, tmp_path: Path):
        """Should recognize common third-party packages as external."""
        resolver = ImportResolver(tmp_path)

        assert resolver._is_external("flask") is True
        assert resolver._is_external("celery") is True
        assert resolver._is_external("numpy") is True
        assert resolver._is_external("pandas") is True

    def test_is_external_submodule(self, tmp_path: Path):
        """Should check top-level module for external detection."""
        resolver = ImportResolver(tmp_path)

        assert resolver._is_external("flask.app") is True
        assert resolver._is_external("os.path") is True


class TestImportGraphCircularDependencies:
    """Tests for handling circular dependencies."""

    def test_get_orphaned_with_circular(self):
        """Should handle circular dependencies without infinite loop."""
        graph = ImportGraph()
        graph.add_module("a", Path("/src/a.py"))
        graph.add_module("b", Path("/src/b.py"))
        graph.add_module("c", Path("/src/c.py"))
        graph.add_module("orphan", Path("/src/orphan.py"))

        # Create circular: a -> b -> c -> a
        graph.add_edge("a", "b")
        graph.add_edge("b", "c")
        graph.add_edge("c", "a")

        orphaned = graph.get_orphaned_modules(["a"])

        # Should complete without hanging
        assert "orphan" in orphaned
        assert "a" not in orphaned
        assert "b" not in orphaned
        assert "c" not in orphaned

    def test_get_import_chain_with_circular(self):
        """Should handle circular imports in chain traversal."""
        graph = ImportGraph()
        graph.add_module("a", Path("/src/a.py"))
        graph.add_module("b", Path("/src/b.py"))

        # Create mutual import
        graph.add_edge("a", "b")
        graph.add_edge("b", "a")

        chain = graph.get_import_chain("a")

        # Should complete without hanging
        assert "a" in chain
        assert "b" in chain


class TestImportGraphMultipleEntrypoints:
    """Tests for multiple entrypoints."""

    def test_multiple_entrypoints(self):
        """Should handle multiple entrypoints correctly."""
        graph = ImportGraph()
        graph.add_module("web_app", Path("/src/web_app.py"))
        graph.add_module("worker", Path("/src/worker.py"))
        graph.add_module("shared", Path("/src/shared.py"))
        graph.add_module("orphan", Path("/src/orphan.py"))

        graph.add_edge("web_app", "shared")
        graph.add_edge("worker", "shared")

        orphaned = graph.get_orphaned_modules(["web_app", "worker"])

        assert "orphan" in orphaned
        assert "shared" not in orphaned
        assert "web_app" not in orphaned
        assert "worker" not in orphaned

    def test_no_entrypoints(self):
        """Should mark all modules as orphaned when no entrypoints."""
        graph = ImportGraph()
        graph.add_module("a", Path("/src/a.py"))
        graph.add_module("b", Path("/src/b.py"))

        orphaned = graph.get_orphaned_modules([])

        assert "a" in orphaned
        assert "b" in orphaned
