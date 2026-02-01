"""Detection modules for framework and entrypoint discovery."""

from openprune.detection.archetype import ArchetypeDetector
from openprune.detection.entrypoints import EntrypointVisitor
from openprune.detection.linting import LintingDetector

__all__ = ["ArchetypeDetector", "EntrypointVisitor", "LintingDetector"]
