"""Non-interactive oneshot verification using LLM CLI."""

import json
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from openprune.models.verification import (
    LLMVerdict,
    VerificationResults,
    VerificationSummary,
    VerifiedItem,
)
from openprune.output.json_writer import load_results
from openprune.paths import get_results_path, get_verified_path
from openprune.verification.prompts import build_system_prompt

console = Console()

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


def _safe_resolve(base_path: Path, user_path: str) -> Path | None:
    """Safely resolve a path ensuring it stays within base directory.

    Args:
        base_path: The base directory that paths must stay within
        user_path: User-provided path to resolve

    Returns:
        Resolved Path if safe, None if path escapes base directory
    """
    try:
        full_path = (base_path / user_path).resolve()
        if not full_path.is_relative_to(base_path.resolve()):
            return None
        return full_path
    except (ValueError, OSError):
        return None


def run_batch_verification(
    project_path: Path,
    llm_tool: str = "claude",
    min_confidence: int = 70,
    timeout: int = 600,  # Longer timeout for full analysis
    include_orphaned: bool = False,
) -> VerificationResults:
    """
    Run single-session oneshot verification of all dead code items.

    This sends a single comprehensive prompt to the LLM with all items
    and file contents, then parses the complete response.

    By default, items from orphaned files (100% confidence, entire file
    unreachable) are automatically marked as DELETE without LLM verification,
    since they are almost certainly dead code.

    Args:
        project_path: Path to the project root
        llm_tool: Name of the LLM CLI tool to use
        min_confidence: Minimum confidence threshold for verification
        timeout: Timeout in seconds for the LLM call
        include_orphaned: If True, include orphaned file items for LLM verification.
                         If False (default), auto-mark them as DELETE.

    Returns:
        VerificationResults with all verified items
    """
    # Validate LLM tool is allowed (security check)
    _validate_llm_tool(llm_tool)

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
    orphaned_files = results_data.get("orphaned_files", [])

    # Build set of orphaned file paths for quick lookup
    orphaned_file_paths = {of.get("file") for of in orphaned_files}

    # Separate orphaned items from non-orphaned
    orphaned_items = []
    non_orphaned_items = []
    for item in dead_code:
        item_file = item.get("file", "")
        reasons = item.get("reasons", [])
        is_orphaned = (
            item_file in orphaned_file_paths
            or any("Entire file is unreachable" in r for r in reasons)
        )
        if is_orphaned:
            orphaned_items.append(item)
        else:
            non_orphaned_items.append(item)

    # Filter by confidence (only non-orphaned items go through this)
    if include_orphaned:
        # Include everything above threshold
        candidates = [d for d in dead_code if d.get("confidence", 0) >= min_confidence]
        auto_verified_orphans: list[VerifiedItem] = []
    else:
        # Skip orphaned items - auto-verify them as DELETE
        candidates = [d for d in non_orphaned_items if d.get("confidence", 0) >= min_confidence]
        auto_verified_orphans = _auto_verify_orphans(orphaned_items)

    skipped = [d for d in dead_code if d.get("confidence", 0) < min_confidence]

    console.print(Panel.fit("[bold blue]OpenPrune - Oneshot Verification[/]"))
    console.print(f"\n[dim]LLM:[/] {llm_tool}")
    console.print(f"[dim]Min confidence:[/] {min_confidence}%")
    console.print(f"[green]{len(candidates)}[/] items to verify")
    console.print(f"[dim]{len(skipped)} items below threshold[/]")
    if orphaned_files:
        if include_orphaned:
            console.print(f"[yellow]{len(orphaned_files)} orphaned files (included for verification)[/]")
        else:
            console.print(f"[green]{len(orphaned_items)} orphaned items auto-marked as DELETE[/]")
            console.print(f"[dim](use --include-orphaned to verify these with LLM)[/]")

    if not candidates:
        if auto_verified_orphans:
            console.print(f"\n[yellow]No items to verify via LLM. {len(auto_verified_orphans)} orphaned items auto-verified.[/]")
            return _build_results(auto_verified_orphans, skipped, llm_tool, min_confidence)
        console.print("\n[yellow]No items to verify.[/]")
        return _build_empty_results(skipped, llm_tool, min_confidence)

    # Build comprehensive oneshot prompt
    console.print("\n[dim]Building prompt with file contents...[/]")
    prompt = _build_oneshot_prompt(project_path, candidates, orphaned_files, min_confidence)

    # Execute single LLM call
    console.print(f"[dim]Sending to {llm_tool} (timeout: {timeout}s)...[/]\n")
    response = _execute_llm_oneshot(llm_tool, prompt, project_path, timeout)

    if response.startswith("ERROR:"):
        console.print(f"[red]{response}[/]")
        raise RuntimeError(response)

    # Parse the complete response
    verified_items = _parse_oneshot_response(response, candidates)

    # Merge auto-verified orphans with LLM-verified items
    all_verified_items = auto_verified_orphans + verified_items

    # Build and save results
    results = _build_results(all_verified_items, skipped, llm_tool, min_confidence)

    output_path = get_verified_path(project_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results.to_dict(), f, indent=2)

    console.print(f"\n[green]Results saved to:[/] {output_path}")
    _display_summary(results)

    return results


