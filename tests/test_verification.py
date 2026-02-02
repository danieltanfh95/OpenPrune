"""Tests for the verification module."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from openprune.models.verification import LLMVerdict, VerifiedItem, VerificationResults, VerificationSummary
from openprune.verification.prompts import (
    OPENPRUNE_SYSTEM_PROMPT,
    build_combined_prompt,
    build_system_prompt,
)
from openprune.verification.batch import (
    _build_oneshot_prompt,
    _parse_item_from_text,
    _parse_oneshot_response,
    _build_results,
    _build_empty_results,
)


class TestBuildSystemPrompt:
    """Tests for build_system_prompt function."""

    def test_includes_base_prompt(self, tmp_path: Path):
        """Should include the base OPENPRUNE_SYSTEM_PROMPT."""
        result = build_system_prompt(tmp_path)

        assert "OpenPrune" in result
        assert "dead code" in result.lower()
        assert "DELETE" in result
        assert "KEEP" in result
        assert "UNCERTAIN" in result

    def test_includes_project_path(self, tmp_path: Path):
        """Should include project path in context."""
        result = build_system_prompt(tmp_path)

        assert str(tmp_path) in result

    def test_includes_priority_tiers(self, tmp_path: Path):
        """Should include priority tier descriptions."""
        result = build_system_prompt(tmp_path)

        # Check that tier information is included
        assert "P0" in result
        assert "P1" in result
        assert "P2" in result
        assert "P3" in result

    def test_includes_results_summary_when_exists(self, tmp_path: Path):
        """Should include results summary when results.json exists."""
        # Create .openprune directory and results.json
        openprune_dir = tmp_path / ".openprune"
        openprune_dir.mkdir()
        results_file = openprune_dir / "results.json"
        results_file.write_text(json.dumps({
            "dead_code": [
                {"name": "a", "confidence": 90},
                {"name": "b", "confidence": 75},
                {"name": "c", "confidence": 30},
            ],
            "orphaned_files": [
                {"file": "deprecated.py"}
            ]
        }))

        result = build_system_prompt(tmp_path)

        assert "1" in result  # orphaned file count
        assert "3" in result  # total dead code
        assert "High confidence" in result

    def test_handles_missing_results_file(self, tmp_path: Path):
        """Should work without results.json."""
        result = build_system_prompt(tmp_path)

        # Should not crash, just return base prompt with project context
        assert str(tmp_path) in result

    def test_handles_invalid_json(self, tmp_path: Path):
        """Should handle malformed results.json gracefully."""
        openprune_dir = tmp_path / ".openprune"
        openprune_dir.mkdir()
        results_file = openprune_dir / "results.json"
        results_file.write_text("not valid json {{{")

        result = build_system_prompt(tmp_path)

        # Should not crash
        assert str(tmp_path) in result


class TestBuildCombinedPrompt:
    """Tests for build_combined_prompt function."""

    def test_includes_system_prompt(self, tmp_path: Path):
        """Should include system prompt content."""
        result = build_combined_prompt(tmp_path)

        assert "OpenPrune" in result
        assert "DELETE" in result

    def test_includes_initial_task(self, tmp_path: Path):
        """Should include initial task instructions."""
        result = build_combined_prompt(tmp_path)

        assert "BEGIN SESSION" in result
        assert "results.json" in result

    def test_includes_separator(self, tmp_path: Path):
        """Should include separator between system and initial prompt."""
        result = build_combined_prompt(tmp_path)

        assert "---" in result


class TestBuildOneshotPrompt:
    """Tests for _build_oneshot_prompt function."""

    def test_includes_candidates(self, tmp_path: Path):
        """Should include all candidates in the prompt."""
        candidates = [
            {
                "qualified_name": "module.func1",
                "name": "func1",
                "file": "module.py",
                "line": 10,
                "confidence": 85,
                "type": "unused_function",
                "reasons": ["No references found"],
            }
        ]

        result = _build_oneshot_prompt(tmp_path, candidates, [])

        assert "module.func1" in result
        assert "func1" in result
        assert "85%" in result

    def test_includes_orphaned_files(self, tmp_path: Path):
        """Should include orphaned files section."""
        orphaned = [
            {"file": "deprecated.py", "symbols": 5, "lines": 100}
        ]

        result = _build_oneshot_prompt(tmp_path, [], orphaned)

        assert "Orphaned Files" in result
        assert "deprecated.py" in result
        assert "5 symbols" in result

    def test_includes_file_contents(self, tmp_path: Path):
        """Should include file contents for candidates."""
        # Create a Python file
        module_file = tmp_path / "module.py"
        module_file.write_text("def func1():\n    pass\n")

        candidates = [
            {
                "qualified_name": "module.func1",
                "name": "func1",
                "file": "module.py",
                "line": 1,
                "confidence": 85,
                "type": "unused_function",
                "reasons": [],
            }
        ]

        result = _build_oneshot_prompt(tmp_path, candidates, [])

        assert "def func1():" in result
        assert "```python" in result

    def test_adds_line_numbers(self, tmp_path: Path):
        """Should add line numbers to file contents."""
        module_file = tmp_path / "module.py"
        module_file.write_text("line1\nline2\nline3\n")

        candidates = [
            {
                "qualified_name": "module.func1",
                "file": "module.py",
                "line": 1,
                "confidence": 85,
                "type": "unused_function",
                "reasons": [],
            }
        ]

        result = _build_oneshot_prompt(tmp_path, candidates, [])

        # Line numbers should be present
        assert "   1 |" in result or "1 |" in result

    def test_confidence_labels(self, tmp_path: Path):
        """Should add confidence level labels with priority."""
        candidates = [
            {"qualified_name": "a", "file": "a.py", "line": 1, "confidence": 0, "type": "f", "reasons": []},
            {"qualified_name": "b", "file": "b.py", "line": 1, "confidence": 30, "type": "f", "reasons": []},
            {"qualified_name": "c", "file": "c.py", "line": 1, "confidence": 60, "type": "f", "reasons": []},
            {"qualified_name": "d", "file": "d.py", "line": 1, "confidence": 90, "type": "function", "reasons": []},
            {"qualified_name": "e", "file": "e.py", "line": 1, "confidence": 90, "type": "import", "reasons": []},
        ]

        result = _build_oneshot_prompt(tmp_path, candidates, [])

        assert "[ENTRYPOINT]" in result
        assert "[LOW - likely used]" in result
        assert "MEDIUM" in result  # P0 MEDIUM for 60% confidence
        assert "HIGH non-import]" in result  # P1 HIGH for 90% function
        assert "HIGH import]" in result  # P2 HIGH for 90% import

    def test_includes_task_instructions(self, tmp_path: Path):
        """Should include task instructions at the end."""
        result = _build_oneshot_prompt(tmp_path, [], [])

        assert "Your Task" in result
        assert "DELETE" in result
        assert "KEEP" in result
        assert "json" in result.lower()


class TestParseOneshotResponse:
    """Tests for _parse_oneshot_response function."""

    def test_parse_valid_json(self):
        """Should parse valid JSON response."""
        candidates = [
            {"qualified_name": "module.func1", "name": "func1", "confidence": 85, "type": "unused_function"},
            {"qualified_name": "module.func2", "name": "func2", "confidence": 75, "type": "unused_function"},
        ]

        response = '''
Here is my analysis:

```json
{
  "verified_items": [
    {"qualified_name": "module.func1", "verdict": "DELETE", "reasoning": "No usages found"},
    {"qualified_name": "module.func2", "verdict": "KEEP", "reasoning": "Used dynamically"}
  ]
}
```
'''

        result = _parse_oneshot_response(response, candidates)

        assert len(result) == 2
        assert result[0].qualified_name == "module.func1"
        assert result[0].verdict == LLMVerdict.DELETE
        assert result[1].qualified_name == "module.func2"
        assert result[1].verdict == LLMVerdict.KEEP

    def test_parse_json_without_code_block(self):
        """Should parse JSON without code block markers."""
        candidates = [
            {"qualified_name": "module.func1", "name": "func1", "confidence": 85, "type": "unused_function"},
        ]

        response = '''{"verified_items": [{"qualified_name": "module.func1", "verdict": "DELETE", "reasoning": "Dead"}]}'''

        result = _parse_oneshot_response(response, candidates)

        assert len(result) == 1
        assert result[0].verdict == LLMVerdict.DELETE

    def test_fallback_to_text_parsing(self):
        """Should fall back to text parsing when JSON is invalid."""
        candidates = [
            {"qualified_name": "module.func1", "name": "func1", "confidence": 85, "type": "unused_function"},
        ]

        response = '''
I analyzed the code:
- module.func1: DELETE - this function is unused
'''

        result = _parse_oneshot_response(response, candidates)

        assert len(result) == 1
        assert result[0].verdict == LLMVerdict.DELETE

    def test_unknown_verdict_becomes_uncertain(self):
        """Should treat unknown verdicts as UNCERTAIN."""
        candidates = [
            {"qualified_name": "module.func1", "name": "func1", "confidence": 85, "type": "unused_function"},
        ]

        response = '''{"verified_items": [{"qualified_name": "module.func1", "verdict": "MAYBE", "reasoning": "Not sure"}]}'''

        result = _parse_oneshot_response(response, candidates)

        assert result[0].verdict == LLMVerdict.UNCERTAIN


class TestParseItemFromText:
    """Tests for _parse_item_from_text function."""

    def test_find_delete_verdict(self):
        """Should find DELETE verdict in text."""
        response = "I recommend `module.func1` should be DELETE because it's unused."

        verdict, reasoning = _parse_item_from_text(response, "module.func1", "func1")

        assert verdict == LLMVerdict.DELETE

    def test_find_keep_verdict(self):
        """Should find KEEP verdict in text."""
        response = "The function func1 - KEEP, it's used dynamically."

        verdict, reasoning = _parse_item_from_text(response, "module.func1", "func1")

        assert verdict == LLMVerdict.KEEP

    def test_find_uncertain_verdict(self):
        """Should find UNCERTAIN verdict in text."""
        response = "module.func1: UNCERTAIN - need more investigation"

        verdict, reasoning = _parse_item_from_text(response, "module.func1", "func1")

        assert verdict == LLMVerdict.UNCERTAIN

    def test_case_insensitive(self):
        """Should match verdicts case-insensitively."""
        response = "func1 should be delete"

        verdict, _ = _parse_item_from_text(response, "module.func1", "func1")

        assert verdict == LLMVerdict.DELETE

    def test_no_match_returns_uncertain(self):
        """Should return UNCERTAIN when no verdict found."""
        response = "I couldn't determine the status of this function."

        verdict, reasoning = _parse_item_from_text(response, "module.func1", "func1")

        assert verdict == LLMVerdict.UNCERTAIN
        assert "Could not determine" in reasoning


class TestBuildResults:
    """Tests for _build_results function."""

    def test_builds_summary(self):
        """Should build correct summary counts."""
        verified_items = [
            VerifiedItem(
                qualified_name="a", name="a", type="f", file=Path("a.py"), line=1,
                original_confidence=80, reasons=[], verdict=LLMVerdict.DELETE, verified_at=datetime.now()
            ),
            VerifiedItem(
                qualified_name="b", name="b", type="f", file=Path("b.py"), line=1,
                original_confidence=80, reasons=[], verdict=LLMVerdict.DELETE, verified_at=datetime.now()
            ),
            VerifiedItem(
                qualified_name="c", name="c", type="f", file=Path("c.py"), line=1,
                original_confidence=80, reasons=[], verdict=LLMVerdict.KEEP, verified_at=datetime.now()
            ),
            VerifiedItem(
                qualified_name="d", name="d", type="f", file=Path("d.py"), line=1,
                original_confidence=80, reasons=[], verdict=LLMVerdict.UNCERTAIN, verified_at=datetime.now()
            ),
        ]
        skipped = [{"name": "e"}, {"name": "f"}]

        result = _build_results(verified_items, skipped, "claude", 70)

        assert result.summary.delete_count == 2
        assert result.summary.keep_count == 1
        assert result.summary.uncertain_count == 1
        assert result.summary.skipped_count == 2
        assert result.summary.total_items == 6

    def test_includes_metadata(self):
        """Should include metadata."""
        result = _build_results([], [], "claude", "P0, P1")

        assert result.metadata["llm_tool"] == "claude"
        assert result.metadata["tiers"] == "P0, P1"
        assert result.metadata["mode"] == "auto"


class TestBuildEmptyResults:
    """Tests for _build_empty_results function."""

    def test_empty_with_skipped(self):
        """Should build empty results with skipped items."""
        skipped = [{"name": "a"}, {"name": "b"}]

        result = _build_empty_results(skipped, "claude", "P0")

        assert len(result.verified_items) == 0
        assert result.summary.skipped_count == 2
        assert result.summary.delete_count == 0

    def test_metadata_present(self):
        """Should include metadata in empty results."""
        result = _build_empty_results([], "kimi", "P0")

        assert result.metadata["llm_tool"] == "kimi"
        assert result.metadata["tiers"] == "P0"


class TestVerificationModels:
    """Tests for verification data models."""

    def test_llm_verdict_enum(self):
        """Should have correct verdict values."""
        assert LLMVerdict.DELETE.name == "DELETE"
        assert LLMVerdict.KEEP.name == "KEEP"
        assert LLMVerdict.UNCERTAIN.name == "UNCERTAIN"

    def test_verified_item_creation(self):
        """Should create VerifiedItem with all fields."""
        item = VerifiedItem(
            qualified_name="module.func",
            name="func",
            type="unused_function",
            file=Path("module.py"),
            line=10,
            end_line=15,
            original_confidence=85,
            reasons=["No references"],
            code_preview="def func(): pass",
            verdict=LLMVerdict.DELETE,
            llm_reasoning="Confirmed dead code",
            verified_at=datetime.now(),
        )

        assert item.qualified_name == "module.func"
        assert item.verdict == LLMVerdict.DELETE
        assert item.original_confidence == 85

    def test_verification_summary_defaults(self):
        """Should have correct defaults."""
        summary = VerificationSummary()

        assert summary.total_items == 0
        assert summary.delete_count == 0
        assert summary.keep_count == 0
        assert summary.uncertain_count == 0
        assert summary.skipped_count == 0

    def test_verification_results_to_dict(self):
        """Should serialize to dictionary correctly."""
        results = VerificationResults(
            version="1.0",
            metadata={"llm_tool": "claude"},
            summary=VerificationSummary(delete_count=5),
            verified_items=[],
        )

        data = results.to_dict()

        assert data["version"] == "1.0"
        assert data["metadata"]["llm_tool"] == "claude"
        # Summary uses "delete" not "delete_count" in serialization
        assert data["summary"]["delete"] == 5
