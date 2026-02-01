"""Data models for analysis results."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class AnalysisMetadata:
    """Metadata about the analysis run."""

    project: str
    analyzed_at: datetime
    openprune_version: str
    files_analyzed: int
    total_symbols: int
    analysis_duration_ms: int

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "analyzed_at": self.analyzed_at.isoformat(),
            "openprune_version": self.openprune_version,
            "files_analyzed": self.files_analyzed,
            "total_symbols": self.total_symbols,
            "analysis_duration_ms": self.analysis_duration_ms,
        }


@dataclass
class DeadCodeItem:
    """A detected piece of dead code."""

    qualified_name: str
    name: str
    type: str  # "unused_function", "unused_import", "unreachable_code", etc.
    file: Path
    line: int
    end_line: int | None = None
    confidence: int = 60
    reasons: list[str] = field(default_factory=list)
    suggested_action: str = "review"  # "remove", "review", "ignore"
    code_preview: str | None = None

    def to_dict(self) -> dict:
        return {
            "qualified_name": self.qualified_name,
            "name": self.name,
            "type": self.type,
            "file": str(self.file),
            "line": self.line,
            "end_line": self.end_line,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "suggested_action": self.suggested_action,
            "code_preview": self.code_preview,
        }


@dataclass
class AnalysisSummary:
    """Summary of analysis results."""

    dead_code_items: int
    by_type: dict[str, int] = field(default_factory=dict)
    by_confidence: dict[str, int] = field(default_factory=dict)
    estimated_lines_removable: int = 0

    def to_dict(self) -> dict:
        return {
            "dead_code_items": self.dead_code_items,
            "by_type": self.by_type,
            "by_confidence": self.by_confidence,
            "estimated_lines_removable": self.estimated_lines_removable,
        }


@dataclass
class EntrypointInfo:
    """Entrypoint information for results."""

    qualified_name: str
    type: str
    file: str
    line: int
    decorator: str | None = None

    def to_dict(self) -> dict:
        result = {
            "qualified_name": self.qualified_name,
            "type": self.type,
            "file": self.file,
            "line": self.line,
        }
        if self.decorator:
            result["decorator"] = self.decorator
        return result


@dataclass
class NoqaSkipped:
    """Information about code skipped due to noqa comments."""

    file: str
    line: int
    comment: str
    symbol: str

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "comment": self.comment,
            "symbol": self.symbol,
        }


@dataclass
class AnalysisResults:
    """Complete analysis results."""

    version: str = "1.0"
    metadata: AnalysisMetadata | None = None
    summary: AnalysisSummary | None = None
    entrypoints: list[EntrypointInfo] = field(default_factory=list)
    dead_code: list[DeadCodeItem] = field(default_factory=list)
    dependency_tree: dict = field(default_factory=dict)
    noqa_skipped: list[NoqaSkipped] = field(default_factory=list)

    def to_dict(self) -> dict:
        result: dict = {"version": self.version}

        if self.metadata:
            result["metadata"] = self.metadata.to_dict()

        if self.summary:
            result["summary"] = self.summary.to_dict()

        result["entrypoints"] = [ep.to_dict() for ep in self.entrypoints]
        result["dead_code"] = [dc.to_dict() for dc in self.dead_code]
        result["dependency_tree"] = self.dependency_tree
        result["noqa_skipped"] = [ns.to_dict() for ns in self.noqa_skipped]

        return result
