"""Non-interactive oneshot deletion using LLM CLI."""

import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from openprune.models.deletion import (
    DeletionResults,
    DeletionSummary,
)
from openprune.models.verification import LLMVerdict, VerifiedItem
from openprune.output.json_writer import load_verification_results
from openprune.paths import ensure_openprune_dir, get_removals_path, get_verified_path
from openprune.verification.batch import (
    _execute_llm_oneshot,
    _safe_resolve,
    _sanitize_content_for_prompt,
    _validate_llm_tool,
)

console = Console()


def _get_git_head(project_path: Path) -> str | None:
    """Get the current git HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=project_path,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _check_git_clean(project_path: Path) -> tuple[bool, str]:
    """Check if git working directory is clean.

    Returns:
        Tuple of (is_clean, message)
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            cwd=project_path,
            timeout=10,
        )
        if result.returncode != 0:
            return False, "Not a git repository"
        if result.stdout.strip():
            return False, f"Uncommitted changes:\n{result.stdout[:500]}"
        return True, ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "Could not run git status"


def _load_delete_items(project_path: Path) -> list[VerifiedItem]:
    """Load verified items with DELETE verdict."""
    verified_path = get_verified_path(project_path)
    if not verified_path.exists():
        raise FileNotFoundError(f"No verified.json found at {verified_path}")

    data = load_verification_results(verified_path)
    items = []
    for item_data in data.get("verified_items", []):
        item = VerifiedItem.from_dict(item_data)
        if item.verdict == LLMVerdict.DELETE:
            items.append(item)

    return items


def _group_by_file(
    items: list[VerifiedItem],
    project_path: Path,
) -> dict[str, list[VerifiedItem]]:
    """Group items by their file path (relative to project root)."""
    by_file: dict[str, list[VerifiedItem]] = defaultdict(list)
    project_str = str(project_path)

    for item in items:
        file_str = str(item.file)
        if file_str.startswith(project_str):
            rel_path = file_str[len(project_str):].lstrip("/")
        else:
            rel_path = file_str
        by_file[rel_path].append(item)

    return dict(by_file)


def _build_oneshot_prompt(
    project_path: Path,
    by_file: dict[str, list[VerifiedItem]],
) -> str:
    """Build a comprehensive single-session prompt for deletion.

    Lists all files and their DELETE items with full file contents,
    then instructs the LLM to process everything.
    """
    total_items = sum(len(v) for v in by_file.values())

    prompt_parts = [
        "# Dead Code Deletion Session\n\n",
        "You are removing confirmed dead code from a Python project. "
        "All items below have been verified as safe to delete.\n\n",
        f"**Files to process**: {len(by_file)}\n",
        f"**Total items to delete**: {total_items}\n\n",
        "---\n\n",
    ]

    for file_path, items in sorted(by_file.items()):
        full_path = _safe_resolve(project_path, file_path)
        prompt_parts.append(f"## {file_path}\n\n")

        # Include file contents
        if full_path is None:
            prompt_parts.append("*(Skipped: path outside project directory)*\n\n")
        elif full_path.exists():
            try:
                raw_content = full_path.read_text()
                content, _warnings = _sanitize_content_for_prompt(raw_content, file_path)
                numbered_lines = []
                for i, line in enumerate(content.split("\n"), 1):
                    numbered_lines.append(f"{i:4d} | {line}")
                prompt_parts.append("```python\n")
                prompt_parts.append("\n".join(numbered_lines))
                prompt_parts.append("\n```\n\n")
            except OSError:
                prompt_parts.append("*(Could not read file)*\n\n")

        # List items to delete
        prompt_parts.append("**Symbols to remove:**\n\n")
        for item in items:
            line_info = f"line {item.line}"
            if item.end_line:
                line_info = f"lines {item.line}-{item.end_line}"
            prompt_parts.append(
                f"- `{item.qualified_name}` ({line_info}, type: {item.type})\n"
            )
        prompt_parts.append("\n")

    prompt_parts.append("""---

## Your Task

For EACH file listed above:
1. Read the source file
2. Remove all listed symbols (including decorators and docstrings)
3. Remove imports that are ONLY used by the removed code
4. Collapse excess blank lines (max 2 consecutive)
5. Write the modified file back
6. If a file becomes empty after removal, delete it

## Rules

- ONLY remove items listed above — do NOT modify any other code
- When removing a function/class, remove its decorators and docstring too
- When removing an import, check if other code in the same file still uses it
- Maintain proper Python formatting and indentation

## Parallelization

You can spawn agents to process multiple files in parallel for efficiency.
Group files by directory or process independent files concurrently.

## Progress

Report progress as you go. When done, create `.openprune/removals.json` with:

```json
{
  "version": "1.0",
  "metadata": {"deleted_at": "<ISO timestamp>", "llm_tool": "claude"},
  "summary": {
    "total_items": <n>,
    "applied": <n>,
    "skipped": <n>,
    "failed": <n>,
    "files_modified": <n>,
    "files_deleted": <n>,
    "total_lines_removed": <n>
  }
}
```

Begin processing now.
""")

    return "".join(prompt_parts)


