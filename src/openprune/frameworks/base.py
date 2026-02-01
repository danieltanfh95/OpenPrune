"""Base framework handler for entrypoint detection."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DecoratorPattern:
    """A decorator pattern that marks a function as an entrypoint."""

    pattern: str  # Regex pattern or simple string match
    entrypoint_type: str
    description: str


class FrameworkHandler(ABC):
    """Base class for framework-specific handling."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Framework name."""
        ...

    @property
    @abstractmethod
    def decorator_patterns(self) -> list[DecoratorPattern]:
        """Decorator patterns that mark entrypoints."""
        ...

    @property
    @abstractmethod
    def factory_functions(self) -> list[str]:
        """Factory function names."""
        ...

    def is_entrypoint_decorator(self, decorator: str) -> bool:
        """Check if a decorator marks an entrypoint."""
        decorator_lower = decorator.lower()
        for pattern in self.decorator_patterns:
            if pattern.pattern.lower() in decorator_lower:
                return True
        return False
