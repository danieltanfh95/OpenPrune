"""Interactive LLM session launcher."""

import os
import shutil
from pathlib import Path

from openprune.verification.batch import (
    PRIORITY_P0,
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
)
from openprune.verification.prompts import build_combined_prompt, build_system_prompt

# Whitelist of allowed LLM CLI tools for security
ALLOWED_LLM_TOOLS = {"claude", "opencode", "kimi"}


def _validate_llm_tool(llm_tool: str) -> None:
    """Validate the LLM tool name for security.

    Args:
        llm_tool: Name of the LLM CLI tool

    Raises:
        ValueError: If the tool is not in the whitelist or contains path separators
    """
    if "/" in llm_tool or "\\" in llm_tool:
        raise ValueError("LLM tool name cannot contain path separators")
    if llm_tool not in ALLOWED_LLM_TOOLS:
        raise ValueError(
            f"Unsupported LLM tool: '{llm_tool}'. "
            f"Allowed tools: {', '.join(sorted(ALLOWED_LLM_TOOLS))}"
        )


def _format_tiers(tiers: set[int]) -> str:
    """Format tier set for display."""
    tier_names = []
    if PRIORITY_P0 in tiers:
        tier_names.append("P0")
    if PRIORITY_P1 in tiers:
        tier_names.append("P1")
    if PRIORITY_P2 in tiers:
        tier_names.append("P2")
    if PRIORITY_P3 in tiers:
        tier_names.append("P3")
    return ", ".join(tier_names) if tier_names else "none"


def launch_llm_session(
    project_path: Path,
    llm_tool: str = "claude",
    tiers: set[int] | None = None,
) -> None:
    """
    Launch an interactive LLM session with OpenPrune context.

    This exec's into the LLM CLI, replacing the current process.
    The LLM will have access to the project files and can read/write
    to .openprune/ directory.

    Args:
        project_path: Path to the project root
        llm_tool: Name of the LLM CLI tool to use (e.g., "claude")
        tiers: Set of priority tiers to verify (default: {PRIORITY_P0})

    Raises:
        RuntimeError: If the LLM CLI tool is not found in PATH
        ValueError: If the LLM tool is not in the allowed whitelist
    """
    # Default to P0 if no tiers specified
    selected_tiers = tiers if tiers is not None else {PRIORITY_P0}
    tiers_str = _format_tiers(selected_tiers)

    # Validate LLM tool is allowed (security check)
    _validate_llm_tool(llm_tool)

    # Validate LLM tool exists
    if not shutil.which(llm_tool):
        raise RuntimeError(
            f"LLM CLI tool '{llm_tool}' not found in PATH. "
            f"Please install it or specify a different tool with --llm."
        )

    # Build the system prompt with project context
    system_prompt = build_system_prompt(project_path)

    # Build command based on LLM tool
    cmd = _build_llm_command(llm_tool, system_prompt, project_path, tiers_str)

    # Change to project directory so LLM can access files
    os.chdir(project_path)

    # exec into LLM CLI (replaces current process)
    os.execvp(cmd[0], cmd)


def _build_llm_command(
    llm_tool: str,
    system_prompt: str,
    project_path: Path,
    tiers_str: str,
) -> list[str]:
    """
    Build the LLM CLI command with appropriate flags.

    Args:
        llm_tool: Name of the LLM CLI tool
        system_prompt: System prompt to pass to the LLM
        project_path: Path to the project (for tool-specific config)
        tiers_str: String representation of selected tiers (e.g., "P0, P1")

    Returns:
        List of command arguments
    """
    # Initial prompt to kick off the verification session
    initial_prompt = f"""Start the dead code verification session.

Read .openprune/results.json to see the dead code candidates, then help me review items in tiers: {tiers_str}.

For each item, examine the source code and determine if it's truly dead (DELETE), a false positive (KEEP), or needs more investigation (UNCERTAIN).

Let's begin - show me the highest priority items first."""

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

    elif llm_tool == "opencode":
        # opencode: use project path as positional, --prompt for initial message
        # No system prompt support - include context in initial prompt
        combined_prompt = build_combined_prompt(project_path)
        return [
            "opencode",
            str(project_path),
            "--prompt",
            combined_prompt,
        ]

    elif llm_tool == "kimi":
        # kimi: use -w for working dir, -p for initial prompt
        # No system prompt support - include context in initial prompt
        combined_prompt = build_combined_prompt(project_path)
        return [
            "kimi",
            "-w",
            str(project_path),
            "-p",
            combined_prompt,
        ]

    else:
        # Generic fallback - just launch the tool with prompt
        return [llm_tool, initial_prompt]
