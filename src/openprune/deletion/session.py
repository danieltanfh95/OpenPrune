"""Interactive LLM session launcher for dead code deletion."""

import json
import os
import shutil
from pathlib import Path

from openprune.deletion.prompts import (
    build_deletion_combined_prompt,
    build_deletion_system_prompt,
)
from openprune.models.verification import LLMVerdict, VerifiedItem
from openprune.output.json_writer import load_verification_results
from openprune.paths import ensure_openprune_dir, get_verified_path
from openprune.verification.batch import _validate_llm_tool


def _generate_delete_plan(project_path: Path) -> Path:
    """Pre-generate a compact delete plan grouped by file.

    Creates .openprune/delete_plan.json with only the fields the LLM needs,
    grouped by file. This avoids the LLM having to parse the large
    verified.json and keeps context usage minimal.

    Returns:
        Path to the generated plan file.
    """
    verified_path = get_verified_path(project_path)
    data = load_verification_results(verified_path)

    # Group delete items by file, keeping only essential fields
    by_file: dict[str, list[dict]] = {}
    for item_data in data.get("verified_items", []):
        item = VerifiedItem.from_dict(item_data)
        if item.verdict != LLMVerdict.DELETE:
            continue

        file_str = str(item.file)
        if file_str not in by_file:
            by_file[file_str] = []

        by_file[file_str].append({
            "name": item.name,
            "qualified_name": item.qualified_name,
            "type": item.type,
            "line": item.line,
        })

    plan = {
        "total_items": sum(len(v) for v in by_file.values()),
        "total_files": len(by_file),
        "files": by_file,
    }

    ensure_openprune_dir(project_path)
    plan_path = project_path / ".openprune" / "delete_plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)

    return plan_path


def launch_deletion_session(
    project_path: Path,
    llm_tool: str = "claude",
) -> None:
    """Launch an interactive LLM session for dead code deletion.

    Pre-generates a compact delete_plan.json grouped by file, then
    exec's into the LLM CLI. The LLM reads items per-file on demand
    to avoid flooding its context window.

    Args:
        project_path: Path to the project root
        llm_tool: Name of the LLM CLI tool to use (e.g., "claude")

    Raises:
        RuntimeError: If the LLM CLI tool is not found in PATH
        ValueError: If the LLM tool is not in the allowed whitelist
    """
    # Validate LLM tool is allowed (security check)
    _validate_llm_tool(llm_tool)

    # Validate LLM tool exists
    if not shutil.which(llm_tool):
        raise RuntimeError(
            f"LLM CLI tool '{llm_tool}' not found in PATH. "
            f"Please install it or specify a different tool with --llm."
        )

    # Pre-generate compact plan file so the LLM doesn't need to parse
    # the full verified.json (which can be very large)
    _generate_delete_plan(project_path)

    # Build the system prompt with project context
    system_prompt = build_deletion_system_prompt(project_path)

    # Build command based on LLM tool
    cmd = _build_deletion_command(llm_tool, system_prompt, project_path)

    # Change to project directory so LLM can access files
    os.chdir(project_path)

    # exec into LLM CLI (replaces current process)
    os.execvp(cmd[0], cmd)


def _build_deletion_command(
    llm_tool: str,
    system_prompt: str,
    project_path: Path,
) -> list[str]:
    """Build the LLM CLI command for deletion session.

    Args:
        llm_tool: Name of the LLM CLI tool
        system_prompt: System prompt to pass to the LLM
        project_path: Path to the project

    Returns:
        List of command arguments
    """
    initial_prompt = """Start the dead code deletion session.

A pre-generated plan is at .openprune/delete_plan.json with items grouped by file.

Step 1: Get the list of files to process:
  jq '.files | keys[]' .openprune/delete_plan.json

Step 2: For each file, get its items:
  jq '.files["/path/to/file.py"]' .openprune/delete_plan.json

Step 3: Read the source file, remove the listed symbols, write it back.

Spawn agents to process multiple files in parallel. \
Each agent should handle a batch of files independently.

Begin now."""

    if llm_tool == "claude":
        return [
            "claude",
            "--system-prompt",
            system_prompt,
            "--allowedTools",
            "Read,Write,Edit,Bash",
            initial_prompt,
        ]

    elif llm_tool == "opencode":
        combined_prompt = build_deletion_combined_prompt(project_path)
        return [
            "opencode",
            str(project_path),
            "--prompt",
            combined_prompt,
        ]

    elif llm_tool == "kimi":
        combined_prompt = build_deletion_combined_prompt(project_path)
        return [
            "kimi",
            "-w",
            str(project_path),
            "-p",
            combined_prompt,
        ]

    else:
        return [llm_tool, initial_prompt]
