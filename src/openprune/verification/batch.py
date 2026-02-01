"""Non-interactive batch verification using LLM CLI."""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from openprune.models.verification import (
    LLMVerdict,
    VerificationResults,
    VerificationSummary,
    VerifiedItem,
)
from openprune.output.json_writer import load_results
from openprune.paths import get_results_path, get_verified_path

console = Console()


def run_batch_verification(
    project_path: Path,
    llm_tool: str = "claude",
    min_confidence: int = 70,
    timeout: int = 120,
) -> VerificationResults:
    """
    Run non-interactive batch verification of dead code items.

    This processes each item one-by-one using the LLM CLI in print mode,
    parsing the response to extract verdicts.

    Args:
        project_path: Path to the project root
        llm_tool: Name of the LLM CLI tool to use
        min_confidence: Minimum confidence threshold for verification
        timeout: Timeout in seconds per LLM call

    Returns:
        VerificationResults with all verified items
    """
    # Validate LLM tool exists
    if not shutil.which(llm_tool):
        raise RuntimeError(
            f"LLM CLI tool '{llm_tool}' not found in PATH. "
            f"Please install it or specify a different tool with --llm."
        )

    # Load analysis results
    results_path = get_results_path(project_path)
    if not results_path.exists():
        raise RuntimeError(f"Results file not found: {results_path}")

    results_data = load_results(results_path)
    dead_code = results_data.get("dead_code", [])

    # Filter by confidence
    candidates = [d for d in dead_code if d.get("confidence", 0) >= min_confidence]
    skipped = [d for d in dead_code if d.get("confidence", 0) < min_confidence]

    console.print(f"[green]{len(candidates)}[/] items to verify (confidence >= {min_confidence}%)")
    console.print(f"[dim]{len(skipped)} items skipped (below threshold)[/]\n")

    if not candidates:
        console.print("[yellow]No items to verify.[/]")
        return _build_empty_results(skipped, llm_tool, min_confidence)

    verified_items: list[VerifiedItem] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Verifying items...", total=len(candidates))

        for item_data in candidates:
            name = item_data.get("name", "unknown")
            progress.update(task, description=f"Verifying {name}...")

            # Build prompt for this item
            prompt = _build_item_prompt(item_data, project_path)

            # Execute LLM
            response = _execute_llm(llm_tool, prompt, project_path, timeout)

            # Parse response
            verdict, reasoning = _parse_response(response)

            # Create verified item
            verified_item = VerifiedItem(
                qualified_name=item_data.get("qualified_name", ""),
                name=name,
                type=item_data.get("type", "unknown"),
                file=Path(item_data.get("file", "")),
                line=item_data.get("line", 0),
                end_line=item_data.get("end_line"),
                original_confidence=item_data.get("confidence", 0),
                reasons=item_data.get("reasons", []),
                code_preview=item_data.get("code_preview"),
                verdict=verdict,
                llm_reasoning=reasoning,
                verified_at=datetime.now(),
            )

            verified_items.append(verified_item)
            progress.update(task, advance=1)

    # Build and save results
    results = _build_results(verified_items, skipped, llm_tool, min_confidence)

    output_path = get_verified_path(project_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results.to_dict(), f, indent=2)

    console.print(f"\n[green]Results saved to:[/] {output_path}")
    _display_summary(results)

    return results


def _build_item_prompt(item_data: dict, project_path: Path) -> str:
    """Build a verification prompt for a single item."""
    file_path = project_path / item_data.get("file", "")
    code_context = ""

    if file_path.exists():
        try:
            lines = file_path.read_text().split("\n")
            start_line = item_data.get("line", 1) - 1
            end_line = item_data.get("end_line") or item_data.get("line", 1)

            # Get surrounding context
            context_start = max(0, start_line - 5)
            context_end = min(len(lines), end_line + 5)

            code_lines = []
            for i in range(context_start, context_end):
                marker = ">>> " if start_line <= i < end_line else "    "
                code_lines.append(f"{i + 1:4d}{marker}{lines[i]}")

            code_context = "\n".join(code_lines)
        except OSError:
            code_context = "(Could not read file)"

    reasons = "\n".join(f"- {r}" for r in item_data.get("reasons", [])) or "- No specific reasons"

    return f"""Analyze this dead code candidate and determine if it can be safely deleted.

## Code Under Review

**Symbol**: `{item_data.get('qualified_name', 'unknown')}`
**Type**: {item_data.get('type', 'unknown')}
**File**: {item_data.get('file', 'unknown')}
**Line**: {item_data.get('line', 0)}
**Confidence**: {item_data.get('confidence', 0)}%

### Code Context:
```python
{code_context}
```

### Static Analysis Reasons:
{reasons}

## Your Task

Determine if this code is truly dead and safe to delete. Consider:
1. Dynamic invocation (getattr, reflection, string imports)
2. Public API external code might depend on
3. Framework patterns (decorators, magic methods)
4. Test/script usage not captured by static analysis

## Response Format

Respond with EXACTLY this format:

VERDICT: DELETE|KEEP|UNCERTAIN

REASONING: <Your explanation in 1-2 sentences>
"""


