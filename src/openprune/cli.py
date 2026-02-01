"""OpenPrune CLI - Interactive dead code detection for Python."""

import fnmatch
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Confirm
from rich.table import Table

from openprune import __version__
from openprune.analysis.imports import ImportResolver
from openprune.analysis.scoring import (
    SuspicionScorer,
    classify_confidence,
    get_file_age_info,
)
from openprune.analysis.noqa import is_noqa_suppressed
from openprune.analysis.visitor import analyze_file
from openprune.config import (
    get_analysis_excludes,
    get_analysis_includes,
    get_entrypoint_types_to_mark,
    get_ignore_decorators,
    get_noqa_patterns,
    load_config,
    should_respect_noqa,
)
from openprune.detection.archetype import ArchetypeDetector
from openprune.analysis.visitor import FileAnalysisResult
from openprune.models.dependency import DependencyNode, Symbol, SymbolType, Usage
from openprune.models.results import (
    AnalysisMetadata,
    AnalysisResults,
    AnalysisSummary,
    DeadCodeItem,
    NoqaSkipped,
    OrphanedFile,
)
from openprune.output.json_writer import write_config, write_results
from openprune.output.tree import build_results_tree, build_summary_tree, display_tree
from openprune.paths import ensure_openprune_dir, get_config_path, get_results_path, get_verified_path

