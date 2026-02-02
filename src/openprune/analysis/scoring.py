"""Suspicion scoring for dead code detection."""

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openprune.models.dependency import DependencyNode, SymbolType


@dataclass
class ScoringConfig:
    """Configuration for suspicion scoring."""

    # Base confidence by symbol type (Vulture-style)
    base_confidence: dict[SymbolType, int] = field(default_factory=dict)

    # Adjustments
    dunder_penalty: int = -40  # __init__, __str__ less suspicious
    private_penalty: int = -10  # _private slightly less suspicious
    entrypoint_penalty: int = -40  # Framework entrypoints
    decorator_penalty: int = -20  # Has decorators
    name_used_penalty: int = -40  # Name found in usages
    qualified_name_used_penalty: int = -20  # Qualified name found in usages

    # File age adjustments
    stale_file_months: int = 6  # Files not modified in this many months are more suspicious
    stale_file_bonus: int = 10  # Bonus suspicion for stale files
    very_stale_file_months: int = 12  # Very old files
    very_stale_file_bonus: int = 15  # Bonus for very stale files

    def __post_init__(self) -> None:
        if not self.base_confidence:
            self.base_confidence = {
                SymbolType.FUNCTION: 60,
                SymbolType.METHOD: 60,
                SymbolType.CLASS: 60,
                SymbolType.VARIABLE: 60,
                SymbolType.IMPORT: 90,
                SymbolType.CONSTANT: 70,
                SymbolType.MODULE: 80,
            }


class SuspicionScorer:
    """
    Calculate suspicion scores for symbols.

    Score range: 0-100
    - 100: Definitely dead (unreachable code, no usages)
    - 60-99: Likely dead (no obvious usages found)
    - 0-59: Probably used (too many indicators of usage)
    """

    def __init__(self, config: ScoringConfig | None = None) -> None:
        self.config = config or ScoringConfig()

        # Decorators that indicate a symbol is used externally
        self.entrypoint_decorators = {
            "route",
            "get",
            "post",
            "put",
            "delete",
            "patch",
            "task",
            "shared_task",
            "command",
            "pytest.fixture",
            "fixture",
            "property",
            "staticmethod",
            "classmethod",
            "abstractmethod",
            "override",
            "register",
            "receiver",
            "admin.register",
            "login_required",
            "permission_required",
            "api_view",
            "action",
        }

        # Names that are implicitly used
        self.implicit_names = {
            # Dunder methods
            "__init__",
            "__new__",
            "__del__",
            "__repr__",
            "__str__",
            "__bytes__",
            "__format__",
            "__hash__",
            "__bool__",
            "__eq__",
            "__ne__",
            "__lt__",
            "__le__",
            "__gt__",
            "__ge__",
            "__getattr__",
            "__setattr__",
            "__delattr__",
            "__getattribute__",
            "__get__",
            "__set__",
            "__delete__",
            "__call__",
            "__len__",
            "__getitem__",
            "__setitem__",
            "__delitem__",
            "__iter__",
            "__next__",
            "__contains__",
            "__add__",
            "__sub__",
            "__mul__",
            "__truediv__",
            "__floordiv__",
            "__mod__",
            "__pow__",
            "__enter__",
            "__exit__",
            "__aenter__",
            "__aexit__",
            "__await__",
            "__aiter__",
            "__anext__",
            # Test methods
            "setUp",
            "tearDown",
            "setUpClass",
            "tearDownClass",
            "setUpModule",
            "tearDownModule",
        }

    def score(
        self,
        node: DependencyNode,
        used_names: set[str],
        file_age_info: dict[Path, datetime] | None = None,
        orm_usages: set[str] | None = None,
        model_table_mapping: dict[str, str] | None = None,
    ) -> tuple[int, list[str]]:
        """
        Calculate suspicion score for a node.
        Returns (score, reasons).

        Args:
            node: The dependency node to score
            used_names: Set of names that are used somewhere
            file_age_info: Optional dict of file path -> last modified datetime
            orm_usages: Set of model/table names referenced via SQLAlchemy ORM
            model_table_mapping: Dict mapping model class names to table names
        """
        symbol = node.symbol
        reasons: list[str] = []

        # Start with base confidence
        confidence = self.config.base_confidence.get(symbol.type, 60)
        reasons.append(f"Base confidence for {symbol.type.name}: {confidence}")

        # Check if name is used anywhere
        if symbol.name in used_names:
            confidence += self.config.name_used_penalty
            reasons.append(f"Name '{symbol.name}' found in usages: {self.config.name_used_penalty}")

        # Check qualified name usages
        if symbol.qualified_name in used_names:
            confidence += self.config.qualified_name_used_penalty
            reasons.append(
                f"Qualified name found in usages: {self.config.qualified_name_used_penalty}"
            )

        # Dunder methods are almost always used implicitly
        if symbol.is_dunder or symbol.name in self.implicit_names:
            confidence += self.config.dunder_penalty
            reasons.append(f"Dunder/implicit method: {self.config.dunder_penalty}")

        # Check plugin-based implicit names
        from openprune.plugins import get_registry

        registry = get_registry()
        for plugin in registry.all_plugins():
            if plugin.is_implicit_name(
                symbol.name, symbol.parent_classes, symbol.decorators
            ):
                confidence += -40  # Same penalty as implicit names
                reasons.append(f"Implicit name detected by {plugin.name} plugin: -40")
                break  # Only apply once

        # Private symbols are often unused but intentional
        if symbol.is_private and not symbol.is_dunder:
            confidence += self.config.private_penalty
            reasons.append(f"Private symbol: {self.config.private_penalty}")

        # Entrypoint markers
        if symbol.is_entrypoint:
            confidence += self.config.entrypoint_penalty
            reasons.append(f"Marked as entrypoint: {self.config.entrypoint_penalty}")

        # Check for entrypoint-like decorators
        for dec in symbol.decorators:
            for pattern in self.entrypoint_decorators:
                if pattern in dec.lower():
                    confidence += self.config.decorator_penalty
                    reasons.append(
                        f"Has entrypoint decorator '{dec}': {self.config.decorator_penalty}"
                    )
                    break

        # Names that look like test functions
        if symbol.name.startswith("test_") or symbol.name.endswith("_test"):
            confidence -= 30
            reasons.append("Looks like a test function: -30")

        # File age scoring
        if file_age_info and symbol.location.file in file_age_info:
            age_adjustment, age_reason = self._score_file_age(
                file_age_info[symbol.location.file]
            )
            if age_adjustment > 0:
                confidence += age_adjustment
                reasons.append(age_reason)

        # SQLAlchemy Model scoring - check if model has ORM usages
        if self._is_sqlalchemy_model(symbol):
            has_orm_usage = self._check_model_orm_usages(
                symbol, orm_usages, model_table_mapping
            )
            if not has_orm_usage:
                # Model has no ORM usages - increase confidence it's dead
                confidence += 30
                reasons.append("SQLAlchemy Model with no ORM usages: +30")
            else:
                # Model is used via ORM - reduce confidence
                confidence -= 20
                reasons.append("SQLAlchemy Model with ORM usages: -20")

        # Clamp to valid range
        confidence = max(0, min(100, confidence))

        return confidence, reasons

    def _score_file_age(self, last_modified: datetime) -> tuple[int, str]:
        """Calculate suspicion adjustment based on file age."""
        # Handle timezone-aware vs naive datetimes
        if last_modified.tzinfo is not None:
            # Make now timezone-aware to match
            from datetime import timezone
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()
        age = now - last_modified
        months = age.days / 30

        if months >= self.config.very_stale_file_months:
            return (
                self.config.very_stale_file_bonus,
                f"File not modified in {int(months)} months: +{self.config.very_stale_file_bonus}",
            )
        if months >= self.config.stale_file_months:
            return (
                self.config.stale_file_bonus,
                f"File not modified in {int(months)} months: +{self.config.stale_file_bonus}",
            )
        return 0, ""

    def _is_sqlalchemy_model(self, symbol) -> bool:
        """Check if symbol is a SQLAlchemy Model class."""
        if symbol.type != SymbolType.CLASS:
            return False
        sqlalchemy_bases = {"Model", "db.Model", "Base", "DeclarativeBase", "AbstractConcreteBase"}
        for parent in symbol.parent_classes:
            if parent in sqlalchemy_bases:
                return True
        return False

    def _check_model_orm_usages(
        self,
        symbol,
        orm_usages: set[str] | None,
        model_table_mapping: dict[str, str] | None,
    ) -> bool:
        """Check if a model has any ORM usages."""
        if not orm_usages:
            return False

        # Check direct class name usage (e.g., session.query(User), User.query)
        if symbol.name in orm_usages:
            return True

        # Check table name usage (for ForeignKey references like "users.id")
        if model_table_mapping:
            table_name = model_table_mapping.get(symbol.name)
            if table_name and table_name in orm_usages:
                return True

        return False

    def calculate_unreachable_score(
        self,
        is_after_return: bool = False,
        is_never_true: bool = False,
    ) -> tuple[int, list[str]]:
        """Score for unreachable code detection."""
        if is_after_return:
            return 100, ["Code after return/raise/break/continue statement"]
        if is_never_true:
            return 100, ["Condition is always False"]
        return 0, []


