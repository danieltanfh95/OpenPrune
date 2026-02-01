"""Detection modules for framework and entrypoint discovery."""

from openprune.detection.archetype import ArchetypeDetector
from openprune.detection.entrypoints import detect_entrypoints
from openprune.detection.linting import LintingDetector

__all__ = ["ArchetypeDetector", "detect_entrypoints", "LintingDetector"]
