"""Prompt templates for LLM verification."""

from pathlib import Path

OPENPRUNE_SYSTEM_PROMPT = """You are helping review dead code candidates detected by OpenPrune.

## What is OpenPrune?

OpenPrune is a static analysis tool that detects potentially dead code in Python Flask+Celery applications. It has already run and produced results in the `.openprune/` directory.

## Files in .openprune/

- `config.json` - Project configuration and detected frameworks
- `results.json` - Dead code candidates with confidence scores
- `verified.json` - Your verification results will be saved here

## Your Task

Help the user review each dead code item in `results.json` and determine:
- **DELETE**: Confirmed dead code, safe to remove
- **KEEP**: False positive, should not be removed
- **UNCERTAIN**: Needs more investigation

For each item, consider:
1. Could it be called dynamically (getattr, reflection, string-based imports)?
2. Is it a public API that external code might depend on?
3. Are there framework-specific patterns (decorators, magic methods) that might invoke it?
4. Could it be used in tests, scripts, or CLI commands not captured by static analysis?

## How to Work

1. Read `.openprune/results.json` to see the dead code candidates
2. For each high-confidence item, examine the actual source code
3. Make a verdict and explain your reasoning
4. Update `.openprune/verified.json` with your verdicts

## verified.json Format

When writing to `.openprune/verified.json`, use this structure:

```json
{
  "version": "1.0",
  "metadata": {
    "verified_at": "<ISO timestamp>",
    "llm_tool": "claude",
    "min_confidence": 70
  },
  "verified_items": [
    {
      "qualified_name": "module.function_name",
      "name": "function_name",
      "type": "unused_function",
      "file": "path/to/file.py",
      "line": 10,
      "original_confidence": 95,
      "reasons": ["No references found"],
      "verdict": "delete",
      "llm_reasoning": "Function has no callers and appears to be legacy code."
    }
  ]
}
```

The user can ask you questions about specific items or request batch processing of all items.
"""


def build_system_prompt(project_path: Path, min_confidence: int = 70) -> str:
    """
    Build a system prompt with project-specific context.

    Args:
        project_path: Path to the project root
        min_confidence: Minimum confidence threshold being used

    Returns:
        Complete system prompt string
    """
    # Start with base prompt
    prompt_parts = [OPENPRUNE_SYSTEM_PROMPT]

    # Add project-specific context
    prompt_parts.append(f"""
## Project Context

- **Project Path**: {project_path}
- **Min Confidence Threshold**: {min_confidence}%

Items with confidence >= {min_confidence}% are the primary focus for verification.
""")

    # Check if results.json exists and add summary
    results_path = project_path / ".openprune" / "results.json"
    if results_path.exists():
        import json

        try:
            with open(results_path) as f:
                results = json.load(f)

            dead_code = results.get("dead_code", [])
            high_conf = [d for d in dead_code if d.get("confidence", 0) >= min_confidence]

            prompt_parts.append(f"""
## Results Summary

- Total dead code candidates: {len(dead_code)}
- High confidence items (>= {min_confidence}%): {len(high_conf)}

Start by reading `.openprune/results.json` to see the full list.
""")
        except (json.JSONDecodeError, OSError):
            pass

    return "\n".join(prompt_parts)


def build_combined_prompt(project_path: Path, min_confidence: int = 70) -> str:
    """
    Build a combined system+initial prompt for LLMs without system prompt support.

    Tools like opencode and kimi don't have --system-prompt flags, so we combine
    the context and task into a single initial prompt.

    Args:
        project_path: Path to the project root
        min_confidence: Minimum confidence threshold being used

    Returns:
        Combined prompt string with context and initial task
    """
    system_prompt = build_system_prompt(project_path, min_confidence)

    initial_task = f"""Start the dead code verification session.

Read .openprune/results.json to see the dead code candidates, then help me review items with confidence >= {min_confidence}%.

For each item, examine the source code and determine if it's truly dead (DELETE), a false positive (KEEP), or needs more investigation (UNCERTAIN).

Let's begin - show me the high-confidence items first."""

    return f"""{system_prompt}

---

## BEGIN SESSION

{initial_task}"""
