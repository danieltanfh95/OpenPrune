"""Tests for the scoring module."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openprune.analysis.scoring import (
    ScoringConfig,
    SuspicionScorer,
    classify_confidence,
    get_file_age_info,
    get_file_mtime,
)
from openprune.models.dependency import DependencyNode, Location, Symbol, SymbolType


def make_symbol(
    name: str,
    sym_type: SymbolType = SymbolType.FUNCTION,
    decorators: list[str] | None = None,
    is_dunder: bool = False,
    is_private: bool = False,
    is_entrypoint: bool = False,
    file: str = "test.py",
) -> Symbol:
    """Helper to create a symbol for testing."""
    return Symbol(
        name=name,
        qualified_name=f"module.{name}",
        type=sym_type,
        location=Location(file=Path(file), line=1, column=0),
        scope="module",
        decorators=decorators or [],
        is_dunder=is_dunder,
        is_private=is_private,
        is_entrypoint=is_entrypoint,
    )


def make_node(symbol: Symbol) -> DependencyNode:
    """Helper to create a dependency node for testing."""
    return DependencyNode(symbol=symbol)


class TestScoringConfig:
    """Tests for ScoringConfig dataclass."""

    def test_default_base_confidence(self):
        """Should set default base confidence values in __post_init__."""
        config = ScoringConfig()

        assert config.base_confidence[SymbolType.FUNCTION] == 60
        assert config.base_confidence[SymbolType.METHOD] == 60
        assert config.base_confidence[SymbolType.CLASS] == 60
        assert config.base_confidence[SymbolType.VARIABLE] == 60
        assert config.base_confidence[SymbolType.IMPORT] == 90
        assert config.base_confidence[SymbolType.CONSTANT] == 70
        assert config.base_confidence[SymbolType.MODULE] == 80

    def test_custom_base_confidence_preserved(self):
        """Should preserve custom base confidence values."""
        custom = {SymbolType.FUNCTION: 50, SymbolType.CLASS: 70}
        config = ScoringConfig(base_confidence=custom)

        assert config.base_confidence[SymbolType.FUNCTION] == 50
        assert config.base_confidence[SymbolType.CLASS] == 70
        # Should not have other defaults since custom was provided
        assert SymbolType.IMPORT not in config.base_confidence

    def test_default_penalties(self):
        """Should have correct default penalty values."""
        config = ScoringConfig()

        assert config.dunder_penalty == -40
        assert config.private_penalty == -10
        assert config.entrypoint_penalty == -40
        assert config.decorator_penalty == -20
        assert config.name_used_penalty == -40

    def test_file_age_defaults(self):
        """Should have correct file age threshold defaults."""
        config = ScoringConfig()

        assert config.stale_file_months == 6
        assert config.stale_file_bonus == 10
        assert config.very_stale_file_months == 12
        assert config.very_stale_file_bonus == 15


class TestSuspicionScorerBasic:
    """Basic tests for SuspicionScorer."""

    def test_base_confidence_for_function(self):
        """Should return base confidence for a plain function."""
        scorer = SuspicionScorer()
        symbol = make_symbol("my_func", SymbolType.FUNCTION)
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        assert confidence == 60
        assert any("Base confidence" in r for r in reasons)

    def test_base_confidence_for_import(self):
        """Imports should have higher base confidence (more likely unused)."""
        scorer = SuspicionScorer()
        symbol = make_symbol("os", SymbolType.IMPORT)
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        assert confidence == 90

    def test_base_confidence_for_class(self):
        """Classes should have standard base confidence."""
        scorer = SuspicionScorer()
        symbol = make_symbol("MyClass", SymbolType.CLASS)
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        assert confidence == 60

    def test_base_confidence_for_constant(self):
        """Constants should have slightly higher confidence."""
        scorer = SuspicionScorer()
        symbol = make_symbol("MY_CONSTANT", SymbolType.CONSTANT)
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        assert confidence == 70


class TestSuspicionScorerPenalties:
    """Tests for penalty calculations."""

    def test_name_used_penalty(self):
        """Should apply penalty when name is found in usages."""
        scorer = SuspicionScorer()
        symbol = make_symbol("helper")
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, {"helper"})

        # 60 (base) + (-40) (name used) = 20
        assert confidence == 20
        assert any("Name 'helper' found in usages" in r for r in reasons)

    def test_qualified_name_used_penalty(self):
        """Should apply penalty when qualified name is found."""
        scorer = SuspicionScorer()
        symbol = make_symbol("helper")
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, {"module.helper"})

        # 60 (base) + (-20) (qualified name used) = 40
        assert confidence == 40
        assert any("Qualified name found" in r for r in reasons)

    def test_both_name_penalties(self):
        """Should apply both name and qualified name penalties."""
        scorer = SuspicionScorer()
        symbol = make_symbol("helper")
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, {"helper", "module.helper"})

        # 60 (base) + (-40) (name) + (-20) (qualified) = 0
        assert confidence == 0

    def test_dunder_penalty(self):
        """Should apply dunder penalty for dunder methods."""
        scorer = SuspicionScorer()
        symbol = make_symbol("__init__", is_dunder=True)
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-40) (dunder) = 20
        assert confidence == 20
        assert any("Dunder/implicit" in r for r in reasons)

    def test_implicit_name_penalty(self):
        """Should apply penalty for implicit names like setUp."""
        scorer = SuspicionScorer()
        symbol = make_symbol("setUp")
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # setUp is in implicit_names
        assert confidence == 20
        assert any("Dunder/implicit" in r for r in reasons)

    def test_private_penalty(self):
        """Should apply private penalty for _private methods."""
        scorer = SuspicionScorer()
        symbol = make_symbol("_helper", is_private=True)
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-10) (private) = 50
        assert confidence == 50
        assert any("Private symbol" in r for r in reasons)

    def test_private_dunder_no_double_penalty(self):
        """Dunder methods should not also get private penalty."""
        scorer = SuspicionScorer()
        symbol = make_symbol("__init__", is_dunder=True, is_private=False)
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # Should only get dunder penalty, not private
        assert confidence == 20
        assert not any("Private symbol" in r for r in reasons)

    def test_entrypoint_penalty(self):
        """Should apply entrypoint penalty for marked entrypoints."""
        scorer = SuspicionScorer()
        symbol = make_symbol("index", is_entrypoint=True)
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-40) (entrypoint) = 20
        assert confidence == 20
        assert any("Marked as entrypoint" in r for r in reasons)

    def test_decorator_penalty(self):
        """Should apply decorator penalty for entrypoint-like decorators."""
        scorer = SuspicionScorer()
        symbol = make_symbol("index", decorators=["@app.route"])
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-40) (Flask plugin route decorator) = 20
        # Flask plugin applies -40 for route decorators (takes precedence over generic -20)
        assert confidence == 20
        assert any("Flask route decorator" in r for r in reasons)

    def test_multiple_decorator_penalties_single(self):
        """Should only apply decorator penalty once per decorator."""
        scorer = SuspicionScorer()
        # "route" matches the Flask plugin decorator pattern
        symbol = make_symbol("index", decorators=["@app.route('/home')"])
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-40) (Flask plugin route decorator) = 20
        assert confidence == 20

    def test_test_function_penalty(self):
        """Should apply penalty for test functions."""
        scorer = SuspicionScorer()
        symbol = make_symbol("test_something")
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-30) (test function) = 30
        assert confidence == 30
        assert any("test function" in r for r in reasons)

    def test_test_function_suffix(self):
        """Should detect test functions by suffix."""
        scorer = SuspicionScorer()
        symbol = make_symbol("something_test")
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        assert confidence == 30
        assert any("test function" in r for r in reasons)


class TestSuspicionScorerFileAge:
    """Tests for file age scoring."""

    def test_stale_file_bonus(self, tmp_path: Path):
        """Should add bonus for stale files (6+ months old)."""
        scorer = SuspicionScorer()
        test_file = tmp_path / "old.py"
        test_file.write_text("# old file")

        symbol = make_symbol("func", file=str(test_file))
        node = make_node(symbol)

        # Create file age info with 7 months ago
        seven_months_ago = datetime.now() - timedelta(days=210)
        file_age_info = {test_file: seven_months_ago}

        confidence, reasons = scorer.score(node, set(), file_age_info)

        # 60 (base) + 10 (stale) = 70
        assert confidence == 70
        assert any("months" in r for r in reasons)

    def test_very_stale_file_bonus(self, tmp_path: Path):
        """Should add higher bonus for very stale files (12+ months old)."""
        scorer = SuspicionScorer()
        test_file = tmp_path / "very_old.py"
        test_file.write_text("# very old file")

        symbol = make_symbol("func", file=str(test_file))
        node = make_node(symbol)

        # Create file age info with 14 months ago
        fourteen_months_ago = datetime.now() - timedelta(days=420)
        file_age_info = {test_file: fourteen_months_ago}

        confidence, reasons = scorer.score(node, set(), file_age_info)

        # 60 (base) + 15 (very stale) = 75
        assert confidence == 75

    def test_recent_file_no_bonus(self, tmp_path: Path):
        """Should not add bonus for recent files."""
        scorer = SuspicionScorer()
        test_file = tmp_path / "recent.py"
        test_file.write_text("# recent file")

        symbol = make_symbol("func", file=str(test_file))
        node = make_node(symbol)

        # Create file age info with 2 months ago
        two_months_ago = datetime.now() - timedelta(days=60)
        file_age_info = {test_file: two_months_ago}

        confidence, reasons = scorer.score(node, set(), file_age_info)

        # 60 (base), no stale bonus
        assert confidence == 60

    def test_timezone_aware_datetime(self, tmp_path: Path):
        """Should handle timezone-aware datetimes correctly."""
        scorer = SuspicionScorer()
        test_file = tmp_path / "tz.py"
        test_file.write_text("# tz file")

        symbol = make_symbol("func", file=str(test_file))
        node = make_node(symbol)

        # Create timezone-aware datetime 7 months ago
        seven_months_ago = datetime.now(timezone.utc) - timedelta(days=210)
        file_age_info = {test_file: seven_months_ago}

        confidence, reasons = scorer.score(node, set(), file_age_info)

        # Should handle tz-aware datetime and add stale bonus
        assert confidence == 70


class TestSuspicionScorerConfidenceClamping:
    """Tests for confidence clamping to 0-100 range."""

    def test_clamp_to_zero(self):
        """Should clamp confidence to minimum of 0."""
        scorer = SuspicionScorer()
        # Dunder, used by name, used by qualified name, entrypoint, decorator
        symbol = make_symbol(
            "__init__",
            is_dunder=True,
            is_entrypoint=True,
            decorators=["@app.route"],
        )
        node = make_node(symbol)

        # 60 - 40 (dunder) - 40 (entrypoint) - 20 (decorator) = -40, clamped to 0
        confidence, _ = scorer.score(node, {"__init__", "module.__init__"})

        assert confidence == 0

    def test_clamp_to_hundred(self):
        """Should clamp confidence to maximum of 100."""
        scorer = SuspicionScorer()
        # Create a custom config with very high values
        config = ScoringConfig()
        config.base_confidence = {SymbolType.IMPORT: 100}
        config.very_stale_file_bonus = 50

        scorer = SuspicionScorer(config)
        symbol = make_symbol("unused_import", SymbolType.IMPORT)
        node = make_node(symbol)

        # Even with high bonuses, should clamp to 100
        confidence, _ = scorer.score(node, set())

        assert confidence == 100


class TestSuspicionScorerEntrypointDecorators:
    """Tests for entrypoint decorator detection."""

    def test_route_decorator(self):
        """Should recognize route as entrypoint decorator."""
        scorer = SuspicionScorer()
        symbol = make_symbol("handler", decorators=["@bp.route('/api')"])
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-40) (Flask plugin route decorator) = 20
        assert confidence == 20
        assert any("route" in r.lower() for r in reasons)

    def test_task_decorator(self):
        """Should recognize task as entrypoint decorator."""
        scorer = SuspicionScorer()
        symbol = make_symbol("send_email", decorators=["@celery.task"])
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-40) (Celery plugin task decorator) = 20
        assert confidence == 20

    def test_pytest_fixture_decorator(self):
        """Should recognize pytest.fixture as entrypoint decorator."""
        scorer = SuspicionScorer()
        symbol = make_symbol("db", decorators=["@pytest.fixture"])
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        # 60 (base) + (-40) (Pytest plugin fixture decorator) = 20
        assert confidence == 20

    def test_property_decorator(self):
        """Should recognize property as entrypoint decorator."""
        scorer = SuspicionScorer()
        symbol = make_symbol("value", decorators=["@property"])
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        assert confidence == 40

    def test_abstractmethod_decorator(self):
        """Should recognize abstractmethod as entrypoint decorator."""
        scorer = SuspicionScorer()
        symbol = make_symbol("do_something", decorators=["@abc.abstractmethod"])
        node = make_node(symbol)

        confidence, reasons = scorer.score(node, set())

        assert confidence == 40


class TestCalculateUnreachableScore:
    """Tests for unreachable code scoring."""

    def test_after_return(self):
        """Should return 100 for code after return statement."""
        scorer = SuspicionScorer()
        confidence, reasons = scorer.calculate_unreachable_score(is_after_return=True)

        assert confidence == 100
        assert "return/raise/break" in reasons[0].lower()

    def test_never_true(self):
        """Should return 100 for conditions that are always False."""
        scorer = SuspicionScorer()
        confidence, reasons = scorer.calculate_unreachable_score(is_never_true=True)

        assert confidence == 100
        assert "always False" in reasons[0]

    def test_neither(self):
        """Should return 0 when no unreachable condition."""
        scorer = SuspicionScorer()
        confidence, reasons = scorer.calculate_unreachable_score()

        assert confidence == 0
        assert reasons == []


class TestClassifyConfidence:
    """Tests for confidence classification."""

    def test_high_confidence(self):
        """Should classify 90+ as high."""
        assert classify_confidence(90) == "high"
        assert classify_confidence(100) == "high"
        assert classify_confidence(95) == "high"

    def test_medium_confidence(self):
        """Should classify 70-89 as medium."""
        assert classify_confidence(70) == "medium"
        assert classify_confidence(89) == "medium"
        assert classify_confidence(80) == "medium"

    def test_low_confidence(self):
        """Should classify below 70 as low."""
        assert classify_confidence(69) == "low"
        assert classify_confidence(0) == "low"
        assert classify_confidence(50) == "low"


class TestGetFileMtime:
    """Tests for get_file_mtime function."""

    def test_existing_file(self, tmp_path: Path):
        """Should return mtime for existing file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")

        mtime = get_file_mtime(test_file)

        assert mtime is not None
        assert isinstance(mtime, datetime)

    def test_nonexistent_file(self):
        """Should return None for nonexistent file."""
        mtime = get_file_mtime(Path("/nonexistent/file.py"))

        assert mtime is None


