"""Prompt templates for LLM-driven dead code deletion."""

from pathlib import Path

DELETION_SYSTEM_PROMPT = """You are helping remove confirmed dead code from a Python project using OpenPrune.

## Context

OpenPrune has already detected and verified dead code items through both static
analysis (confidence scoring, reachability analysis) and LLM verification. The items you are
removing have been confirmed as safe to delete with verdict="delete".

## Files in .openprune/

- `verified.json` - Contains items with verdict="delete" that should be removed
- `removals.json` - Track your progress here (will be created/updated)

## Your Task

For each file containing DELETE-verdict items:
1. Read the source file
2. Remove the dead code symbols (functions, classes, variables, imports)
3. Clean up any imports that were ONLY used by the removed code
4. Collapse excessive blank lines (max 2 consecutive blank lines)
5. If a file becomes completely empty after removal, delete the file

## Rules

1. ONLY remove items listed in verified.json with verdict="delete"
2. Do NOT modify any code that is not in the delete list
3. When removing a function/class, also remove its decorators and docstring
4. When removing an import, check if other code in the same file still uses it
5. Preserve all comments not directly associated with removed code
6. Maintain proper Python formatting and indentation
7. If removing code would create syntax errors, keep the code and report the issue

## Parallelization

You can spawn agents to process multiple files in parallel for efficiency.
Group files by directory or process independent files concurrently.

## Progress Tracking

After processing all files, create .openprune/removals.json with a summary of what was done.
"""


def build_deletion_system_prompt(project_path: Path) -> str:
    """Build a system prompt with project-specific context for deletion.

    Args:
        project_path: Path to the project root

    Returns:
        Complete system prompt string
    """
    prompt_parts = [DELETION_SYSTEM_PROMPT]

    prompt_parts.append(f"""
## Project Context

- **Project Path**: {project_path}
""")

    # Add summary of items to delete from verified.json
    verified_path = project_path / ".openprune" / "verified.json"
    if verified_path.exists():
        import json

        try:
            with open(verified_path, encoding="utf-8") as f:
                data = json.load(f)

            items = data.get("verified_items", [])
            delete_items = [i for i in items if i.get("verdict") == "delete"]

            # Group by file
            by_file: dict[str, int] = {}
            for item in delete_items:
                f = item.get("file", "unknown")
                by_file[f] = by_file.get(f, 0) + 1

            prompt_parts.append(f"""
## Deletion Summary

- **Total items to delete**: {len(delete_items)}
- **Files affected**: {len(by_file)}

Process files one at a time or in parallel via agents. For each file, read it,
remove the dead code, and write back.
""")
        except (json.JSONDecodeError, OSError):
            pass

    return "\n".join(prompt_parts)


def build_deletion_combined_prompt(project_path: Path) -> str:
    """Build a combined system+initial prompt for LLMs without system prompt support.

    Args:
        project_path: Path to the project root

    Returns:
        Combined prompt string
    """
    system_prompt = build_deletion_system_prompt(project_path)

    initial_task = """Start the dead code deletion session.

1. Read .openprune/verified.json to get all items with verdict="delete"
2. Group items by file
3. For each file:
   - Read the source file
   - Remove all DELETE-verdict symbols (including decorators and docstrings)
   - Clean up orphaned imports
   - Write the modified file back
4. Create .openprune/removals.json tracking what was done

Begin by reading verified.json and listing the files to process."""

    return f"""{system_prompt}

---

## BEGIN SESSION

{initial_task}"""
