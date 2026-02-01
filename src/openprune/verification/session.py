"""Interactive LLM session launcher."""

import os
import shutil
from pathlib import Path

from openprune.verification.prompts import build_system_prompt


def launch_llm_session(
    project_path: Path,
    llm_tool: str = "claude",
    min_confidence: int = 70,
) -> None:
    """
    Launch an interactive LLM session with OpenPrune context.

    This exec's into the LLM CLI, replacing the current process.
    The LLM will have access to the project files and can read/write
    to .openprune/ directory.

    Args:
        project_path: Path to the project root
        llm_tool: Name of the LLM CLI tool to use (e.g., "claude")
        min_confidence: Minimum confidence threshold for verification

    Raises:
        RuntimeError: If the LLM CLI tool is not found in PATH
    """
    # Validate LLM tool exists
    if not shutil.which(llm_tool):
        raise RuntimeError(
            f"LLM CLI tool '{llm_tool}' not found in PATH. "
            f"Please install it or specify a different tool with --llm."
        )

    # Build the system prompt with project context
    system_prompt = build_system_prompt(project_path, min_confidence)

    # Build command based on LLM tool
    cmd = _build_llm_command(llm_tool, system_prompt, project_path, min_confidence)

    # Change to project directory so LLM can access files
    os.chdir(project_path)

    # exec into LLM CLI (replaces current process)
    os.execvp(cmd[0], cmd)


def _build_llm_command(
    llm_tool: str,
    system_prompt: str,
    project_path: Path,
    min_confidence: int,
) -> list[str]:
    """
    Build the LLM CLI command with appropriate flags.

    Args:
        llm_tool: Name of the LLM CLI tool
        system_prompt: System prompt to pass to the LLM
        project_path: Path to the project (for tool-specific config)
        min_confidence: Minimum confidence threshold

    Returns:
        List of command arguments
    """
    # Initial prompt to kick off the verification session
    initial_prompt = f"""Start the dead code verification session.

Read .openprune/results.json to see the dead code candidates, then help me review items with confidence >= {min_confidence}%.

For each item, examine the source code and determine if it's truly dead (DELETE), a false positive (KEEP), or needs more investigation (UNCERTAIN).

Let's begin - show me the high-confidence items first."""

    if llm_tool == "claude":
        # claude CLI supports --system-prompt, --allowedTools, and positional prompt
        return [
            "claude",
            "--system-prompt",
            system_prompt,
            "--allowedTools",
            "Read,Write,Edit,Bash",
            initial_prompt,
        ]

    else:
        # Generic fallback - just launch the tool with prompt
        return [llm_tool, initial_prompt]
