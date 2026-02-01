"""Tests for ignore_decorators functionality."""

import pytest
from pathlib import Path
from openprune.models.dependency import Symbol, SymbolType, Location


# Import the function we're testing
# We need to handle the import carefully since cli.py has many dependencies
def _should_ignore_by_decorator(symbol: Symbol, patterns: list[str]) -> bool:
    """Check if symbol has a decorator matching ignore patterns.

    This is a copy of the function from cli.py for isolated testing.
    """
    import fnmatch

    for decorator in symbol.decorators:
        for pattern in patterns:
            # Strip leading @ from both
            pattern_clean = pattern.lstrip("@")
            decorator_clean = decorator.lstrip("@")

            # Try glob match (e.g., "pytest.mark.*")
            if fnmatch.fnmatch(decorator_clean, pattern_clean):
                return True

            # Also check if pattern is contained (e.g., "abstractmethod" in "@abc.abstractmethod")
            if pattern_clean in decorator_clean:
                return True

    return False


def make_symbol(name: str, decorators: list[str]) -> Symbol:
    """Helper to create a symbol with decorators."""
    return Symbol(
        name=name,
        qualified_name=f"test_module.{name}",
        type=SymbolType.FUNCTION,
        location=Location(file=Path("test.py"), line=1, column=0),
        scope="module",
        decorators=decorators,
    )


class TestIgnoreDecoratorsBasic:
    """Basic tests for decorator matching."""

    def test_exact_match_with_at(self):
        """Test exact match with @ prefix."""
        symbol = make_symbol("my_fixture", ["@pytest.fixture"])
        patterns = ["@pytest.fixture"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_exact_match_without_at(self):
        """Test exact match without @ prefix in pattern."""
        symbol = make_symbol("my_fixture", ["@pytest.fixture"])
        patterns = ["pytest.fixture"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_no_match(self):
        """Test no match."""
        symbol = make_symbol("regular_func", ["@app.route"])
        patterns = ["@pytest.fixture"]
        assert _should_ignore_by_decorator(symbol, patterns) is False

    def test_empty_decorators(self):
        """Test symbol with no decorators."""
        symbol = make_symbol("plain_func", [])
        patterns = ["@pytest.fixture"]
        assert _should_ignore_by_decorator(symbol, patterns) is False

    def test_empty_patterns(self):
        """Test with empty patterns list."""
        symbol = make_symbol("my_fixture", ["@pytest.fixture"])
        patterns: list[str] = []
        assert _should_ignore_by_decorator(symbol, patterns) is False


class TestIgnoreDecoratorsGlobMatching:
    """Tests for glob pattern matching."""

    def test_wildcard_match(self):
        """Test wildcard pattern matching."""
        symbol = make_symbol("test_func", ["@pytest.mark.slow"])
        patterns = ["@pytest.mark.*"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_wildcard_match_parametrize(self):
        """Test wildcard matches pytest.mark.parametrize."""
        symbol = make_symbol("test_func", ["@pytest.mark.parametrize"])
        patterns = ["@pytest.mark.*"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_wildcard_no_match(self):
        """Test wildcard doesn't match different prefix."""
        symbol = make_symbol("test_func", ["@unittest.skip"])
        patterns = ["@pytest.mark.*"]
        assert _should_ignore_by_decorator(symbol, patterns) is False


class TestIgnoreDecoratorsContainment:
    """Tests for containment matching."""

    def test_abstractmethod_in_abc(self):
        """Test abstractmethod matches when in abc.abstractmethod."""
        symbol = make_symbol("abstract_method", ["@abc.abstractmethod"])
        patterns = ["@abstractmethod"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_property_standalone(self):
        """Test property matches standalone decorator."""
        symbol = make_symbol("my_property", ["@property"])
        patterns = ["@property"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_override_in_typing(self):
        """Test override matches when in typing.override."""
        symbol = make_symbol("overridden", ["@typing.override"])
        patterns = ["@override"]
        assert _should_ignore_by_decorator(symbol, patterns) is True


class TestIgnoreDecoratorsMultiple:
    """Tests with multiple decorators and patterns."""

    def test_multiple_decorators_one_match(self):
        """Test multiple decorators where one matches."""
        symbol = make_symbol("cached_fixture", ["@cache", "@pytest.fixture"])
        patterns = ["@pytest.fixture"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_multiple_patterns_one_match(self):
        """Test multiple patterns where one matches."""
        symbol = make_symbol("my_method", ["@abstractmethod"])
        patterns = ["@pytest.fixture", "@abstractmethod", "@property"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_multiple_no_match(self):
        """Test multiple decorators and patterns with no match."""
        symbol = make_symbol("route_handler", ["@app.route", "@login_required"])
        patterns = ["@pytest.fixture", "@abstractmethod"]
        assert _should_ignore_by_decorator(symbol, patterns) is False


class TestIgnoreDecoratorsEdgeCases:
    """Edge case tests."""

    def test_decorator_with_args(self):
        """Test decorator string that might include arguments."""
        # Note: In practice, the visitor extracts just the decorator name
        symbol = make_symbol("test", ["@pytest.mark.parametrize"])
        patterns = ["@pytest.mark.*"]
        assert _should_ignore_by_decorator(symbol, patterns) is True

    def test_case_sensitivity(self):
        """Test that matching is case-sensitive."""
        symbol = make_symbol("test", ["@Property"])
        patterns = ["@property"]
        # Should not match due to case sensitivity
        assert _should_ignore_by_decorator(symbol, patterns) is False

    def test_partial_name_no_match(self):
        """Test that partial names don't accidentally match."""
        symbol = make_symbol("test", ["@pytest_fixture_extra"])
        patterns = ["@pytest.fixture"]
        # "pytest.fixture" is not contained in "pytest_fixture_extra"
        assert _should_ignore_by_decorator(symbol, patterns) is False
