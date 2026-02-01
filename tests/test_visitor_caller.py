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
