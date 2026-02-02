"""Pytest framework plugin - CI-aware test detection.

Detects pytest entrypoints:
- test_* functions
- Test* classes with test_* methods
- @pytest.fixture decorated functions

Only marks tests as entrypoints if they're in paths specified by CI config.
If no CI config found, defaults to all test files being covered.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from openprune.models.archetype import EntrypointType
from openprune.plugins.protocol import (
    DecoratorScoringRule,
    DetectedEntrypoint,
    ImplicitName,
)


class PytestPlugin:
    """Plugin for pytest test detection."""

    def __init__(self) -> None:
        self._ci_test_paths: set[Path] | None = None
        self._project_root: Path | None = None

    @property
    def name(self) -> str:
        return "pytest"

    @property
    def framework_type(self) -> str:
        return "pytest"

    @property
    def import_indicators(self) -> list[str]:
        return ["pytest"]

    @property
    def factory_functions(self) -> list[str]:
        return []

    @property
    def implicit_names(self) -> list[ImplicitName]:
        return []

    @property
    def decorator_scoring_rules(self) -> list[DecoratorScoringRule]:
        return [
            DecoratorScoringRule(
                pattern="pytest.fixture",
                score_adjustment=-40,
                description="Pytest fixture",
            ),
        ]

    def detect_entrypoints(
        self,
        tree: ast.AST,
        file_path: Path,
    ) -> list[DetectedEntrypoint]:
        """Detect pytest entrypoints in an AST."""
        # Check if file is in CI-covered test paths
        if not self._is_ci_covered(file_path):
            return []

        visitor = _PytestVisitor(file_path)
        visitor.visit(tree)
        return visitor.entrypoints

    def set_project_root(self, root: Path) -> None:
        """Set project root and parse CI config."""
        self._project_root = root
        self._ci_test_paths = self._parse_ci_test_paths(root)

    def _is_ci_covered(self, file_path: Path) -> bool:
        """Check if file is in a CI-covered test path."""
        # If no CI config found, default to all tests being covered
        if self._ci_test_paths is None or len(self._ci_test_paths) == 0:
            return self._is_test_file(file_path)

        for ci_path in self._ci_test_paths:
            try:
                file_path.relative_to(ci_path)
                return True
            except ValueError:
                continue
        return False

    def _parse_ci_test_paths(self, root: Path) -> set[Path]:
        """Parse CI configs to find pytest test paths."""
        paths: set[Path] = set()

        # Parse .gitlab-ci.yml
        gitlab_ci = root / ".gitlab-ci.yml"
        if gitlab_ci.exists():
            paths.update(self._parse_gitlab_ci(gitlab_ci, root))

        # Parse GitHub Actions
        gh_workflows = root / ".github" / "workflows"
        if gh_workflows.exists():
            for workflow in gh_workflows.glob("*.yml"):
                paths.update(self._parse_github_actions(workflow, root))
            for workflow in gh_workflows.glob("*.yaml"):
                paths.update(self._parse_github_actions(workflow, root))

        return paths

    def _parse_gitlab_ci(self, ci_file: Path, root: Path) -> set[Path]:
        """Extract pytest paths from .gitlab-ci.yml."""
        paths: set[Path] = set()
        content = ci_file.read_text()

        # Find pytest commands and extract paths
        # Pattern: pytest ... ./path1 ./path2
        pytest_pattern = r"pytest\s+.*?((?:\./[\w/.-]+\s*)+)"
        for match in re.finditer(pytest_pattern, content):
            path_str = match.group(1)
            for path in path_str.split():
                if path.startswith("./"):
                    full_path = root / path[2:]  # Remove ./
                    paths.add(full_path.resolve())

        return paths

    def _parse_github_actions(self, workflow: Path, root: Path) -> set[Path]:
        """Extract pytest paths from GitHub Actions workflow."""
        paths: set[Path] = set()
        content = workflow.read_text()

        # Similar pattern matching for pytest commands
        pytest_pattern = r"pytest\s+.*?((?:[\w/.-]+\s*)+)"
        for match in re.finditer(pytest_pattern, content):
            path_str = match.group(1)
            for path in path_str.split():
                if not path.startswith("-"):  # Skip flags
                    full_path = root / path
                    if full_path.exists():
                        paths.add(full_path.resolve())

        return paths

    def _is_test_file(self, file_path: Path) -> bool:
        """Check if file is a test file by naming convention."""
        # test_*.py files or conftest.py
        name = file_path.name
        if name.startswith("test_") or name == "conftest.py":
            return True
        # Check if in test/ or tests/ directory
        parts = file_path.parts
        return "test" in parts or "tests" in parts

    def is_implicit_name(
        self,
        name: str,
        parent_classes: list[str],
        decorators: list[str],
    ) -> bool:
        """Pytest doesn't have implicit method names."""
        return False


class _PytestVisitor(ast.NodeVisitor):
    """AST visitor to find pytest test patterns."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.entrypoints: list[DetectedEntrypoint] = []
        self._current_class: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit class definitions, tracking Test* classes."""
        if node.name.startswith("Test"):
            old_class = self._current_class
            self._current_class = node.name
            self.generic_visit(node)
            self._current_class = old_class
        else:
            self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit function definitions to find tests and fixtures."""
        if self._has_fixture_decorator(node):
            self.entrypoints.append(
                DetectedEntrypoint(
                    name=node.name,
                    type=EntrypointType.PYTEST_FIXTURE,
                    line=node.lineno,
                    file=self.file_path,
                    decorator="@pytest.fixture",
                )
            )
        elif node.name.startswith("test_"):
            self.entrypoints.append(
                DetectedEntrypoint(
                    name=node.name,
                    type=EntrypointType.PYTEST_TEST,
                    line=node.lineno,
                    file=self.file_path,
                    parent_class=self._current_class,
                )
            )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Handle async test functions."""
        self.visit_FunctionDef(node)  # type: ignore

    def _has_fixture_decorator(self, node: ast.FunctionDef) -> bool:
        """Check if function has @pytest.fixture decorator."""
        for decorator in node.decorator_list:
            # @pytest.fixture
            if isinstance(decorator, ast.Attribute):
                if (
                    isinstance(decorator.value, ast.Name)
                    and decorator.value.id == "pytest"
                    and decorator.attr == "fixture"
                ):
                    return True
            # @pytest.fixture()
            elif isinstance(decorator, ast.Call):
                if isinstance(decorator.func, ast.Attribute):
                    if (
                        isinstance(decorator.func.value, ast.Name)
                        and decorator.func.value.id == "pytest"
                        and decorator.func.attr == "fixture"
                    ):
                        return True
            # @fixture (from conftest imports)
            elif isinstance(decorator, ast.Name) and decorator.id == "fixture":
                return True
        return False


def create_plugin() -> PytestPlugin:
    """Factory function for plugin discovery."""
    return PytestPlugin()