def _build_oneshot_prompt(
    project_path: Path,
    candidates: list[dict],
    orphaned_files: list[dict],
    min_confidence: int,
) -> str:
    """Build a comprehensive prompt for single-session verification."""
    prompt_parts = [
        build_system_prompt(project_path, min_confidence),
        "\n---\n\n",
        "# Verification Session\n\n",
    ]

    # Add orphaned files section if any
    if orphaned_files:
        prompt_parts.append("## Orphaned Files (100% confidence - entire file unreachable)\n\n")
        for of in orphaned_files:
            prompt_parts.append(f"- **{of.get('file', 'unknown')}**: {of.get('symbols', 0)} symbols, ")
            prompt_parts.append(f"{of.get('lines', 0)} lines\n")
        prompt_parts.append("\n")

    # Group candidates by file for context
    prompt_parts.append("## Items to Verify\n\n")

    by_file: dict[str, list[dict]] = defaultdict(list)
    for item in candidates:
        by_file[item.get("file", "")].append(item)

    for file_path, items in by_file.items():
        full_path = _safe_resolve(project_path, file_path)
        prompt_parts.append(f"### {file_path}\n\n")

        # Include file contents (skip if path traversal detected)
        if full_path is None:
            prompt_parts.append("*(Skipped: path outside project directory)*\n\n")
        elif full_path.exists():
            try:
                content = full_path.read_text()
                # Add line numbers for reference
                numbered_lines = []
                for i, line in enumerate(content.split("\n"), 1):
                    numbered_lines.append(f"{i:4d} | {line}")
                prompt_parts.append("```python\n")
                prompt_parts.append("\n".join(numbered_lines))
                prompt_parts.append("\n```\n\n")
            except OSError:
                prompt_parts.append("*(Could not read file)*\n\n")

        # List items in this file
        prompt_parts.append("**Dead code candidates in this file:**\n\n")
        for item in items:
            confidence = item.get("confidence", 0)
            reasons = ", ".join(item.get("reasons", [])) or "No specific reason"

            # Add confidence level indicator
            if confidence == 0:
                conf_label = "[ENTRYPOINT]"
            elif confidence < 50:
                conf_label = "[LOW - needs verification]"
            elif confidence < 80:
                conf_label = "[MEDIUM]"
            else:
                conf_label = "[HIGH]"

            prompt_parts.append(
                f"- `{item.get('qualified_name', item.get('name', 'unknown'))}` "
                f"(line {item.get('line', 0)}, {confidence}% {conf_label})\n"
                f"  - Type: {item.get('type', 'unknown')}\n"
                f"  - Reasons: {reasons}\n\n"
            )

    # Add task instructions
    prompt_parts.append("""
---

## Your Task

Review ALL items listed above and provide verdicts.

**IMPORTANT REMINDERS**:
1. **0% confidence = ENTRYPOINT** (Flask route, Celery task, etc.) - Usually **KEEP**
2. **Low confidence (1-49%)** = Reachable from entrypoints - Check if ACTUALLY called
3. **High confidence (80-100%)** = Likely truly dead - Verify no dynamic usage
4. **100% confidence in orphaned files** = Entire file unreachable - Usually **DELETE**

For EACH item, determine:
- **DELETE**: Confirmed dead code, safe to remove
- **KEEP**: False positive, should not be removed (entrypoint, dynamic usage, public API)
- **UNCERTAIN**: Needs more investigation

## Response Format

Return a JSON array with your verdicts. Include ALL items from the list above.

```json
{
  "verified_items": [
    {
      "qualified_name": "module.function_name",
      "verdict": "DELETE",
      "reasoning": "Brief explanation why this is dead code"
    },
    {
      "qualified_name": "app.index",
      "verdict": "KEEP",
      "reasoning": "Flask route entrypoint, will be called by web requests"
    }
  ]
}
```

Now analyze all items and return the complete JSON:
""")

    return "".join(prompt_parts)


def _execute_llm_oneshot(
    llm_tool: str,
    prompt: str,
    project_path: Path,
    timeout: int,
) -> str:
    """Execute LLM CLI with oneshot prompt and return response."""
    if llm_tool == "claude":
        # Use --print for non-interactive mode
        cmd = ["claude", "--print", prompt]
    elif llm_tool == "opencode":
        cmd = ["opencode", "run", prompt]
    elif llm_tool == "kimi":
        cmd = ["kimi", "--print", "-p", prompt]
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
        if result.returncode != 0:
            return f"ERROR: {result.stderr}"
        return result.stdout
    except subprocess.TimeoutExpired:
        return f"ERROR: LLM timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


