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

# Priority levels for LLM verification (lower = higher priority)
PRIORITY_P0 = 0  # Medium confidence (50-79%) non-imports - highest LLM value
PRIORITY_P1 = 1  # High confidence (80-99%) non-imports
PRIORITY_P2 = 2  # High confidence (80-99%) imports - usually true positives
PRIORITY_P3 = 3  # 100% confidence - auto-delete, no LLM needed
PRIORITY_SKIP = 4  # Low confidence (<50) - likely used, skip


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


def _assign_priority(item: dict) -> int:
    """Assign verification priority (lower = higher priority).

    Priority order:
    - P0: Medium confidence (50-79%) non-imports - highest false positive risk
    - P1: High confidence (80-99%) non-imports - medium risk
    - P2: High confidence (80-99%) imports - usually true positives
    - P3: 100% confidence - auto-delete, no LLM needed
    - SKIP: Low confidence (<50) - likely used
    """
    conf = item.get("confidence", 0)
    symbol_type = item.get("type", "")
    # Type field values are like "unused_import", "unused_function", etc.
    is_import = "import" in symbol_type

    if conf == 100:
        return PRIORITY_P3
    elif 80 <= conf <= 99:
        return PRIORITY_P2 if is_import else PRIORITY_P1
    elif 50 <= conf <= 79:
        return PRIORITY_P0
    else:
        return PRIORITY_SKIP


def _sort_by_priority(items: list[dict]) -> list[dict]:
    """Sort items by verification priority (highest priority first).

    Returns items sorted with P0 first, then P1, P2, etc.
    Within same priority, maintains original order.
    """
    return sorted(items, key=_assign_priority)


def _get_priority_label(priority: int) -> str:
    """Get human-readable priority label."""
    labels = {
        PRIORITY_P0: "P0 (Medium conf, highest LLM value)",
        PRIORITY_P1: "P1 (High conf non-import)",
        PRIORITY_P2: "P2 (High conf import)",
        PRIORITY_P3: "P3 (100% conf, auto-delete)",
        PRIORITY_SKIP: "SKIP (Low conf, likely used)",
    }
    return labels.get(priority, f"P{priority}")


def _collapse_orphaned_files(
    items: list[dict],
    orphaned_file_paths: set[str],
) -> tuple[list[dict], list[dict]]:
    """Collapse orphaned file items to file-level entries.

    Instead of listing each symbol from an orphaned file separately,
    collapse them to single entries like:
    "credentials_enc.py (entire file unreachable, 10 symbols)"

    Args:
        items: All dead code items
        orphaned_file_paths: Set of file paths marked as orphaned

    Returns:
        tuple: (non_orphaned_items, collapsed_file_entries)
    """
    orphaned_by_file: dict[str, list[dict]] = {}
    non_orphaned: list[dict] = []

    for item in items:
        item_file = item.get("file", "")
        reasons = item.get("reasons", [])
        is_orphaned = (
            item_file in orphaned_file_paths
            or any("Entire file is unreachable" in r for r in reasons)
        )
        if is_orphaned:
            if item_file not in orphaned_by_file:
                orphaned_by_file[item_file] = []
            orphaned_by_file[item_file].append(item)
        else:
            non_orphaned.append(item)

    # Create collapsed entries for each orphaned file
    collapsed: list[dict] = []
    for file_path, file_items in orphaned_by_file.items():
        # Collect symbol types for summary
        type_counts: dict[str, int] = {}
        for item in file_items:
            t = item.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        type_summary = ", ".join(f"{count} {t}s" for t, count in sorted(type_counts.items()))

        collapsed.append({
            "file": file_path,
            "type": "orphaned_file",
            "confidence": 100,
            "symbol_count": len(file_items),
            "type_summary": type_summary,
            "symbols": [i.get("qualified_name", "") for i in file_items],
            "reasons": ["Entire file is unreachable from any entrypoint"],
            "suggested_action": "DELETE",
        })

    return non_orphaned, collapsed


