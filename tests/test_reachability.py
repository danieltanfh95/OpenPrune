"""Tests for reachability analysis functionality."""

import pytest
from pathlib import Path
from openprune.models.dependency import Symbol, SymbolType, Location, Usage, UsageContext
from openprune.analysis.visitor import FileAnalysisResult


def make_symbol(name: str, file: str = "test.py", decorators: list[str] | None = None) -> Symbol:
    """Helper to create a symbol."""
    return Symbol(
        name=name,
        qualified_name=f"{Path(file).stem}.{name}",
        type=SymbolType.FUNCTION,
        location=Location(file=Path(file), line=1, column=0),
        scope="module",
        decorators=decorators or [],
    )


def make_usage(symbol_name: str, caller: str | None = None) -> Usage:
    """Helper to create a usage."""
    return Usage(
        symbol_name=symbol_name,
        context=UsageContext.CALL,
        location=Location(file=Path("test.py"), line=10, column=0),
        caller=caller,
    )


# Copy functions from cli.py for isolated testing
def _build_call_graph(
    all_definitions: dict[str, Symbol],
    all_usages: list[Usage],
) -> dict[str, set[str]]:
    """Build graph of caller -> callees (qualified names)."""
    graph: dict[str, set[str]] = {qname: set() for qname in all_definitions}

    for usage in all_usages:
        if usage.caller and usage.caller in graph:
            # Try to resolve the usage to a known definition
            for qname in all_definitions:
                if qname.endswith(f".{usage.symbol_name}"):
                    graph[usage.caller].add(qname)
                    break

    return graph


def _find_reachable_symbols(
    entrypoint_qnames: set[str],
    call_graph: dict[str, set[str]],
) -> set[str]:
    """Find all symbols reachable from entrypoints via call graph."""
    reachable = set(entrypoint_qnames)
    to_visit = list(entrypoint_qnames)

    while to_visit:
        current = to_visit.pop()
        for callee in call_graph.get(current, set()):
            if callee not in reachable:
                reachable.add(callee)
                to_visit.append(callee)

    return reachable


def _find_reachable_modules(
    entrypoint_files: set[Path],
    file_results: dict[Path, FileAnalysisResult],
) -> set[str]:
    """Find all modules reachable via imports from entrypoint files."""
    from openprune.models.dependency import ImportInfo

    reachable = {f.stem for f in entrypoint_files}
    to_visit = list(reachable)

    # Build module import graph
    module_imports: dict[str, set[str]] = {}
    for py_file, result in file_results.items():
        module_imports[py_file.stem] = {
            imp.module.split(".")[0]  # Get top-level module
            for imp in result.imports
            if imp.module
        }

    while to_visit:
        current = to_visit.pop()
        for imported in module_imports.get(current, set()):
            if imported not in reachable:
                reachable.add(imported)
                to_visit.append(imported)

    return reachable


class TestBuildCallGraph:
    """Tests for call graph construction."""

    def test_empty_definitions(self):
        """Test with no definitions."""
        graph = _build_call_graph({}, [])
        assert graph == {}

    def test_no_usages(self):
        """Test definitions with no usages."""
        definitions = {
            "module.func1": make_symbol("func1"),
            "module.func2": make_symbol("func2"),
        }
        graph = _build_call_graph(definitions, [])
        assert graph == {"module.func1": set(), "module.func2": set()}

    def test_simple_call(self):
        """Test simple function call relationship."""
        definitions = {
            "module.caller": make_symbol("caller"),
            "module.callee": make_symbol("callee"),
        }
        usages = [make_usage("callee", caller="module.caller")]
        graph = _build_call_graph(definitions, usages)

        assert "module.callee" in graph["module.caller"]
        assert graph["module.callee"] == set()

    def test_chain_of_calls(self):
        """Test chain: a -> b -> c."""
        definitions = {
            "module.a": make_symbol("a"),
            "module.b": make_symbol("b"),
            "module.c": make_symbol("c"),
        }
        usages = [
            make_usage("b", caller="module.a"),
            make_usage("c", caller="module.b"),
        ]
        graph = _build_call_graph(definitions, usages)

        assert graph["module.a"] == {"module.b"}
        assert graph["module.b"] == {"module.c"}
        assert graph["module.c"] == set()

    def test_multiple_callees(self):
        """Test function calling multiple other functions."""
        definitions = {
            "module.main": make_symbol("main"),
            "module.helper1": make_symbol("helper1"),
            "module.helper2": make_symbol("helper2"),
        }
        usages = [
            make_usage("helper1", caller="module.main"),
            make_usage("helper2", caller="module.main"),
        ]
        graph = _build_call_graph(definitions, usages)

        assert graph["module.main"] == {"module.helper1", "module.helper2"}

    def test_usage_without_caller(self):
        """Test module-level usage (no caller)."""
        definitions = {
            "module.func": make_symbol("func"),
        }
        usages = [make_usage("func", caller=None)]
        graph = _build_call_graph(definitions, usages)

        # No edges should be added for module-level usages
        assert graph == {"module.func": set()}

    def test_unknown_caller(self):
        """Test usage from unknown caller."""
        definitions = {
            "module.func": make_symbol("func"),
        }
        usages = [make_usage("func", caller="unknown.caller")]
        graph = _build_call_graph(definitions, usages)

        # Unknown caller should be ignored
        assert graph == {"module.func": set()}