def _parse_oneshot_response(
    response: str,
    candidates: list[dict],
) -> list[VerifiedItem]:
    """Parse the oneshot response to extract all verdicts."""
    verified_items: list[VerifiedItem] = []

    # Try to extract JSON from response
    json_match = re.search(r"\{[\s\S]*\"verified_items\"[\s\S]*\}", response)

    if json_match:
        try:
            data = json.loads(json_match.group())
            items_data = data.get("verified_items", [])

            # Build lookup for candidates
            candidate_lookup = {}
            for c in candidates:
                qname = c.get("qualified_name", "")
                name = c.get("name", "")
                candidate_lookup[qname] = c
                candidate_lookup[name] = c

            for item_data in items_data:
                qname = item_data.get("qualified_name", "")
                verdict_str = item_data.get("verdict", "UNCERTAIN").upper()
                reasoning = item_data.get("reasoning", item_data.get("llm_reasoning", ""))

                # Map to original candidate
                original = candidate_lookup.get(qname, {})

                try:
                    verdict = LLMVerdict[verdict_str]
                except KeyError:
                    verdict = LLMVerdict.UNCERTAIN

                verified_items.append(
                    VerifiedItem(
                        qualified_name=qname,
                        name=original.get("name", qname.split(".")[-1]),
                        type=original.get("type", "unknown"),
                        file=Path(original.get("file", "")),
                        line=original.get("line", 0),
                        end_line=original.get("end_line"),
                        original_confidence=original.get("confidence", 0),
                        reasons=original.get("reasons", []),
                        code_preview=original.get("code_preview"),
                        verdict=verdict,
                        llm_reasoning=reasoning,
                        verified_at=datetime.now(),
                    )
                )

            return verified_items

        except json.JSONDecodeError:
            console.print("[yellow]Warning: Could not parse JSON, falling back to heuristic parsing[/]")

    # Fallback: parse individual verdicts from text
    for candidate in candidates:
        qname = candidate.get("qualified_name", "")
        name = candidate.get("name", "")

        verdict, reasoning = _parse_item_from_text(response, qname, name)

        verified_items.append(
            VerifiedItem(
                qualified_name=qname,
                name=name,
                type=candidate.get("type", "unknown"),
                file=Path(candidate.get("file", "")),
                line=candidate.get("line", 0),
                end_line=candidate.get("end_line"),
                original_confidence=candidate.get("confidence", 0),
                reasons=candidate.get("reasons", []),
                code_preview=candidate.get("code_preview"),
                verdict=verdict,
                llm_reasoning=reasoning,
                verified_at=datetime.now(),
            )
        )

    return verified_items


def _parse_item_from_text(
    response: str,
    qualified_name: str,
    name: str,
) -> tuple[LLMVerdict, str]:
    """Parse verdict for a specific item from unstructured text."""
    # Look for mentions of this item
    patterns = [
        rf"`?{re.escape(qualified_name)}`?.*?(DELETE|KEEP|UNCERTAIN)",
        rf"`?{re.escape(name)}`?.*?(DELETE|KEEP|UNCERTAIN)",
        rf"(DELETE|KEEP|UNCERTAIN).*?`?{re.escape(qualified_name)}`?",
        rf"(DELETE|KEEP|UNCERTAIN).*?`?{re.escape(name)}`?",
    ]

    for pattern in patterns:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            verdict_str = match.group(1).upper()
            try:
                return LLMVerdict[verdict_str], f"Extracted from response for {name}"
            except KeyError:
                pass

    # Default to uncertain
    return LLMVerdict.UNCERTAIN, "Could not determine verdict from response"


def _auto_verify_orphans(orphaned_items: list[dict]) -> list[VerifiedItem]:
    """Auto-verify orphaned items as DELETE without LLM verification.

    Items from orphaned files (100% confidence, entire file unreachable) are
    almost certainly dead code, so we can skip LLM verification for them.
    """
    verified: list[VerifiedItem] = []
    for item in orphaned_items:
        verified.append(
            VerifiedItem(
                qualified_name=item.get("qualified_name", ""),
                name=item.get("name", ""),
                type=item.get("type", "unknown"),
                file=Path(item.get("file", "")),
                line=item.get("line", 0),
                end_line=item.get("end_line"),
                original_confidence=item.get("confidence", 100),
                reasons=item.get("reasons", []),
                code_preview=item.get("code_preview"),
                verdict=LLMVerdict.DELETE,
                llm_reasoning="Auto-verified: Entire file is unreachable from any entrypoint",
                verified_at=datetime.now(),
            )
        )
    return verified


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
            "mode": "oneshot",
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
            "mode": "oneshot",
        },
        summary=VerificationSummary(skipped_count=len(skipped_items)),
        skipped_items=skipped_items,
    )


def _display_summary(results: VerificationResults) -> None:
    """Display verification summary."""
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
