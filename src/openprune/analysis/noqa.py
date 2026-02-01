"""Noqa comment filtering for dead code detection."""

import re
from dataclasses import dataclass, field


@dataclass
class NoqaMatch:
    """Result of noqa pattern matching."""

    matched: bool
    pattern: str
    codes: list[str] = field(default_factory=list)


def is_noqa_suppressed(
    comment: str | None,
    patterns: list[str] | None = None,
) -> NoqaMatch:
    """
    Check if a comment contains a noqa suppression.

    Args:
        comment: The comment string to check (e.g., "# noqa: F401")
        patterns: List of patterns to match (default: ["# noqa", "# type: ignore"])

    Returns:
        NoqaMatch with matched=True if suppression found, plus pattern and codes
    """
    if not comment:
        return NoqaMatch(matched=False, pattern="", codes=[])

    if patterns is None:
        patterns = ["# noqa", "# type: ignore"]

    comment_lower = comment.lower()

    for pattern in patterns:
        if pattern.lower() in comment_lower:
            # Extract codes if present (e.g., "noqa: F401, F403")
            codes: list[str] = []
            code_match = re.search(r"noqa:\s*([\w,\s]+)", comment, re.IGNORECASE)
            if code_match:
                codes = [c.strip() for c in code_match.group(1).split(",")]

            return NoqaMatch(matched=True, pattern=pattern, codes=codes)

    return NoqaMatch(matched=False, pattern="", codes=[])