def run_batch_verification(
    project_path: Path,
    llm_tool: str = "claude",
    tiers: set[int] | None = None,
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
        tiers: Set of priority tiers to verify (default: {PRIORITY_P0})
        timeout: Timeout in seconds for the LLM call
        include_orphaned: If True, include orphaned file items for LLM verification.
                         If False (default), auto-mark them as DELETE.

    Returns:
        VerificationResults with all verified items
    """
    # Default to P0 if no tiers specified
    selected_tiers = tiers if tiers is not None else {PRIORITY_P0}

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

    # Collapse orphaned files to file-level entries
    non_orphaned_items, collapsed_orphan_files = _collapse_orphaned_files(
        dead_code, orphaned_file_paths
    )
    orphaned_item_count = sum(f["symbol_count"] for f in collapsed_orphan_files)

    # Filter by selected tiers (only non-orphaned items go through this)
    if include_orphaned:
        # Include everything in selected tiers
        candidates = [
            d for d in dead_code
            if _assign_priority(d) in selected_tiers
        ]
        auto_verified_orphans: list[VerifiedItem] = []
        collapsed_orphan_files = []  # Don't auto-verify, include in candidates
    else:
        # Skip orphaned items - auto-verify them as DELETE
        candidates = [
            d for d in non_orphaned_items
            if _assign_priority(d) in selected_tiers
        ]
        auto_verified_orphans = _auto_verify_orphans_from_collapsed(collapsed_orphan_files)

    # Sort candidates by priority (P0 first, then P1, P2)
    candidates = _sort_by_priority(candidates)

    # Count items by priority for display
    priority_counts: dict[int, int] = {}
    for item in candidates:
        p = _assign_priority(item)
        priority_counts[p] = priority_counts.get(p, 0) + 1

    # Items not in selected tiers
    skipped = [
        d for d in non_orphaned_items
        if _assign_priority(d) not in selected_tiers
    ]

    # Format selected tiers for display
    tier_names = []
    if PRIORITY_P0 in selected_tiers:
        tier_names.append("P0")
    if PRIORITY_P1 in selected_tiers:
        tier_names.append("P1")
    if PRIORITY_P2 in selected_tiers:
        tier_names.append("P2")
    if PRIORITY_P3 in selected_tiers:
        tier_names.append("P3")
    tiers_str = ", ".join(tier_names) if tier_names else "none"

    console.print(Panel.fit("[bold blue]OpenPrune - Auto Verification[/]"))
    console.print(f"\n[dim]LLM:[/] {llm_tool}")
    console.print(f"[dim]Selected tiers:[/] {tiers_str}")
    console.print(f"[green]{len(candidates)}[/] items to verify (sorted by priority)")
    console.print(f"[dim]{len(skipped)} items in other tiers (not selected)[/]")

    # Show priority breakdown
    if priority_counts:
        console.print("\n[bold]Priority breakdown:[/]")
        for p in sorted(priority_counts.keys()):
            label = _get_priority_label(p)
            count = priority_counts[p]
            color = "cyan" if p == PRIORITY_P0 else "blue" if p == PRIORITY_P1 else "dim"
            console.print(f"  [{color}]{label}:[/] {count} items")

    if collapsed_orphan_files:
        if include_orphaned:
            console.print(f"\n[yellow]{len(collapsed_orphan_files)} orphaned files (included for verification)[/]")
        else:
            console.print(f"\n[green]{len(collapsed_orphan_files)} orphaned files collapsed → {orphaned_item_count} items auto-marked DELETE[/]")
            console.print("[dim](use --include-orphaned to verify these with LLM)[/]")

    if not candidates:
        if auto_verified_orphans:
            console.print(f"\n[yellow]No items to verify via LLM. {len(auto_verified_orphans)} orphaned items auto-verified.[/]")
            return _build_results(auto_verified_orphans, skipped, llm_tool, tiers_str)
        console.print("\n[yellow]No items to verify.[/]")
        return _build_empty_results(skipped, llm_tool, tiers_str)

    # Build comprehensive oneshot prompt
    console.print("\n[dim]Building prompt with file contents...[/]")
    prompt = _build_oneshot_prompt(project_path, candidates, orphaned_files)

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
    results = _build_results(all_verified_items, skipped, llm_tool, tiers_str)

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
) -> str:
    """Build a comprehensive prompt for single-session verification."""
    prompt_parts = [
        build_system_prompt(project_path),
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
            priority = _assign_priority(item)

            # Add confidence level indicator with priority
            if confidence == 0:
                conf_label = "[ENTRYPOINT]"
            elif confidence < 50:
                conf_label = "[LOW - likely used]"
            elif confidence < 80:
                conf_label = f"[P{priority} MEDIUM - highest LLM value]"
            elif confidence < 100:
                is_import = "import" in item.get("type", "")
                conf_label = f"[P{priority} HIGH {'import' if is_import else 'non-import'}]"
            else:
                conf_label = "[P3 100% - auto-delete candidate]"

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

**PROCESS IN TIER ORDER**: Items are listed by priority. You MUST:
1. Process ALL P0 items first (complete every P0 before any P1)
2. Then ALL P1 items (complete every P1 before any P2)
3. Then ALL P2 items
4. Then P3 items if any

Do NOT jump between tiers. Finish each tier completely before proceeding.

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
      "reasoning": "Flask route entrypoint, will be called by web requests",
      "false_positive_pattern": "decorator_implicit"
    }
  ]
}
```

**For KEEP verdicts**, include `false_positive_pattern` to help improve detection. Use one of:
- `framework_instance` - Flask app, Celery instance, db session
- `decorator_implicit` - @validator, @fixture, @route not detected
- `dynamic_dispatch` - getattr(), importlib, string lookup
- `signal_handler` - signal.connect(), @receiver
- `registry_pattern` - HANDLERS['key'] = func, plugin systems
- `inheritance` - Base class calls child method
- `public_api` - __all__, documented API
- `test_infrastructure` - Test fixtures and helpers

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
                fp_pattern = item_data.get("false_positive_pattern")

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
                        false_positive_pattern=fp_pattern,
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


def _auto_verify_orphans_from_collapsed(
    collapsed_files: list[dict],
) -> list[VerifiedItem]:
    """Auto-verify orphaned files (collapsed) as DELETE without LLM verification.

    Creates a single VerifiedItem per orphaned file instead of one per symbol.
    The file-level entry shows the total symbol count.
    """
    verified: list[VerifiedItem] = []
    for file_entry in collapsed_files:
        file_path = file_entry.get("file", "")
        symbol_count = file_entry.get("symbol_count", 0)
        type_summary = file_entry.get("type_summary", "")

        verified.append(
            VerifiedItem(
                qualified_name=f"{file_path} (entire file)",
                name=Path(file_path).name,
                type="orphaned_file",
                file=Path(file_path),
                line=0,
                end_line=None,
                original_confidence=100,
                reasons=["Entire file is unreachable from any entrypoint"],
                code_preview=f"{symbol_count} symbols: {type_summary}" if type_summary else f"{symbol_count} symbols",
                verdict=LLMVerdict.DELETE,
                llm_reasoning=f"Auto-verified: Entire file unreachable ({symbol_count} symbols: {type_summary})",
                verified_at=datetime.now(),
            )
        )
    return verified


def _build_results(
    verified_items: list[VerifiedItem],
    skipped_items: list[dict],
    llm_tool: str,
    tiers: str,
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
            "tiers": tiers,
            "mode": "auto",
        },
        summary=summary,
        verified_items=verified_items,
        skipped_items=skipped_items,
    )


def _build_empty_results(
    skipped_items: list[dict],
    llm_tool: str,
    tiers: str,
) -> VerificationResults:
    """Build empty results when nothing to verify."""
    return VerificationResults(
        version="1.0",
        metadata={
            "verified_at": datetime.now().isoformat(),
            "llm_tool": llm_tool,
            "tiers": tiers,
            "mode": "auto",
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