def get_git_last_modified(file_path: Path, repo_root: Path | None = None) -> datetime | None:
    """Get the last git commit date for a file."""
    try:
        cwd = repo_root or file_path.parent

        # Security: validate file_path is within repo_root if provided
        if repo_root is not None:
            try:
                resolved_path = file_path.resolve()
                resolved_root = repo_root.resolve()
                if not resolved_path.is_relative_to(resolved_root):
                    return None
            except (ValueError, OSError):
                return None

        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", str(file_path)],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Parse ISO format datetime
            date_str = result.stdout.strip()
            # Handle timezone offset
            if "+" in date_str or date_str.endswith("Z"):
                date_str = date_str.replace("Z", "+00:00")
                return datetime.fromisoformat(date_str)
        return None
    except Exception:
        return None


def get_file_mtime(file_path: Path) -> datetime | None:
    """Get the file system modification time."""
    try:
        stat = file_path.stat()
        return datetime.fromtimestamp(stat.st_mtime)
    except Exception:
        return None


def get_file_age_info(
    files: list[Path],
    repo_root: Path | None = None,
    prefer_git: bool = True,
) -> dict[Path, datetime]:
    """
    Get last modified dates for a list of files.

    Args:
        files: List of file paths
        repo_root: Git repository root (for git log commands)
        prefer_git: If True, prefer git commit date over file mtime

    Returns:
        Dict mapping file path to last modified datetime
    """
    result: dict[Path, datetime] = {}

    for file_path in files:
        last_modified: datetime | None = None

        if prefer_git:
            last_modified = get_git_last_modified(file_path, repo_root)

        if last_modified is None:
            last_modified = get_file_mtime(file_path)

        if last_modified is not None:
            result[file_path] = last_modified

    return result


def classify_confidence(confidence: int) -> str:
    """Classify confidence level into categories."""
    if confidence >= 90:
        return "high"
    if confidence >= 70:
        return "medium"
    return "low"
