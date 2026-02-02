"""Data models for application archetype detection."""

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path


# Framework types are now dynamic strings defined by plugins.
# Well-known framework types include:
#   "flask", "flask_restplus", "celery", "fastapi", "django", "click", "typer"


class EntrypointType(Enum):
    """Types of framework entrypoints."""

    FLASK_ROUTE = auto()
    FLASK_BLUEPRINT = auto()
    FLASK_CLI = auto()
    FLASK_ERRORHANDLER = auto()
    FLASK_HOOK = auto()
    CELERY_TASK = auto()
    CELERY_SHARED_TASK = auto()
    CELERY_SIGNAL = auto()
    FASTAPI_ROUTE = auto()
    CLICK_COMMAND = auto()
    TYPER_COMMAND = auto()
    MAIN_BLOCK = auto()
    FACTORY_FUNCTION = auto()
    INFRA_ENTRYPOINT = auto()  # From Dockerfile/docker-compose/CI
    SCRIPT_ENTRYPOINT = auto()  # Direct script execution (python script.py)
    PYTEST_TEST = auto()  # test_* functions in CI-covered paths
    PYTEST_FIXTURE = auto()  # @pytest.fixture decorated functions


@dataclass
class FrameworkDetection:
    """Result of detecting a framework in the project."""

    framework: str  # Dynamic string defined by plugins (e.g., "flask", "flask_restplus")
    confidence: float  # 0.0 - 1.0
    evidence: list[str] = field(default_factory=list)  # Files/imports indicating framework
    version: str | None = None


@dataclass
class Entrypoint:
    """A detected entrypoint in the codebase."""

    type: EntrypointType
    name: str
    file: Path
    line: int
    decorator: str | None = None
    arguments: dict | None = None


@dataclass
class LintingConfig:
    """Aggregated linting configuration from the project."""

    ignore_patterns: list[str] = field(default_factory=list)
    noqa_patterns: list[str] = field(default_factory=list)
    type_ignore_patterns: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # Config files found

    def __post_init__(self) -> None:
        # Default noqa patterns if none detected
        if not self.noqa_patterns:
            self.noqa_patterns = ["# noqa", "# type: ignore"]


@dataclass
class ArchetypeResult:
    """Complete result of archetype detection."""

    frameworks: list[FrameworkDetection]
    entrypoints: list[Entrypoint]
    linting_config: LintingConfig
    python_version: str = "3.11"
    project_root: Path = field(default_factory=Path)
