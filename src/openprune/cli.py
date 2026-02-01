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
from openprune.analysis.visitor import analyze_file
from openprune.config import (
    get_analysis_excludes,
    get_analysis_includes,
    get_entrypoint_types_to_mark,
    load_config,
)
from openprune.detection.archetype import ArchetypeDetector
from openprune.models.dependency import DependencyNode, Symbol, SymbolType
from openprune.models.results import (
    AnalysisMetadata,
    AnalysisResults,
    AnalysisSummary,
    DeadCodeItem,
)
from openprune.output.json_writer import write_config, write_results
from openprune.output.tree import build_results_tree, build_summary_tree, display_tree

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
) -> None:
    """Run full detection and analysis (default command)."""
    run_interactive(path, config, output, verbose, interactive)


@app.command()
def detect(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the Python project to analyze",
    ),
    output: Path = typer.Option(
        Path("open-prune.json"),
        "--output",
        "-o",
        help="Path for config JSON output",
    ),
) -> None:
    """Run archetype detection and generate config file."""
    console.print(Panel.fit("[bold blue]OpenPrune - Archetype Detection[/]"))
    console.print()

    path = path.resolve()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Detecting application archetype...", total=None)

        detector = ArchetypeDetector()
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
    config: Path = typer.Option(
        Path("open-prune.json"),
        "--config",
        "-c",
        help="Path to open-prune.json config file",
    ),
    output: Path = typer.Option(
        Path("openprune-results.json"),
        "--output",
        "-o",
        help="Path for results JSON output",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show full tree in CLI",
    ),
) -> None:
    """Run full dead code analysis using existing config."""
    path = path.resolve()

    if not config.exists():
        console.print(f"[red]Config file not found:[/] {config}")
        console.print("Run [bold]openprune detect[/] first to generate the config file.")
        raise typer.Exit(1)

    config_data = load_config(config)
    results = _run_analysis(path, config_data)

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
def show(
    results_path: Path = typer.Argument(
        Path("openprune-results.json"),
        help="Path to results file",
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
) -> None:
    """Run OpenPrune in interactive mode."""
    path = path.resolve()
    config_path = config_path or Path("open-prune.json")
    output_path = output_path or Path("openprune-results.json")

    console.print(Panel.fit("[bold blue]OpenPrune - Dead Code Detection[/]"))
    console.print(f"\n[dim]Scanning:[/] {path}\n")

    # Phase 1: Archetype Detection
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Detecting application archetype...", total=None)

        detector = ArchetypeDetector()
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
    results = _run_analysis(path, config_data)

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


def _run_analysis(path: Path, config: dict) -> AnalysisResults:
    """Run the full dead code analysis."""
    start_time = time.time()

    # Get file patterns
    includes = get_analysis_includes(config)
    excludes = get_analysis_excludes(config)

    # Find all Python files
    py_files = _find_python_files(path, includes, excludes)

    console.print(f"[dim]Found {len(py_files)} Python files to analyze[/]\n")

    # Analyze all files
    all_definitions: dict[str, Symbol] = {}
    all_usages: set[str] = set()
    file_results: dict[Path, dict] = {}

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

            file_results[py_file] = {
                "definitions": result.definitions,
                "usages": result.usages,
                "imports": result.imports,
            }

            # Collect definitions and usages
            all_definitions.update(result.definitions)
            for usage in result.usages:
                all_usages.add(usage.symbol_name)

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

    # Score all definitions
    console.print("[dim]Calculating suspicion scores...[/]")
    scorer = SuspicionScorer()
    dead_code: list[DeadCodeItem] = []

    for qname, symbol in all_definitions.items():
        # Create a node for scoring
        node = DependencyNode(symbol=symbol)

        # Check if it's an entrypoint type
        for dec in symbol.decorators:
            for ep_type in entrypoint_types:
                if ep_type.lower() in dec.lower():
                    symbol.is_entrypoint = True
                    break

        # Score the node
        confidence, reasons = scorer.score(node, all_usages, file_age_info)

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
        dead_code=dead_code,
        dependency_tree=import_graph.to_dict(),
    )

    return results


def _find_python_files(
    path: Path,
    includes: list[str],
    excludes: list[str],
) -> list[Path]:
    """Find Python files matching include/exclude patterns."""
    py_files: list[Path] = []

    for py_file in path.rglob("*.py"):
        # Get relative path for pattern matching
        try:
            rel_path = py_file.relative_to(path)
        except ValueError:
            continue

        rel_str = str(rel_path)

        # Check excludes first
        excluded = False
        for pattern in excludes:
            if fnmatch.fnmatch(rel_str, pattern):
                excluded = True
                break
            # Also check directory components
            for part in rel_path.parts:
                if fnmatch.fnmatch(part, pattern.replace("**/", "").replace("/**", "")):
                    excluded = True
                    break

        if excluded:
            continue

        # Check includes
        included = False
        for pattern in includes:
            if fnmatch.fnmatch(rel_str, pattern):
                included = True
                break

        if included:
            py_files.append(py_file)

    return py_files


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