class TestFindReachableSymbols:
    """Tests for reachability traversal."""

    def test_empty_entrypoints(self):
        """Test with no entrypoints."""
        reachable = _find_reachable_symbols(set(), {})
        assert reachable == set()

    def test_single_entrypoint_no_calls(self):
        """Test single entrypoint that calls nothing."""
        call_graph = {"module.entry": set()}
        reachable = _find_reachable_symbols({"module.entry"}, call_graph)
        assert reachable == {"module.entry"}

    def test_direct_call(self):
        """Test entrypoint directly calling one function."""
        call_graph = {
            "module.entry": {"module.helper"},
            "module.helper": set(),
        }
        reachable = _find_reachable_symbols({"module.entry"}, call_graph)
        assert reachable == {"module.entry", "module.helper"}

    def test_transitive_reachability(self):
        """Test transitive reachability: entry -> a -> b -> c."""
        call_graph = {
            "module.entry": {"module.a"},
            "module.a": {"module.b"},
            "module.b": {"module.c"},
            "module.c": set(),
            "module.orphan": set(),  # Not reachable
        }
        reachable = _find_reachable_symbols({"module.entry"}, call_graph)

        assert "module.entry" in reachable
        assert "module.a" in reachable
        assert "module.b" in reachable
        assert "module.c" in reachable
        assert "module.orphan" not in reachable

    def test_multiple_entrypoints(self):
        """Test multiple entrypoints."""
        call_graph = {
            "module.entry1": {"module.shared"},
            "module.entry2": {"module.helper2"},
            "module.shared": set(),
            "module.helper2": set(),
            "module.orphan": set(),
        }
        reachable = _find_reachable_symbols({"module.entry1", "module.entry2"}, call_graph)

        assert "module.entry1" in reachable
        assert "module.entry2" in reachable
        assert "module.shared" in reachable
        assert "module.helper2" in reachable
        assert "module.orphan" not in reachable

    def test_circular_calls(self):
        """Test circular call graph doesn't cause infinite loop."""
        call_graph = {
            "module.entry": {"module.a"},
            "module.a": {"module.b"},
            "module.b": {"module.a"},  # Circular back to a
        }
        reachable = _find_reachable_symbols({"module.entry"}, call_graph)

        # Should still complete without infinite loop
        assert reachable == {"module.entry", "module.a", "module.b"}

    def test_diamond_pattern(self):
        """Test diamond call pattern: entry -> a,b -> c."""
        call_graph = {
            "module.entry": {"module.a", "module.b"},
            "module.a": {"module.c"},
            "module.b": {"module.c"},
            "module.c": set(),
        }
        reachable = _find_reachable_symbols({"module.entry"}, call_graph)
        assert reachable == {"module.entry", "module.a", "module.b", "module.c"}


class TestCallerTracking:
    """Tests for caller field in Usage."""

    def test_usage_has_caller(self):
        """Test that Usage correctly stores caller."""
        usage = make_usage("target", caller="module.caller_func")
        assert usage.caller == "module.caller_func"

    def test_usage_no_caller(self):
        """Test module-level usage has no caller."""
        usage = make_usage("target", caller=None)
        assert usage.caller is None


class TestIntegrationScenarios:
    """Integration-style tests for realistic scenarios."""

    def test_flask_route_pattern(self):
        """Test Flask route as entrypoint calling helpers."""
        # Simulate: index() -> helper() -> utils()
        definitions = {
            "app.index": make_symbol("index", decorators=["@app.route"]),
            "app.helper": make_symbol("helper"),
            "app.utils": make_symbol("utils"),
            "app.orphan": make_symbol("orphan"),  # Never called
        }

        usages = [
            make_usage("helper", caller="app.index"),
            make_usage("utils", caller="app.helper"),
        ]

        call_graph = _build_call_graph(definitions, usages)
        entrypoints = {"app.index"}
        reachable = _find_reachable_symbols(entrypoints, call_graph)

        assert "app.index" in reachable
        assert "app.helper" in reachable
        assert "app.utils" in reachable
        assert "app.orphan" not in reachable

    def test_celery_task_pattern(self):
        """Test Celery task as entrypoint."""
        definitions = {
            "tasks.send_email": make_symbol("send_email", decorators=["@celery.task"]),
            "tasks.format_email": make_symbol("format_email"),
            "tasks.old_sender": make_symbol("old_sender"),  # Unused
        }

        usages = [
            make_usage("format_email", caller="tasks.send_email"),
        ]

        call_graph = _build_call_graph(definitions, usages)
        entrypoints = {"tasks.send_email"}
        reachable = _find_reachable_symbols(entrypoints, call_graph)

        assert "tasks.send_email" in reachable
        assert "tasks.format_email" in reachable
        assert "tasks.old_sender" not in reachable
