"""Rich tree visualization for dead code results."""

from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.text import Text
from rich.tree import Tree

from openprune.models.results import DeadCodeItem

console = Console()


def build_results_tree(
    dead_code: list[DeadCodeItem],
    project_root: Path,
) -> Tree:
    """Build a Rich tree showing dead code by file."""
    # Group by file
    by_file: dict[Path, list[DeadCodeItem]] = defaultdict(list)
    for item in dead_code:
        try:
            rel_path = item.file.relative_to(project_root)
        except ValueError:
            rel_path = item.file
        by_file[rel_path].append(item)

    # Sort files by path
    sorted_files = sorted(by_file.keys())

    # Build tree structure
    root = Tree(
        f"[bold]{project_root.name}[/]",
        guide_style="dim",
    )

    # Track directories we've added
    dir_nodes: dict[Path, Tree] = {}

    for file_path in sorted_files:
        items = by_file[file_path]

        # Create directory nodes as needed
        parent = root
        for i, part in enumerate(file_path.parts[:-1]):
            dir_path = Path(*file_path.parts[: i + 1])
            if dir_path not in dir_nodes:
                dir_nodes[dir_path] = parent.add(f"[bold blue]{part}/[/]")
            parent = dir_nodes[dir_path]

        # Add file node
        file_node = parent.add(f"[yellow]{file_path.name}[/]")

        # Add dead code items
        for item in sorted(items, key=lambda x: x.line):
            confidence_color = _confidence_color(item.confidence)

            item_text = Text()
            item_text.append("x ", style="red bold")
            item_text.append(item.name, style="red")
            item_text.append(f" ({item.type}, line {item.line}, ", style="dim")
            item_text.append(f"confidence: {item.confidence}%", style=f"{confidence_color}")
            item_text.append(")", style="dim")

            file_node.add(item_text)

    return root


def _confidence_color(confidence: int) -> str:
    """Get color based on confidence level."""
    if confidence >= 90:
        return "red"
    if confidence >= 70:
        return "yellow"
    return "green"


def display_tree(tree: Tree) -> None:
    """Display the tree to console."""
    console.print()
    console.print(tree)
    console.print()


def build_summary_tree(
    dead_code: list[DeadCodeItem],
    min_confidence: int = 0,
) -> Tree:
    """Build a summary tree grouped by type and confidence."""
    # Filter by confidence
    filtered = [item for item in dead_code if item.confidence >= min_confidence]

    # Group by type
    by_type: dict[str, list[DeadCodeItem]] = defaultdict(list)
    for item in filtered:
        by_type[item.type].append(item)

    root = Tree("[bold]Dead Code Summary[/]", guide_style="dim")

    for type_name, items in sorted(by_type.items()):
        # Calculate stats
        count = len(items)
        avg_confidence = sum(item.confidence for item in items) // count if count else 0
        max_confidence = max(item.confidence for item in items) if items else 0

        type_node = root.add(f"[cyan]{type_name}[/] ({count} items)")
        type_node.add(f"Average confidence: {avg_confidence}%")
        type_node.add(f"Highest confidence: {max_confidence}%")

        # Show top 3 highest confidence items
        top_items = sorted(items, key=lambda x: x.confidence, reverse=True)[:3]
        if top_items:
            examples_node = type_node.add("[dim]Examples:[/]")
            for item in top_items:
                color = _confidence_color(item.confidence)
                examples_node.add(
                    f"[{color}]{item.name}[/] in {item.file.name}:{item.line} "
                    f"({item.confidence}%)"
                )

    return root
