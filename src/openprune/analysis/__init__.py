"""Analysis modules for dead code detection."""

from openprune.analysis.imports import ImportGraph, ImportResolver
from openprune.analysis.noqa import NoqaMatch, is_noqa_suppressed
from openprune.analysis.scope import NestedScope
from openprune.analysis.scoring import ScoringConfig, SuspicionScorer
from openprune.analysis.visitor import DeadCodeVisitor, extract_line_comments

__all__ = [
    "DeadCodeVisitor",
    "ImportGraph",
    "ImportResolver",
    "NestedScope",
    "NoqaMatch",
    "ScoringConfig",
    "SuspicionScorer",
    "extract_line_comments",
    "is_noqa_suppressed",
]