def _save_results(results: DeletionResults, project_path: Path) -> None:
    """Save deletion results to removals.json."""
    ensure_openprune_dir(project_path)
    output_path = get_removals_path(project_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results.to_dict(), f, indent=2)


def _display_dry_run(by_file: dict[str, list[VerifiedItem]]) -> None:
    """Display dry-run preview of what would be deleted."""
    table = Table(title="Files to Process")
    table.add_column("File", style="cyan")
    table.add_column("Items", justify="right")
    table.add_column("Types")

    for file_path, items in sorted(by_file.items()):
        types = ", ".join(sorted({i.type for i in items}))
        table.add_row(file_path, str(len(items)), types)

    console.print(table)

    total = sum(len(v) for v in by_file.values())
    console.print(f"\n[bold]Total:[/] {total} items across {len(by_file)} files")
    console.print("[dim]Use without --dry-run to execute deletion[/]")


def run_batch_deletion(
    project_path: Path,
    llm_tool: str = "claude",
    timeout: int = 600,
    dry_run: bool = False,
    force: bool = False,
) -> DeletionResults | None:
    """Run LLM-driven batch deletion of verified dead code.

    Builds a single comprehensive prompt and sends it to the LLM in one session,
    mirroring the verify command's oneshot approach.

    Args:
        project_path: Path to the project root
        llm_tool: LLM CLI tool to use
        timeout: Timeout in seconds for the LLM call
        dry_run: Preview without modifying files
        force: Skip git clean check

    Returns:
        DeletionResults if dry_run, None otherwise (LLM handles everything)
    """
    _validate_llm_tool(llm_tool)

    # Safety check: require clean git state
    if not force and not dry_run:
        is_clean, msg = _check_git_clean(project_path)
        if not is_clean:
            raise RuntimeError(
                f"Git working tree is not clean. {msg}\n"
                "Commit or stash your changes first, or use --force to skip this check."
            )

    # Load DELETE items from verified.json
    delete_items = _load_delete_items(project_path)
    if not delete_items:
        console.print("[yellow]No items with DELETE verdict found in verified.json[/]")
        raise RuntimeError("No items to delete")

    # Group by file
    by_file = _group_by_file(delete_items, project_path)

    console.print(Panel.fit("[bold blue]OpenPrune - Dead Code Deletion[/]"))
    console.print(f"\n[dim]LLM tool:[/] {llm_tool}")
    console.print(f"[dim]Mode:[/] {'dry-run' if dry_run else 'auto'}")
    console.print(f"[dim]Items to delete:[/] {len(delete_items)}")
    console.print(f"[dim]Files affected:[/] {len(by_file)}\n")

    if dry_run:
        _display_dry_run(by_file)
        return DeletionResults(
            metadata={
                "deleted_at": datetime.now(timezone.utc).isoformat(),
                "llm_tool": llm_tool,
                "mode": "dry_run",
            },
            git_commit_before=_get_git_head(project_path),
            summary=DeletionSummary(total_items=len(delete_items)),
        )

    # Build single comprehensive prompt
    prompt = _build_oneshot_prompt(project_path, by_file)

    console.print(
        f"Sending {len(delete_items)} items across "
        f"{len(by_file)} files to {llm_tool}...\n"
    )

    # Execute LLM in single session
    response = _execute_llm_oneshot(llm_tool, prompt, project_path, timeout)

    if response.startswith("ERROR:"):
        console.print(f"[red]{response}[/]")
        raise RuntimeError(response)

    console.print("[green]LLM session complete.[/]")
    console.print("\n[dim]To undo all changes:[/] git checkout .")

    return None
