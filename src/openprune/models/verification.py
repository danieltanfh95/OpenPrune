"""Verification models for LLM-verified dead code results."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class LLMVerdict(Enum):
    """LLM's decision on whether code should be deleted."""

    DELETE = "delete"  # Confirmed dead, safe to remove
    KEEP = "keep"  # False positive, should not remove
    UNCERTAIN = "uncertain"  # Needs human decision


@dataclass
class VerifiedItem:
    """A dead code item that has been verified by LLM."""

    # Original DeadCodeItem fields
    qualified_name: str
    name: str
    type: str
    file: Path
    line: int
    end_line: int | None = None
    original_confidence: int = 60
    reasons: list[str] = field(default_factory=list)
    code_preview: str | None = None

    # Verification fields
    verdict: LLMVerdict = LLMVerdict.UNCERTAIN
    llm_reasoning: str = ""
    verified_at: datetime | None = None
    # For KEEP verdicts: categorizes the false positive pattern for improvement tracking
    # e.g., "framework_instance", "decorator_implicit", "dynamic_dispatch", "signal_handler",
    #       "registry_pattern", "inheritance", "public_api"
    false_positive_pattern: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "qualified_name": self.qualified_name,
            "name": self.name,
            "type": self.type,
            "file": str(self.file),
            "line": self.line,
            "end_line": self.end_line,
            "original_confidence": self.original_confidence,
            "reasons": self.reasons,
            "code_preview": self.code_preview,
            "verdict": self.verdict.value,
            "llm_reasoning": self.llm_reasoning,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "false_positive_pattern": self.false_positive_pattern,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VerifiedItem":
        """Create from dictionary."""
        return cls(
            qualified_name=data["qualified_name"],
            name=data["name"],
            type=data["type"],
            file=Path(data["file"]),
            line=data["line"],
            end_line=data.get("end_line"),
            original_confidence=data.get("original_confidence", 60),
            reasons=data.get("reasons", []),
            code_preview=data.get("code_preview"),
            verdict=LLMVerdict(data.get("verdict", "uncertain")),
            llm_reasoning=data.get("llm_reasoning", ""),
            verified_at=(
                datetime.fromisoformat(data["verified_at"])
                if data.get("verified_at")
                else None
            ),
            false_positive_pattern=data.get("false_positive_pattern"),
        )


@dataclass
class VerificationSummary:
    """Summary of verification results."""

    total_items: int = 0
    delete_count: int = 0
    keep_count: int = 0
    uncertain_count: int = 0
    skipped_count: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_items": self.total_items,
            "delete": self.delete_count,
            "keep": self.keep_count,
            "uncertain": self.uncertain_count,
            "skipped": self.skipped_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationSummary":
        """Create from dictionary."""
        return cls(
            total_items=data.get("total_items", 0),
            delete_count=data.get("delete", 0),
            keep_count=data.get("keep", 0),
            uncertain_count=data.get("uncertain", 0),
            skipped_count=data.get("skipped", 0),
        )


@dataclass
class VerificationResults:
    """Complete verification results."""

    version: str = "1.0"
    metadata: dict = field(default_factory=dict)
    summary: VerificationSummary | None = None
    verified_items: list[VerifiedItem] = field(default_factory=list)
    skipped_items: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "metadata": self.metadata,
            "summary": self.summary.to_dict() if self.summary else None,
            "verified_items": [item.to_dict() for item in self.verified_items],
            "skipped_items": self.skipped_items,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationResults":
        """Create from dictionary."""
        summary_data = data.get("summary")
        return cls(
            version=data.get("version", "1.0"),
            metadata=data.get("metadata", {}),
            summary=VerificationSummary.from_dict(summary_data) if summary_data else None,
            verified_items=[
                VerifiedItem.from_dict(item) for item in data.get("verified_items", [])
            ],
            skipped_items=data.get("skipped_items", []),
        )