class TestGetFileAgeInfo:
    """Tests for get_file_age_info function."""

    def test_returns_dict_of_datetimes(self, tmp_path: Path):
        """Should return dict mapping paths to datetimes."""
        file1 = tmp_path / "a.py"
        file2 = tmp_path / "b.py"
        file1.write_text("# a")
        file2.write_text("# b")

        result = get_file_age_info([file1, file2], prefer_git=False)

        assert file1 in result
        assert file2 in result
        assert isinstance(result[file1], datetime)
        assert isinstance(result[file2], datetime)

    def test_empty_file_list(self):
        """Should return empty dict for empty file list."""
        result = get_file_age_info([])

        assert result == {}

    def test_nonexistent_files_excluded(self):
        """Should exclude files that don't exist."""
        result = get_file_age_info([Path("/nonexistent/file.py")], prefer_git=False)

        assert result == {}


class TestSQLAlchemyModelScoring:
    """Tests for SQLAlchemy model scoring based on ORM usages.

    Note: SQLAlchemy model classes also get the 'implicit name' penalty (-40)
    from the SQLAlchemy plugin, which is added to the ORM scoring adjustments.
    """

    def test_model_with_no_usages_gets_high_penalty(self):
        """SQLAlchemy model with no usages at all should get +40 confidence.

        This overrides the entrypoint protection for truly unused models.
        """
        scorer = SuspicionScorer()
        symbol = Symbol(
            name="UnusedModel",
            qualified_name="models.UnusedModel",
            type=SymbolType.CLASS,
            location=Location(file=Path("models.py"), line=1, column=0),
            scope="module",
            parent_classes=["db.Model"],  # SQLAlchemy model
        )
        node = make_node(symbol)

        # No usages at all (neither in used_names nor ORM)
        confidence, reasons = scorer.score(
            node, set(), None, orm_usages=set(), model_table_mapping={}
        )

        # 60 (base) - 40 (implicit name from plugin) + 40 (no usages) = 60
        assert confidence == 60
        assert any("no usages" in r for r in reasons)

    def test_model_with_usages_but_no_orm_gets_penalty(self):
        """SQLAlchemy model used somewhere but not via ORM gets +30 confidence."""
        scorer = SuspicionScorer()
        symbol = Symbol(
            name="UsedModel",
            qualified_name="models.UsedModel",
            type=SymbolType.CLASS,
            location=Location(file=Path("models.py"), line=1, column=0),
            scope="module",
            parent_classes=["db.Model"],  # SQLAlchemy model
        )
        node = make_node(symbol)

        # Model is used somewhere (e.g., in type hints) but not via ORM
        confidence, reasons = scorer.score(
            node, {"UsedModel"}, None, orm_usages=set(), model_table_mapping={}
        )

        # 60 (base) - 40 (implicit) - 40 (name in usages) + 30 (no ORM) = 10
        assert confidence == 10
        assert any("no ORM usages" in r for r in reasons)

    def test_model_with_orm_usages_gets_reduction(self):
        """SQLAlchemy model with ORM usages should get -20 confidence."""
        scorer = SuspicionScorer()
        symbol = Symbol(
            name="User",
            qualified_name="models.User",
            type=SymbolType.CLASS,
            location=Location(file=Path("models.py"), line=1, column=0),
            scope="module",
            parent_classes=["Model"],  # SQLAlchemy model
        )
        node = make_node(symbol)

        # User is used via ORM
        confidence, reasons = scorer.score(
            node, set(), None, orm_usages={"User"}, model_table_mapping={}
        )

        # 60 (base) - 40 (implicit name from plugin) - 20 (has ORM usages) = 0
        assert confidence == 0
        assert any("ORM usages" in r for r in reasons)

    def test_model_matched_by_table_name(self):
        """SQLAlchemy model should be matched by table name in ForeignKey."""
        scorer = SuspicionScorer()
        symbol = Symbol(
            name="User",
            qualified_name="models.User",
            type=SymbolType.CLASS,
            location=Location(file=Path("models.py"), line=1, column=0),
            scope="module",
            parent_classes=["Base"],  # SQLAlchemy model
        )
        node = make_node(symbol)

        # Table "users" is referenced via ForeignKey
        confidence, reasons = scorer.score(
            node,
            set(),
            None,
            orm_usages={"users"},  # Table name, not class name
            model_table_mapping={"User": "users"},  # Maps class to table
        )

        # 60 (base) - 40 (implicit name from plugin) - 20 (has ORM usages) = 0
        assert confidence == 0
        assert any("ORM usages" in r for r in reasons)

    def test_non_model_class_not_affected(self):
        """Non-SQLAlchemy classes should not get ORM scoring."""
        scorer = SuspicionScorer()
        symbol = Symbol(
            name="RegularClass",
            qualified_name="module.RegularClass",
            type=SymbolType.CLASS,
            location=Location(file=Path("module.py"), line=1, column=0),
            scope="module",
            parent_classes=["object"],  # Not a SQLAlchemy model
        )
        node = make_node(symbol)

        confidence, reasons = scorer.score(
            node, set(), None, orm_usages=set(), model_table_mapping={}
        )

        # Should just be base confidence, no ORM adjustment
        assert confidence == 60
        assert not any("ORM" in r for r in reasons)

    def test_function_not_affected_by_orm_scoring(self):
        """Functions should not get ORM scoring even if named like a model."""
        scorer = SuspicionScorer()
        symbol = make_symbol("User", SymbolType.FUNCTION)
        node = make_node(symbol)

        confidence, reasons = scorer.score(
            node, set(), None, orm_usages=set(), model_table_mapping={}
        )

        # Should just be base confidence
        assert confidence == 60
        assert not any("ORM" in r for r in reasons)
