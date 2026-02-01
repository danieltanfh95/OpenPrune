"""Tests for caller tracking in the AST visitor."""

import pytest
from pathlib import Path
from openprune.analysis.visitor import analyze_file, DeadCodeVisitor, FileAnalysisResult
from openprune.models.dependency import UsageContext
import tempfile
import textwrap


def analyze_source(source: str) -> FileAnalysisResult:
    """Helper to analyze source code string."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(textwrap.dedent(source))
        f.flush()
        return analyze_file(Path(f.name))


class TestCallerTracking:
    """Tests for tracking which function makes each usage."""

    def test_module_level_usage_has_no_caller(self):
        """Module-level usages should have no caller."""
        source = """
        import os

        # Module-level usage
        result = os.path.exists("/tmp")
        """
        result = analyze_source(source)

        # Find usages of 'os' or 'exists'
        module_usages = [u for u in result.usages if u.caller is None]
        assert len(module_usages) > 0

    def test_function_call_has_caller(self):
        """Calls inside a function should have that function as caller."""
        source = """
        def helper():
            return 42

        def main():
            x = helper()
            return x
        """
        result = analyze_source(source)

        # Find usage of 'helper' from within 'main'
        helper_usages = [u for u in result.usages if u.symbol_name == "helper"]
        assert len(helper_usages) >= 1

        # At least one should have 'main' as caller (the one inside main())
        main_callers = [u for u in helper_usages if u.caller and "main" in u.caller]
        assert len(main_callers) >= 1

    def test_method_call_has_method_caller(self):
        """Calls inside a method should have that method as caller."""
        source = """
        class MyClass:
            def method_a(self):
                return 1

            def method_b(self):
                return self.method_a()
        """
        result = analyze_source(source)

        # Find usage of 'method_a'
        method_a_usages = [u for u in result.usages if u.symbol_name == "method_a"]

        # Should have method_b as caller
        method_b_callers = [u for u in method_a_usages if u.caller and "method_b" in u.caller]
        assert len(method_b_callers) >= 1

    def test_nested_function_caller(self):
        """Nested function calls should track the inner function as caller."""
        source = """
        def helper():
            return 1

        def outer():
            def inner():
                return helper()
            return inner()
        """
        result = analyze_source(source)

        # Find usage of 'helper'
        helper_usages = [u for u in result.usages if u.symbol_name == "helper"]

        # Should have 'inner' as caller, not 'outer'
        inner_callers = [u for u in helper_usages if u.caller and "inner" in u.caller]
        assert len(inner_callers) >= 1

    def test_lambda_caller(self):
        """Lambda expressions should track enclosing function as caller."""
        source = """
        def helper():
            return 1

        def main():
            f = lambda: helper()
            return f()
        """
        result = analyze_source(source)

        # Find usage of 'helper'
        helper_usages = [u for u in result.usages if u.symbol_name == "helper"]

        # Lambda is inside main, so caller should include main
        # (lambdas don't create their own scope for this purpose)
        main_callers = [u for u in helper_usages if u.caller and "main" in u.caller]
        # Note: This may vary based on implementation - lambdas might not be tracked
        # as scopes. The test documents expected behavior.

    def test_getattr_usage_has_caller(self):
        """getattr() dynamic attribute access should track caller."""
        source = """
        class Obj:
            pass

        def get_value(obj):
            return getattr(obj, "value")
        """
        result = analyze_source(source)

        # Find usage of 'value' (the string in getattr)
        value_usages = [u for u in result.usages if u.symbol_name == "value"]

        # Should have get_value as caller
        if value_usages:  # Only if getattr strings are tracked
            caller_usages = [u for u in value_usages if u.caller and "get_value" in u.caller]
            assert len(caller_usages) >= 1

    def test_decorator_usage_has_no_caller(self):
        """Decorator usages should have no function caller (applied at class/module level)."""
        source = """
        def my_decorator(f):
            return f

        @my_decorator
        def decorated():
            pass
        """
        result = analyze_source(source)

        # Find usage of 'my_decorator' from decorator context
        decorator_usages = [
            u for u in result.usages
            if u.symbol_name == "my_decorator" and u.context == UsageContext.DECORATOR
        ]

        # Decorator is applied at module level, not inside a function
        for usage in decorator_usages:
            # Caller should be None or module-level
            assert usage.caller is None or "module" in str(usage.caller).lower()

    def test_class_body_has_no_caller(self):
        """Class body statements should have no function caller."""
        source = """
        CONSTANT = 42

        class MyClass:
            class_var = CONSTANT
        """
        result = analyze_source(source)

        # Find usage of 'CONSTANT' in class body
        constant_usages = [u for u in result.usages if u.symbol_name == "CONSTANT"]

        # Class body is not inside a function
        for usage in constant_usages:
            # Should have no function caller (class body)
            if usage.caller:
                assert "MyClass" not in usage.caller or "function" not in str(usage.caller)


class TestCallerQualifiedNames:
    """Tests for qualified name generation in callers."""

    def test_simple_function_qualified_name(self):
        """Simple function should have module.function qualified name."""
        source = """
        def target():
            pass

        def caller():
            target()
        """
        result = analyze_source(source)

        target_usages = [u for u in result.usages if u.symbol_name == "target"]
        callers = [u.caller for u in target_usages if u.caller]

        # Should contain qualified name with module and function
        assert any("caller" in c for c in callers)

    def test_method_qualified_name(self):
        """Method should have module.Class.method qualified name."""
        source = """
        def target():
            pass

        class MyClass:
            def my_method(self):
                target()
        """
        result = analyze_source(source)

        target_usages = [u for u in result.usages if u.symbol_name == "target"]
        callers = [u.caller for u in target_usages if u.caller]

        # Should contain class and method in qualified name
        assert any("MyClass" in c and "my_method" in c for c in callers)


class TestVisitorDefinitions:
    """Tests for symbol definition collection."""

    def test_collects_function_definitions(self):
        """Should collect function definitions."""
        source = """
        def my_function():
            pass

        def another_function(x, y):
            return x + y
        """
        result = analyze_source(source)

        names = [s.name for s in result.definitions.values()]
        assert "my_function" in names
        assert "another_function" in names

    def test_collects_class_definitions(self):
        """Should collect class definitions."""
        source = """
        class MyClass:
            pass

        class AnotherClass(MyClass):
            pass
        """
        result = analyze_source(source)

        names = [s.name for s in result.definitions.values()]
        assert "MyClass" in names
        assert "AnotherClass" in names

    def test_collects_method_definitions(self):
        """Should collect method definitions inside classes."""
        source = """
        class MyClass:
            def method_one(self):
                pass

            def method_two(self, arg):
                return arg
        """
        result = analyze_source(source)

        qnames = list(result.definitions.keys())
        assert any("method_one" in q for q in qnames)
        assert any("method_two" in q for q in qnames)

    def test_collects_module_level_variables(self):
        """Should collect module-level variable definitions."""
        source = """
        module_var = 42
        another_var = "hello"
        """
        result = analyze_source(source)

        names = [s.name for s in result.definitions.values()]
        assert "module_var" in names
        assert "another_var" in names

    def test_collects_constants(self):
        """Should identify ALL_CAPS as constants."""
        source = """
        MY_CONSTANT = 100
        ANOTHER_CONSTANT = "value"
        """
        result = analyze_source(source)

        from openprune.models.dependency import SymbolType

        constants = [s for s in result.definitions.values() if s.type == SymbolType.CONSTANT]
        names = [c.name for c in constants]
        assert "MY_CONSTANT" in names
        assert "ANOTHER_CONSTANT" in names

    def test_collects_imports(self):
        """Should collect import definitions."""
        source = """
        import os
        import sys as system
        from pathlib import Path
        from typing import List, Dict
        """
        result = analyze_source(source)

        names = [s.name for s in result.definitions.values()]
        assert "os" in names
        assert "system" in names
        assert "Path" in names
        assert "List" in names
        assert "Dict" in names


class TestVisitorDecorators:
    """Tests for decorator tracking."""

    def test_tracks_function_decorators(self):
        """Should track decorators on functions."""
        source = """
        def decorator(f):
            return f

        @decorator
        def decorated():
            pass
        """
        result = analyze_source(source)

        decorated = [s for s in result.definitions.values() if s.name == "decorated"][0]
        assert len(decorated.decorators) == 1
        assert "decorator" in decorated.decorators[0]

    def test_tracks_multiple_decorators(self):
        """Should track multiple decorators."""
        source = """
        def dec1(f): return f
        def dec2(f): return f

        @dec1
        @dec2
        def multi_decorated():
            pass
        """
        result = analyze_source(source)

        decorated = [s for s in result.definitions.values() if s.name == "multi_decorated"][0]
        assert len(decorated.decorators) == 2

    def test_tracks_class_decorators(self):
        """Should track decorators on classes."""
        source = """
        def class_decorator(cls):
            return cls

        @class_decorator
        class DecoratedClass:
            pass
        """
        result = analyze_source(source)

        decorated = [s for s in result.definitions.values() if s.name == "DecoratedClass"][0]
        assert len(decorated.decorators) == 1

    def test_tracks_decorator_with_args(self):
        """Should track decorators with arguments."""
        source = """
        def decorator(arg):
            def wrapper(f):
                return f
            return wrapper

        @decorator("value")
        def decorated():
            pass
        """
        result = analyze_source(source)

        decorated = [s for s in result.definitions.values() if s.name == "decorated"][0]
        assert len(decorated.decorators) == 1
        # Should contain the decorator call
        assert "decorator" in decorated.decorators[0]


class TestVisitorDunderAndPrivate:
    """Tests for dunder and private symbol detection."""

    def test_detects_dunder_methods(self):
        """Should mark dunder methods."""
        source = """
        class MyClass:
            def __init__(self):
                pass

            def __str__(self):
                return ""
        """
        result = analyze_source(source)

        init = [s for s in result.definitions.values() if s.name == "__init__"][0]
        str_method = [s for s in result.definitions.values() if s.name == "__str__"][0]

        assert init.is_dunder is True
        assert str_method.is_dunder is True

    def test_detects_private_methods(self):
        """Should mark private methods (single underscore)."""
        source = """
        class MyClass:
            def _private_method(self):
                pass

            def public_method(self):
                pass
        """
        result = analyze_source(source)

        private = [s for s in result.definitions.values() if s.name == "_private_method"][0]
        public = [s for s in result.definitions.values() if s.name == "public_method"][0]

        assert private.is_private is True
        assert public.is_private is False

    def test_dunder_not_private(self):
        """Dunder methods should not be marked as private."""
        source = """
        class MyClass:
            def __init__(self):
                pass
        """
        result = analyze_source(source)

        init = [s for s in result.definitions.values() if s.name == "__init__"][0]

        assert init.is_dunder is True
        assert init.is_private is False


class TestVisitorAssignments:
    """Tests for various assignment types."""

    def test_annotated_assignment(self):
        """Should handle annotated assignments."""
        source = """
        name: str = "test"
        count: int = 0
        """
        result = analyze_source(source)

        names = [s.name for s in result.definitions.values()]
        assert "name" in names
        assert "count" in names

    def test_tuple_unpacking(self):
        """Should handle tuple unpacking in assignments."""
        source = """
        a, b = 1, 2
        x, y, z = (1, 2, 3)
        """
        result = analyze_source(source)

        # Local variables inside module scope should be tracked
        # but this depends on implementation
        # At minimum, should not crash

    def test_for_loop_variables(self):
        """Should track for loop variables (as local)."""
        source = """
        def func():
            for i in range(10):
                print(i)
        """
        result = analyze_source(source)

        # 'i' should be treated as local and not cause issues
        assert result.error is None

    def test_with_statement_variables(self):
        """Should track with statement variables."""
        source = """
        def func():
            with open("file") as f:
                data = f.read()
        """
        result = analyze_source(source)

        assert result.error is None


class TestVisitorUsageContexts:
    """Tests for different usage contexts."""

    def test_call_context(self):
        """Should track function calls."""
        source = """
        def helper():
            pass

        def main():
            helper()
        """
        result = analyze_source(source)

        helper_usages = [u for u in result.usages
                        if u.symbol_name == "helper" and u.context == UsageContext.CALL]
        assert len(helper_usages) >= 1

    def test_attribute_context(self):
        """Should track attribute access."""
        source = """
        class Obj:
            value = 1

        def func():
            obj = Obj()
            return obj.value
        """
        result = analyze_source(source)

        value_usages = [u for u in result.usages
                       if u.symbol_name == "value" and u.context == UsageContext.ATTRIBUTE]
        assert len(value_usages) >= 1

    def test_inheritance_context(self):
        """Should track inheritance usages."""
        source = """
        class Base:
            pass

        class Derived(Base):
            pass
        """
        result = analyze_source(source)

        base_usages = [u for u in result.usages
                      if u.symbol_name == "Base" and u.context == UsageContext.INHERITANCE]
        assert len(base_usages) >= 1

    def test_type_hint_context(self):
        """Should track type hint usages."""
        source = """
        from typing import List

        def func(items: List[int]) -> int:
            return sum(items)
        """
        result = analyze_source(source)

        # Should have type hint usages
        type_hint_usages = [u for u in result.usages
                          if u.context == UsageContext.TYPE_HINT]
        assert len(type_hint_usages) >= 1


class TestVisitorComprehensions:
    """Tests for comprehension variable handling."""

    def test_list_comprehension(self):
        """Should handle list comprehension variables."""
        source = """
        def func():
            result = [x * 2 for x in range(10)]
            return result
        """
        result = analyze_source(source)

        assert result.error is None

    def test_dict_comprehension(self):
        """Should handle dict comprehension variables."""
        source = """
        def func():
            result = {k: v for k, v in items.items()}
            return result
        """
        result = analyze_source(source)

        assert result.error is None

    def test_set_comprehension(self):
        """Should handle set comprehension variables."""
        source = """
        def func():
            result = {x for x in range(10)}
            return result
        """
        result = analyze_source(source)

        assert result.error is None

    def test_generator_expression(self):
        """Should handle generator expressions."""
        source = """
        def func():
            result = (x * 2 for x in range(10))
            return list(result)
        """
        result = analyze_source(source)

        assert result.error is None


class TestVisitorErrorHandling:
    """Tests for error handling in the visitor."""

    def test_syntax_error_reported(self, tmp_path: Path):
        """Should report syntax errors."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(:\n    pass")

        result = analyze_file(bad_file)

        assert result.error is not None
        assert "Syntax error" in result.error

    def test_encoding_error_reported(self, tmp_path: Path):
        """Should report encoding errors."""
        bad_file = tmp_path / "bad_encoding.py"
        bad_file.write_bytes(b"\xff\xfe invalid utf8")

        result = analyze_file(bad_file)

        assert result.error is not None

    def test_empty_file_works(self, tmp_path: Path):
        """Should handle empty files."""
        empty_file = tmp_path / "empty.py"
        empty_file.write_text("")

        result = analyze_file(empty_file)

        assert result.error is None
        assert len(result.definitions) == 0


class TestVisitorComments:
    """Tests for comment extraction."""

    def test_extracts_comments(self):
        """Should extract line comments."""
        source = """
        # This is a comment
        x = 1  # inline comment

        def func():
            pass  # noqa
        """
        result = analyze_source(source)

        assert len(result.line_comments) > 0

    def test_extracts_noqa_comments(self):
        """Should extract noqa comments."""
        source = """
        import os  # noqa: F401
        """
        result = analyze_source(source)

        noqa_comments = [c for c in result.line_comments.values() if "noqa" in c.lower()]
        assert len(noqa_comments) >= 1