def _execute_llm(llm_tool: str, prompt: str, project_path: Path, timeout: int) -> str:
    """Execute LLM CLI and return response."""
    if llm_tool == "claude":
        cmd = ["claude", "--print", prompt]
    else:
        cmd = [llm_tool, prompt]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=project_path,
        )
        return result.stdout if result.returncode == 0 else f"ERROR: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "ERROR: LLM timed out"
    except Exception as e:
        return f"ERROR: {e}"


def _parse_response(response: str) -> tuple[LLMVerdict, str]:
    """Parse LLM response to extract verdict and reasoning."""
    import re

    response = response.strip()

    # Try structured format
    verdict_match = re.search(r"VERDICT:\s*(DELETE|KEEP|UNCERTAIN)", response, re.IGNORECASE)
    reasoning_match = re.search(
        r"REASONING:\s*(.+?)(?:$|VERDICT:)", response, re.DOTALL | re.IGNORECASE
    )

    if verdict_match:
        verdict_str = verdict_match.group(1).upper()
        verdict = LLMVerdict[verdict_str]
        reasoning = reasoning_match.group(1).strip() if reasoning_match else response
        return verdict, reasoning

    # Fallback: heuristic parsing
    response_lower = response.lower()

    delete_indicators = [
        "can be safely removed",
        "should be deleted",
        "is dead code",
        "safe to delete",
        "definitely unused",
    ]

    keep_indicators = [
        "should not be deleted",
        "is still used",
        "false positive",
        "do not remove",
        "may be called",
    ]

    for indicator in delete_indicators:
        if indicator in response_lower:
            return LLMVerdict.DELETE, response

    for indicator in keep_indicators:
        if indicator in response_lower:
            return LLMVerdict.KEEP, response

    return LLMVerdict.UNCERTAIN, response


def _build_results(
    verified_items: list[VerifiedItem],
    skipped_items: list[dict],
    llm_tool: str,
    min_confidence: int,
) -> VerificationResults:
    """Build VerificationResults from verified items."""
    summary = VerificationSummary(
        total_items=len(verified_items) + len(skipped_items),
        delete_count=sum(1 for i in verified_items if i.verdict == LLMVerdict.DELETE),
        keep_count=sum(1 for i in verified_items if i.verdict == LLMVerdict.KEEP),
        uncertain_count=sum(1 for i in verified_items if i.verdict == LLMVerdict.UNCERTAIN),
        skipped_count=len(skipped_items),
    )

    return VerificationResults(
        version="1.0",
        metadata={
            "verified_at": datetime.now().isoformat(),
            "llm_tool": llm_tool,
            "min_confidence": min_confidence,
            "mode": "batch",
        },
        summary=summary,
        verified_items=verified_items,
        skipped_items=skipped_items,
    )


def _build_empty_results(
    skipped_items: list[dict],
    llm_tool: str,
    min_confidence: int,
) -> VerificationResults:
    """Build empty results when nothing to verify."""
    return VerificationResults(
        version="1.0",
        metadata={
            "verified_at": datetime.now().isoformat(),
            "llm_tool": llm_tool,
            "min_confidence": min_confidence,
            "mode": "batch",
        },
        summary=VerificationSummary(skipped_count=len(skipped_items)),
        skipped_items=skipped_items,
    )


def _display_summary(results: VerificationResults) -> None:
    """Display verification summary."""
    from rich.panel import Panel

    if not results.summary:
        return

    s = results.summary
    console.print(
        Panel(
            f"[green]DELETE:[/] {s.delete_count} items\n"
            f"[yellow]KEEP:[/] {s.keep_count} items\n"
            f"[dim]UNCERTAIN:[/] {s.uncertain_count} items\n"
            f"[dim]SKIPPED:[/] {s.skipped_count} items",
            title="[bold]Verification Summary[/]",
            border_style="blue",
        )
    )
