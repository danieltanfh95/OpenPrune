"""Deletion models for LLM-driven dead code removal."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class DeletionStatus(Enum):
    """Status of a deletion operation."""

    APPLIED = "applied"  # Successfully removed
    SKIPPED = "skipped"  # Skipped (e.g., file not found, validation failed)
    FAILED = "failed"  # LLM failed to produce valid edit
    DRY_RUN = "dry_run"  # Preview only, not applied


@dataclass
class FileModification:
    """A single file modification performed during deletion."""

    file: Path
    original_lines: int
    modified_lines: int
    lines_removed: int
    symbols_removed: list[str] = field(default_factory=list)
    file_deleted: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "file": str(self.file),
            "original_lines": self.original_lines,
            "modified_lines": self.modified_lines,
            "lines_removed": self.lines_removed,
            "symbols_removed": self.symbols_removed,
            "file_deleted": self.file_deleted,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileModification":
        """Create from dictionary."""
        return cls(
            file=Path(data["file"]),
            original_lines=data["original_lines"],
            modified_lines=data["modified_lines"],
            lines_removed=data["lines_removed"],
            symbols_removed=data.get("symbols_removed", []),
            file_deleted=data.get("file_deleted", False),
        )


@dataclass
class DeletionItem:
    """A single item that was processed for deletion."""

    qualified_name: str
    name: str
    type: str
    file: Path
    line: int
    end_line: int | None = None
    status: DeletionStatus = DeletionStatus.SKIPPED
    error: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "qualified_name": self.qualified_name,
            "name": self.name,
            "type": self.type,
            "file": str(self.file),
            "line": self.line,
            "end_line": self.end_line,
            "status": self.status.value,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeletionItem":
        """Create from dictionary."""
        return cls(
            qualified_name=data["qualified_name"],
            name=data["name"],
            type=data["type"],
            file=Path(data["file"]),
            line=data["line"],
            end_line=data.get("end_line"),
            status=DeletionStatus(data.get("status", "skipped")),
            error=data.get("error"),
        )


@dataclass
class DeletionSummary:
    """Summary of deletion results."""

    total_items: int = 0
    applied_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    files_modified: int = 0
    files_deleted: int = 0
    total_lines_removed: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_items": self.total_items,
            "applied": self.applied_count,
            "skipped": self.skipped_count,
            "failed": self.failed_count,
            "files_modified": self.files_modified,
            "files_deleted": self.files_deleted,
            "total_lines_removed": self.total_lines_removed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeletionSummary":
        """Create from dictionary."""
        return cls(
            total_items=data.get("total_items", 0),
            applied_count=data.get("applied", 0),
            skipped_count=data.get("skipped", 0),
            failed_count=data.get("failed", 0),
            files_modified=data.get("files_modified", 0),
            files_deleted=data.get("files_deleted", 0),
            total_lines_removed=data.get("total_lines_removed", 0),
        )


@dataclass
class DeletionResults:
    """Complete deletion results saved to removals.json."""

    version: str = "1.0"
    metadata: dict = field(default_factory=dict)
    summary: DeletionSummary | None = None
    file_modifications: list[FileModification] = field(default_factory=list)
    deletion_items: list[DeletionItem] = field(default_factory=list)
    git_commit_before: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "metadata": self.metadata,
            "summary": self.summary.to_dict() if self.summary else None,
            "file_modifications": [m.to_dict() for m in self.file_modifications],
            "deletion_items": [i.to_dict() for i in self.deletion_items],
            "git_commit_before": self.git_commit_before,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeletionResults":
        """Create from dictionary."""
        summary_data = data.get("summary")
        return cls(
            version=data.get("version", "1.0"),
            metadata=data.get("metadata", {}),
            summary=DeletionSummary.from_dict(summary_data) if summary_data else None,
            file_modifications=[
                FileModification.from_dict(m)
                for m in data.get("file_modifications", [])
            ],
            deletion_items=[
                DeletionItem.from_dict(i) for i in data.get("deletion_items", [])
            ],
            git_commit_before=data.get("git_commit_before"),
        )
