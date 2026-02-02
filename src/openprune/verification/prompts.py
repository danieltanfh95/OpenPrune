"""Prompt templates for LLM verification."""

from pathlib import Path

OPENPRUNE_SYSTEM_PROMPT = """You are helping review dead code candidates detected by OpenPrune.

## What is OpenPrune?

OpenPrune is a static analysis tool that detects potentially dead code in Python Flask+Celery applications. It has already run and produced results in the `.openprune/` directory.

## Files in .openprune/

- `config.json` - Project configuration and detected frameworks
- `results.json` - Dead code candidates with confidence scores and reachability info
- `verified.json` - Your verification results will be saved here

## Understanding Confidence Scores

OpenPrune uses entrypoint-based reachability analysis:

- **100% confidence**: Symbol is in an orphaned file (entire file unreachable from entrypoints) OR has zero references
- **High confidence (80-99%)**: Not reachable from entrypoints via call graph
- **Medium confidence (50-79%)**: Reachable from entrypoints but may still be unused
- **Low confidence (0-49%)**: Reachable from entrypoints, or is itself an entrypoint (0%)

**IMPORTANT**: Low confidence items are NOT necessarily safe! They need MORE verification:
- 0% confidence = detected as entrypoint (Flask route, Celery task) - usually KEEP
- Low confidence = reachable from entrypoints via call chain - check if actually called

## Your Task

Help the user review dead code items and determine:
- **DELETE**: Confirmed dead code, safe to remove
- **KEEP**: False positive, should not be removed
- **UNCERTAIN**: Needs more investigation

## Verification Strategy

**For HIGH confidence items (80-100%)**:
- These are likely truly dead - verify no dynamic/reflection usage
- Check if part of public API that external code might use
- If in orphaned file, likely safe to DELETE

**For LOW confidence items (0-49%)**:
- These are reachable from entrypoints - check if ACTUALLY called
- Read the calling functions to verify the call chain exists
- 0% confidence items are entrypoints - usually KEEP unless deprecated

**For ALL items**:
1. Read the source file containing the symbol
2. Search for usages in other files using grep/search
3. Check for dynamic patterns (getattr, importlib, string-based references)

## How to Work

1. Read `.openprune/results.json` to see ALL dead code candidates
2. Also read `.openprune/config.json` to understand detected frameworks
3. For EACH item, examine the actual source code at the file:line
4. **CRITICAL**: For low confidence items, read the files that supposedly call the symbol
5. Make a verdict and explain your reasoning
6. Update `.openprune/verified.json` with your verdicts

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
""")

    # Check if results.json exists and add summary
    results_path = project_path / ".openprune" / "results.json"
    if results_path.exists():
        import json

        try:
            with open(results_path) as f:
                results = json.load(f)

            dead_code = results.get("dead_code", [])
            orphaned_files = results.get("orphaned_files", [])

            # Categorize by confidence
            high_conf = [d for d in dead_code if d.get("confidence", 0) >= 80]
            medium_conf = [d for d in dead_code if 50 <= d.get("confidence", 0) < 80]
            low_conf = [d for d in dead_code if d.get("confidence", 0) < 50]

            prompt_parts.append(f"""
## Results Summary

- **Orphaned files**: {len(orphaned_files)} (entire files unreachable - high priority)
- **Total dead code candidates**: {len(dead_code)}
  - High confidence (80-100%): {len(high_conf)} items
  - Medium confidence (50-79%): {len(medium_conf)} items
  - Low confidence (0-49%): {len(low_conf)} items (need careful review!)

Start by reading `.openprune/results.json` to see the full list.
Then read the actual source files to verify each item.
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

    initial_task = """Start the dead code verification session.

1. First, read .openprune/results.json to see ALL dead code candidates
2. Review items across ALL confidence levels (not just high confidence!)
3. For each item:
   - Read the source file at the specified line
   - For low confidence items, trace the call chain from entrypoints
   - Determine: DELETE, KEEP, or UNCERTAIN

**Remember**: Low confidence (0-49%) items need MORE verification, not less!
They may be reachable from entrypoints but still unused.

Let's begin - show me a summary of items by confidence level."""

    return f"""{system_prompt}

---

## BEGIN SESSION

{initial_task}"""
