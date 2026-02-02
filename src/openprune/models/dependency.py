"""Data models for dependency tree and symbol tracking."""

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path


class SymbolType(Enum):
    """Types of Python symbols."""

    MODULE = auto()
    CLASS = auto()
    FUNCTION = auto()
    METHOD = auto()
    VARIABLE = auto()
    IMPORT = auto()
    CONSTANT = auto()


class UsageContext(Enum):
    """Context in which a symbol is used."""

    DEFINITION = auto()
    IMPORT = auto()
    CALL = auto()
    REFERENCE = auto()
    ATTRIBUTE = auto()
    INHERITANCE = auto()
    DECORATOR = auto()
    TYPE_HINT = auto()


@dataclass
class Location:
    """Source code location."""

    file: Path
    line: int
    column: int
    end_line: int | None = None
    end_column: int | None = None

    def to_dict(self) -> dict:
        return {
            "file": str(self.file),
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
        }


@dataclass
class Symbol:
    """A Python symbol (function, class, variable, etc.)."""

    name: str
    qualified_name: str  # e.g., "mymodule.MyClass.my_method"
    type: SymbolType
    location: Location
    scope: str  # e.g., "module", "MyClass", "my_function"
    decorators: list[str] = field(default_factory=list)
    is_entrypoint: bool = False
    is_dunder: bool = False  # __name__, __init__, etc.
    is_private: bool = False  # _private or __private
    parent_classes: list[str] = field(default_factory=list)  # For class inheritance tracking


@dataclass
class Usage:
    """A usage of a symbol."""

    symbol_name: str
    context: UsageContext
    location: Location
    resolved_to: str | None = None  # Qualified name if resolved
    caller: str | None = None  # Qualified name of the function/method making this usage


@dataclass
class ImportInfo:
    """Information about an import statement."""

    module: str
    name: str | None  # For "from X import Y", this is Y
    alias: str | None
    location: Location
    is_relative: bool
    level: int  # For relative imports


@dataclass
class DependencyNode:
    """A node in the dependency tree."""

    symbol: Symbol
    definitions: list[Location] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)

    # Analysis results
    is_used: bool = False
    use_count: int = 0
    suspicion_score: float = 0.0  # 0.0 = definitely used, 1.0 = definitely dead
    confidence: int = 60  # 60-100, Vulture-style

    # Reasoning
    reasons: list[str] = field(default_factory=list)


@dataclass
class ModuleInfo:
    """Information about a Python module."""

    name: str
    path: Path | None  # None for built-in/external
    is_package: bool = False
    is_external: bool = False


@dataclass
class DependencyTree:
    """The complete dependency tree for a project."""

    root: Path
    nodes: dict[str, DependencyNode] = field(default_factory=dict)  # qualified_name -> node
    imports_graph: dict[str, list[str]] = field(
        default_factory=dict
    )  # module -> imported modules
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    entrypoints: list[str] = field(default_factory=list)  # qualified names of entrypoints
    orphaned_files: list[Path] = field(default_factory=list)
