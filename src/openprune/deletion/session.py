"""Interactive LLM session launcher for dead code deletion."""

import os
import shutil
from pathlib import Path

from openprune.deletion.prompts import (
    build_deletion_combined_prompt,
    build_deletion_system_prompt,
)
from openprune.verification.batch import _validate_llm_tool


def launch_deletion_session(
    project_path: Path,
    llm_tool: str = "claude",
) -> None:
    """Launch an interactive LLM session for dead code deletion.

    This exec's into the LLM CLI, replacing the current process.
    The LLM will have access to the project files and can read/write
    to .openprune/ directory.

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

Read .openprune/verified.json to get all items with verdict="delete".
Group them by file, then process each file:
1. Read the source file
2. Remove all DELETE-verdict symbols (including decorators and docstrings)
3. Clean up orphaned imports
4. Write the modified file back
5. Track progress in .openprune/removals.json

Begin by reading verified.json and listing the files to process."""

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