app = typer.Typer(
    name="openprune",
    help="Detect dead code in Python Flask+Celery applications",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"openprune version {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """Detect dead code in Python Flask+Celery applications."""
    if ctx.invoked_subcommand is None:
        # Default to run command
        ctx.invoke(run)


@app.command()
def run(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the Python project to analyze",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to open-prune.json config file",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Path for results JSON output",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show full tree in CLI (default: summary only)",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        "-i/-I",
        help="Enable/disable interactive mode",
    ),
    include_ignored: bool = typer.Option(
        False,
        "--include-ignored",
        help="Include files normally excluded by .gitignore and pyproject.toml",
    ),
) -> None:
    """Run full detection and analysis (default command)."""
    run_interactive(path, config, output, verbose, interactive, include_ignored)


@app.command()
def detect(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the Python project to analyze",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Path for config JSON output (default: .openprune/config.json)",
    ),
    include_ignored: bool = typer.Option(
        False,
        "--include-ignored",
        help="Include files normally excluded by .gitignore and pyproject.toml",
    ),
) -> None:
    """Run archetype detection and generate config file."""
    console.print(Panel.fit("[bold blue]OpenPrune - Archetype Detection[/]"))
    console.print()

    path = path.resolve()

    # Use .openprune/config.json by default
    if output is None:
        ensure_openprune_dir(path)
        output = get_config_path(path)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Detecting application archetype...", total=None)

        detector = ArchetypeDetector(include_ignored=include_ignored)
        result = detector.detect(path)

        progress.update(task, completed=True)

    # Display results
    _display_archetype_results(result)

    # Write config
    write_config(result, output)
    console.print(f"\n[green]Configuration saved to:[/] {output}")


@app.command()
def analyze(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the Python project to analyze",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (default: .openprune/config.json)",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Path for results JSON output (default: .openprune/results.json)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show full tree in CLI",
    ),
    include_ignored: bool = typer.Option(
        False,
        "--include-ignored",
        help="Include files normally excluded by .gitignore and pyproject.toml",
    ),
) -> None:
    """Run full dead code analysis using existing config."""
    path = path.resolve()

    # Use .openprune/ paths by default
    if config is None:
        config = get_config_path(path)
    if output is None:
        ensure_openprune_dir(path)
        output = get_results_path(path)

    if not config.exists():
        console.print(f"[red]Config file not found:[/] {config}")
        console.print("Run [bold]openprune detect[/] first to generate the config file.")
        raise typer.Exit(1)

    config_data = load_config(config)
    results = _run_analysis(path, config_data, include_ignored)

    # Write results
    write_results(results, output)
    console.print(f"\n[green]Results saved to:[/] {output}")

    # Display summary or full tree
    if verbose:
        tree = build_results_tree(results.dead_code, path)
        display_tree(tree)
    else:
        _display_summary(results)


@app.command()
def verify(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the Python project",
    ),
    llm: str = typer.Option(
        "claude",
        "--llm",
        "-l",
        help="LLM CLI tool to use (e.g., claude, kimi, opencode)",
    ),
    min_confidence: int = typer.Option(
        70,
        "--min-confidence",
        "-m",
        help="Minimum confidence to include for verification",
    ),
    batch: bool = typer.Option(
        False,
        "--batch",
        "-b",
        help="Run in non-interactive batch mode",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show what would be sent to LLM without executing",
    ),
) -> None:
    """Verify dead code candidates using an LLM.

    By default, launches an interactive LLM session with context about the
    .openprune/ folder. The LLM can read results.json, examine source files,
    and write verdicts to verified.json.

    Use --batch for non-interactive processing of each item.
    """
    path = path.resolve()
    results_path = get_results_path(path)

    if not results_path.exists():
        console.print(f"[red]No results found:[/] {results_path}")
        console.print("Run [bold]openprune analyze[/] first to generate results.")
        raise typer.Exit(1)

    if dry_run:
        _show_verify_dry_run(path, min_confidence)
        return

    if batch:
        # Non-interactive batch mode
        from openprune.verification.batch import run_batch_verification

        try:
            run_batch_verification(path, llm, min_confidence)
        except RuntimeError as e:
            console.print(f"[red]Error:[/] {e}")
            raise typer.Exit(1)
    else:
        # Interactive mode - handoff to LLM CLI
        console.print(Panel.fit("[bold blue]OpenPrune - LLM Verification[/]"))
        console.print(f"\n[dim]Using LLM:[/] {llm}")
        console.print(f"[dim]Min confidence:[/] {min_confidence}%")
        console.print(f"[dim]Working directory:[/] {path}")
        console.print(f"[dim]The LLM has access to .openprune/ files[/]\n")

        from openprune.verification.session import launch_llm_session

        try:
            launch_llm_session(path, llm, min_confidence)
        except RuntimeError as e:
            console.print(f"[red]Error:[/] {e}")
            raise typer.Exit(1)


def _show_verify_dry_run(path: Path, min_confidence: int) -> None:
    """Show dry-run preview for verification."""
    from openprune.output.json_writer import load_results
    from openprune.verification.prompts import build_system_prompt

    results_path = get_results_path(path)
    data = load_results(results_path)
    dead_code = data.get("dead_code", [])

    candidates = [d for d in dead_code if d.get("confidence", 0) >= min_confidence]

    console.print(Panel.fit("[bold cyan]Dry Run Preview[/]"))
    console.print(f"\n[dim]Items to verify:[/] {len(candidates)}")
    console.print(f"[dim]Skipped (below threshold):[/] {len(dead_code) - len(candidates)}\n")

    if candidates:
        console.print("[bold]Top 5 candidates:[/]")
        for item in candidates[:5]:
            console.print(
                f"  • [cyan]{item.get('name')}[/] "
                f"({item.get('type')}, {item.get('confidence')}%)"
            )

    console.print("\n[bold]System prompt preview:[/]")
    prompt = build_system_prompt(path, min_confidence)
    # Show first 500 chars
    preview = prompt[:500] + "..." if len(prompt) > 500 else prompt
    console.print(f"[dim]{preview}[/]")


@app.command()
def show(
    results_path: Optional[Path] = typer.Argument(
        None,
        help="Path to results file (default: .openprune/results.json)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show full tree view",
    ),
    min_confidence: int = typer.Option(
        0,
        "--min-confidence",
        help="Only show items with this confidence or higher",
    ),
) -> None:
    """Display results from a previous analysis run."""
    # Default to .openprune/results.json in current directory
    if results_path is None:
        results_path = get_results_path(Path.cwd())

    if not results_path.exists():
        console.print(f"[red]Results file not found:[/] {results_path}")
        raise typer.Exit(1)

    from openprune.output.json_writer import load_results

    data = load_results(results_path)

    # Convert to DeadCodeItem objects
    dead_code = []
    for item in data.get("dead_code", []):
        if item.get("confidence", 0) >= min_confidence:
            dead_code.append(
                DeadCodeItem(
                    qualified_name=item.get("qualified_name", ""),
                    name=item.get("name", ""),
                    type=item.get("type", "unknown"),
                    file=Path(item.get("file", "")),
                    line=item.get("line", 0),
                    end_line=item.get("end_line"),
                    confidence=item.get("confidence", 0),
                    reasons=item.get("reasons", []),
                )
            )

    if verbose:
        # Try to get project root from metadata
        project_root = Path(data.get("metadata", {}).get("project", "."))
        tree = build_results_tree(dead_code, project_root)
        display_tree(tree)
    else:
        summary_tree = build_summary_tree(dead_code, min_confidence)
        display_tree(summary_tree)


def run_interactive(
    path: Path,
    config_path: Optional[Path],
    output_path: Optional[Path],
    verbose: bool,
    interactive: bool,
    include_ignored: bool = False,
) -> None:
    """Run OpenPrune in interactive mode."""
    path = path.resolve()

    # Use .openprune/ directory by default
    ensure_openprune_dir(path)
    config_path = config_path or get_config_path(path)
    output_path = output_path or get_results_path(path)

    console.print(Panel.fit("[bold blue]OpenPrune - Dead Code Detection[/]"))
    console.print(f"\n[dim]Scanning:[/] {path}\n")

    # Phase 1: Archetype Detection
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Detecting application archetype...", total=None)

        detector = ArchetypeDetector(include_ignored=include_ignored)
        archetype_result = detector.detect(path)

        progress.update(task, completed=True)

    _display_archetype_results(archetype_result)

    # Write config
    write_config(archetype_result, config_path)
    console.print(f"\n[green]Configuration saved to:[/] {config_path}")

    # Phase 2: Ask to proceed with analysis
    if interactive:
        proceed = Confirm.ask("\n[bold]Proceed with dead code analysis?[/]", default=True)
        if not proceed:
            console.print("[yellow]Analysis cancelled.[/]")
            raise typer.Exit()

    console.print()

    # Phase 3: Run analysis
    config_data = load_config(config_path)
    results = _run_analysis(path, config_data, include_ignored)

    # Write results
    write_results(results, output_path)
    console.print(f"\n[green]Results saved to:[/] {output_path}")

    # Display results
    _display_summary(results)

    # Ask to show detailed tree
    if interactive and results.dead_code:
        show_tree = Confirm.ask("\n[bold]Show detailed tree view?[/]", default=False)
        if show_tree:
            tree = build_results_tree(results.dead_code, path)
            display_tree(tree)


def _display_archetype_results(result) -> None:
    """Display archetype detection results."""
    if result.frameworks:
        console.print("[bold green]✓[/] Detected frameworks:")
        for fw in result.frameworks:
            evidence_count = len(fw.evidence)
            console.print(f"  • {fw.framework.name} ({evidence_count} files)")
    else:
        console.print("[yellow]![/] No frameworks detected")

    if result.entrypoints:
        console.print("\n[bold green]✓[/] Found entrypoints:")
        # Group by type
        by_type: dict[str, int] = defaultdict(int)
        for ep in result.entrypoints:
            by_type[ep.type.name.lower().replace("_", " ")] += 1

        for ep_type, count in sorted(by_type.items()):
            console.print(f"  • {count} {ep_type}s")
    else:
        console.print("[yellow]![/] No entrypoints detected")

    if result.linting_config.sources:
        console.print("\n[bold green]✓[/] Linting configuration:")
        for source in result.linting_config.sources:
            console.print(f"  • {source}")


def _run_analysis(path: Path, config: dict, include_ignored: bool = False) -> AnalysisResults:
    """Run the full dead code analysis."""
    start_time = time.time()

    # Get file patterns
    includes = get_analysis_includes(config)
    excludes = get_analysis_excludes(config)

    # Get noqa configuration
    noqa_patterns = get_noqa_patterns(config)
    respect_noqa = should_respect_noqa(config)

    # Get ignore_decorators patterns
    ignore_decorators = get_ignore_decorators(config)

    # Find all Python files
    py_files = _find_python_files(path, includes, excludes, include_ignored)

    console.print(f"[dim]Found {len(py_files)} Python files to analyze[/]\n")

    # Analyze all files
    all_definitions: dict[str, Symbol] = {}
    all_usages: set[str] = set()
    all_usages_list: list[Usage] = []  # For call graph
    file_results: dict[Path, FileAnalysisResult] = {}
    noqa_skipped: list[NoqaSkipped] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        parse_task = progress.add_task("Parsing Python files...", total=len(py_files))

        for py_file in py_files:
            result = analyze_file(py_file)
            if result.error:
                progress.update(parse_task, advance=1)
                continue

            file_results[py_file] = result

            # Collect definitions, filtering those with noqa comments
            for qname, symbol in result.definitions.items():
                line = symbol.location.line
                comment = result.line_comments.get(line)

                if respect_noqa and comment:
                    match = is_noqa_suppressed(comment, noqa_patterns)
                    if match.matched:
                        noqa_skipped.append(
                            NoqaSkipped(
                                file=str(py_file),
                                line=line,
                                comment=comment,
                                symbol=qname,
                            )
                        )
                        continue  # Skip this definition

                # Skip symbols with ignored decorators
                if _should_ignore_by_decorator(symbol, ignore_decorators):
                    continue

                all_definitions[qname] = symbol

            # Collect usages
            for usage in result.usages:
                all_usages.add(usage.symbol_name)
                all_usages_list.append(usage)

            progress.update(parse_task, advance=1)

        # Build import graph
        progress.add_task("Building import graph...", total=None)
        resolver = ImportResolver(path)
        import_graph = resolver.build_graph(py_files)

        # Get file age info for scoring
        progress.add_task("Collecting file age info...", total=None)
        file_age_info = get_file_age_info(py_files, path, prefer_git=True)

    # Get entrypoint types to mark as used
    entrypoint_types = get_entrypoint_types_to_mark(config)

    # Build call graph and find reachable symbols
    console.print("[dim]Building call graph and analyzing reachability...[/]")
    call_graph = _build_call_graph(all_definitions, all_usages_list)

    # Map entrypoint types to decorator patterns
    entrypoint_decorator_patterns = {
        "flask_route": ["route", "get", "post", "put", "delete", "patch"],
        "flask_blueprint": ["route", "get", "post", "put", "delete", "patch"],
        "flask_hook": ["before_request", "after_request", "teardown_request", "before_first_request"],
        "flask_errorhandler": ["errorhandler"],
        "flask_cli": ["cli.command", "command"],
        "celery_task": ["task"],
        "celery_shared_task": ["shared_task"],
        "celery_signal": ["connect"],
        "fastapi_route": ["get", "post", "put", "delete", "patch"],
        "click_command": ["command"],
        "factory_function": [],  # Matched by name, not decorator
        "main_block": [],  # Not a decorator-based entrypoint
    }

    # Mark symbols as entrypoints based on decorators
    for qname, symbol in all_definitions.items():
        # Check if symbol name matches factory function pattern
        if "factory_function" in entrypoint_types and symbol.name in {
            "create_app", "make_app", "make_celery", "create_celery", "app_factory"
        }:
            symbol.is_entrypoint = True
            continue

        # Check decorators against patterns
        for dec in symbol.decorators:
            dec_lower = dec.lower()
            for ep_type in entrypoint_types:
                patterns = entrypoint_decorator_patterns.get(ep_type, [])
                for pattern in patterns:
                    if pattern in dec_lower:
                        symbol.is_entrypoint = True
                        break
                if symbol.is_entrypoint:
                    break
            if symbol.is_entrypoint:
                break

    # Collect entrypoint qualified names
    entrypoint_qnames: set[str] = {
        qname for qname, sym in all_definitions.items() if sym.is_entrypoint
    }

    # Find all reachable symbols from entrypoints
    reachable_symbols = _find_reachable_symbols(entrypoint_qnames, call_graph)

    # Find reachable modules (for orphaned file detection)
    entrypoint_files = {
        symbol.location.file
        for qname, symbol in all_definitions.items()
        if symbol.is_entrypoint
    }
    reachable_modules = _find_reachable_modules(entrypoint_files, file_results)

    # All analyzed modules
    all_modules = {py_file.stem for py_file in py_files}
    orphaned_modules = all_modules - reachable_modules

    if orphaned_modules:
        console.print(f"[dim]Found {len(orphaned_modules)} orphaned modules[/]")

    # Score all definitions
    console.print("[dim]Calculating suspicion scores...[/]")
    scorer = SuspicionScorer()
    dead_code: list[DeadCodeItem] = []

    for qname, symbol in all_definitions.items():
        # Create a node for scoring
        node = DependencyNode(symbol=symbol)

        # Score the node
        confidence, reasons = scorer.score(node, all_usages, file_age_info)

        # Check module-level reachability
        module_name = symbol.location.file.stem
        if module_name in orphaned_modules:
            # Entire file is orphaned - very high confidence
            confidence = 100
            reasons = ["Entire file is unreachable from any entrypoint"]
        elif qname not in reachable_symbols and entrypoint_qnames:
            # Symbol is in a reachable file but not called from entrypoints
            confidence = min(confidence + 30, 100)
            if "Not reachable from any entrypoint" not in reasons:
                reasons.append("Not reachable from any entrypoint")
        elif qname in reachable_symbols:
            # Symbol is reachable - lower confidence
            confidence = max(confidence - 20, 0)

        # Determine dead code type
        dead_type = _get_dead_code_type(symbol)

        # Create dead code item
        dead_code.append(
            DeadCodeItem(
                qualified_name=qname,
                name=symbol.name,
                type=dead_type,
                file=symbol.location.file,
                line=symbol.location.line,
                end_line=symbol.location.end_line,
                confidence=confidence,
                reasons=reasons,
                suggested_action="review" if confidence < 90 else "remove",
            )
        )

    # Sort by confidence (highest first)
    dead_code.sort(key=lambda x: x.confidence, reverse=True)

    # Build orphaned files list
    orphaned_file_list: list[OrphanedFile] = []
    for py_file in py_files:
        if py_file.stem in orphaned_modules:
            result = file_results.get(py_file)
            if result:
                # Count symbols and lines
                symbol_count = len(result.definitions)
                try:
                    line_count = len(py_file.read_text().splitlines())
                except Exception:
                    line_count = 0

                orphaned_file_list.append(
                    OrphanedFile(
                        file=str(py_file),
                        module_name=py_file.stem,
                        symbols=symbol_count,
                        lines=line_count,
                    )
                )

    # Calculate duration
    duration_ms = int((time.time() - start_time) * 1000)

    # Build summary
    summary = _build_summary(dead_code)

    # Build results
    results = AnalysisResults(
        version="1.0",
        metadata=AnalysisMetadata(
            project=path.name,
            analyzed_at=datetime.now(),
            openprune_version=__version__,
            files_analyzed=len(py_files),
            total_symbols=len(all_definitions),
            analysis_duration_ms=duration_ms,
        ),
        summary=summary,
        orphaned_files=orphaned_file_list,
        dead_code=dead_code,
        dependency_tree=import_graph.to_dict(),
        noqa_skipped=noqa_skipped,
    )

    # Show noqa skipped count if any
    if noqa_skipped:
        console.print(f"[dim]Skipped {len(noqa_skipped)} items due to noqa comments[/]")

    return results


def _find_python_files(
    path: Path,
    includes: list[str],
    excludes: list[str],
    include_ignored: bool = False,
) -> list[Path]:
    """Find Python files matching include/exclude patterns."""
    from openprune.exclusion import FileExcluder

    # Use FileExcluder for gitignore/pyproject.toml patterns
    excluder = FileExcluder(path, include_ignored=include_ignored, extra_excludes=excludes)

    py_files: list[Path] = []

    for py_file in path.rglob("*.py"):
        # Use FileExcluder for all exclusion logic
        if excluder.should_exclude(py_file):
            continue

        # Get relative path for include pattern matching
        try:
            rel_path = py_file.relative_to(path)
        except ValueError:
            continue

        rel_str = str(rel_path)

        # Check includes
        included = False
        for pattern in includes:
            if fnmatch.fnmatch(rel_str, pattern):
                included = True
                break
            # Handle **/*.py matching files at root level
            if pattern.startswith("**/") and fnmatch.fnmatch(rel_str, pattern[3:]):
                included = True
                break

        if included:
            py_files.append(py_file)

    return py_files


def _should_ignore_by_decorator(symbol: Symbol, patterns: list[str]) -> bool:
    """Check if symbol has a decorator matching ignore patterns."""
    for decorator in symbol.decorators:
        for pattern in patterns:
            # Strip leading @ from both
            pattern_clean = pattern.lstrip("@")
            decorator_clean = decorator.lstrip("@")

            # Try glob match (e.g., "pytest.mark.*")
            if fnmatch.fnmatch(decorator_clean, pattern_clean):
                return True

            # Also check if pattern is contained (e.g., "abstractmethod" in "@abc.abstractmethod")
            if pattern_clean in decorator_clean:
                return True

    return False


def _build_call_graph(
    all_definitions: dict[str, Symbol],
    all_usages: list[Usage],
) -> dict[str, set[str]]:
    """Build graph of caller -> callees (qualified names)."""
    graph: dict[str, set[str]] = {qname: set() for qname in all_definitions}

    for usage in all_usages:
        if usage.caller and usage.caller in graph:
            # Try to resolve the usage to a known definition
            for qname in all_definitions:
                if qname.endswith(f".{usage.symbol_name}"):
                    graph[usage.caller].add(qname)
                    break

    return graph


def _find_reachable_symbols(
    entrypoint_qnames: set[str],
    call_graph: dict[str, set[str]],
) -> set[str]:
    """Find all symbols reachable from entrypoints via call graph."""
    reachable = set(entrypoint_qnames)
    to_visit = list(entrypoint_qnames)

    while to_visit:
        current = to_visit.pop()
        for callee in call_graph.get(current, set()):
            if callee not in reachable:
                reachable.add(callee)
                to_visit.append(callee)

    return reachable


def _find_reachable_modules(
    entrypoint_files: set[Path],
    file_results: dict[Path, FileAnalysisResult],
) -> set[str]:
    """Find all modules reachable via imports from entrypoint files."""
    reachable = {f.stem for f in entrypoint_files}
    to_visit = list(reachable)

    # Build module import graph
    module_imports: dict[str, set[str]] = {}
    for py_file, result in file_results.items():
        module_imports[py_file.stem] = {
            imp.module.split(".")[0]  # Get top-level module
            for imp in result.imports
            if imp.module
        }

    while to_visit:
        current = to_visit.pop()
        for imported in module_imports.get(current, set()):
            if imported not in reachable:
                reachable.add(imported)
                to_visit.append(imported)

    return reachable


def _get_entrypoint_qnames(
    entrypoints: list,  # list of Entrypoint from archetype
    all_definitions: dict[str, Symbol],
) -> set[str]:
    """Get qualified names of all entrypoint symbols."""
    entrypoint_qnames: set[str] = set()

    for ep in entrypoints:
        # Match by file and name
        for qname, symbol in all_definitions.items():
            if str(symbol.location.file) == ep.file and symbol.name == ep.name:
                entrypoint_qnames.add(qname)
                break

    return entrypoint_qnames


def _get_dead_code_type(symbol: Symbol) -> str:
    """Get the dead code type string for a symbol."""
    match symbol.type:
        case SymbolType.FUNCTION:
            return "unused_function"
        case SymbolType.METHOD:
            return "unused_method"
        case SymbolType.CLASS:
            return "unused_class"
        case SymbolType.VARIABLE:
            return "unused_variable"
        case SymbolType.IMPORT:
            return "unused_import"
        case SymbolType.CONSTANT:
            return "unused_constant"
        case SymbolType.MODULE:
            return "orphaned_module"
        case _:
            return "unused_code"


def _build_summary(dead_code: list[DeadCodeItem]) -> AnalysisSummary:
    """Build analysis summary from dead code items."""
    by_type: dict[str, int] = defaultdict(int)
    by_confidence: dict[str, int] = defaultdict(int)
    total_lines = 0

    for item in dead_code:
        by_type[item.type] += 1
        conf_level = classify_confidence(item.confidence)
        by_confidence[conf_level] += 1

        # Estimate lines
        if item.end_line and item.line:
            total_lines += item.end_line - item.line + 1
        else:
            total_lines += 1

    return AnalysisSummary(
        dead_code_items=len(dead_code),
        by_type=dict(by_type),
        by_confidence=dict(by_confidence),
        estimated_lines_removable=total_lines,
    )


def _display_summary(results: AnalysisResults) -> None:
    """Display analysis summary."""
    if not results.summary:
        return

    summary = results.summary

    # Create summary panel
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="cyan")
    table.add_column("Value")

    table.add_row("Total items", str(summary.dead_code_items))

    for dead_type, count in sorted(summary.by_type.items()):
        table.add_row(f"  {dead_type}", str(count))

    table.add_row("", "")
    table.add_row("By confidence:", "")
    for conf_level, count in sorted(summary.by_confidence.items()):
        color = {"high": "red", "medium": "yellow", "low": "green"}.get(conf_level, "white")
        table.add_row(f"  [{color}]{conf_level}[/]", str(count))

    table.add_row("", "")
    table.add_row("Estimated removable lines", str(summary.estimated_lines_removable))

    console.print(Panel(table, title="[bold]Dead Code Summary[/]", border_style="blue"))


if __name__ == "__main__":
    app()
