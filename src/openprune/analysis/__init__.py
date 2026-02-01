"""Analysis modules for dead code detection."""

from openprune.analysis.imports import ImportGraph, ImportResolver
from openprune.analysis.scope import NestedScope
from openprune.analysis.scoring import ScoringConfig, SuspicionScorer
from openprune.analysis.visitor import DeadCodeVisitor

__all__ = [
    "DeadCodeVisitor",
    "ImportGraph",
    "ImportResolver",
    "NestedScope",
    "ScoringConfig",
    "SuspicionScorer",
]
