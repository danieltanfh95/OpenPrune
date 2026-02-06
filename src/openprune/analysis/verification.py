"""Import verification using grep-based cross-checking.

This module provides grep-based verification to catch false positives in
orphaned file detection. It serves as a sanity check after AST-based analysis.

The key insight is that a simple grep for import patterns can catch cases
where the AST-based analysis failed to trace imports correctly.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class VerificationResult:
    """Result of verifying a single orphaned module."""

    module_name: str
    file_path: str
    is_orphaned: bool
    importer_count: int
    sample_importers: list[str]
    import_patterns_found: list[str]
    confidence: str  # "confirmed", "likely_false_positive", "uncertain"

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class VerificationReport:
    """Report from verifying all orphaned modules."""

    total_reported_orphaned: int
    false_positives: list[VerificationResult]
    confirmed_orphans: list[VerificationResult]
    false_positive_rate: float

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return {
            "summary": {
                "total_reported_orphaned": self.total_reported_orphaned,
                "false_positives_count": len(self.false_positives),
                "confirmed_orphans_count": len(self.confirmed_orphans),
                "false_positive_rate": f"{self.false_positive_rate:.1%}",
            },
            "false_positives": [fp.to_dict() for fp in self.false_positives],
            "confirmed_orphans": [co.to_dict() for co in self.confirmed_orphans],
        }


def file_to_module_path(file_path: str | Path, project_root: str | Path) -> str:
    """Convert a file path to a Python module path.

    Examples:
        src/foo/bar.py -> src.foo.bar
        lib/utils/helper.py -> lib.utils.helper
    """
    file_path_str = str(file_path)
    project_root_str = str(project_root)

    # Remove project root prefix if present
    if file_path_str.startswith(project_root_str):
        file_path_str = file_path_str[len(project_root_str) :].lstrip("/")

    # Convert path to module
    module = file_path_str.replace("/", ".").replace(".py", "")

    # Remove __init__ suffix for packages
    if module.endswith(".__init__"):
        module = module[:-9]

    return module


def find_import_patterns(module_path: str) -> list[str]:
    """Generate possible import patterns to search for.

    For module 'src.foo.bar', generates:
        - 'from src.foo.bar import'
        - 'from src.foo import bar'
        - 'import src.foo.bar'

    Security: Module paths are escaped to prevent regex injection attacks
    from malicious filenames containing regex metacharacters.
    """
    # Escape regex metacharacters to prevent injection attacks
    escaped_path = re.escape(module_path)

    patterns = [
        f"from {escaped_path} import",
        f"import {escaped_path}",
    ]

    # Also check for parent module imports
    # e.g., "from src.foo import bar" for module "src.foo.bar"
    parts = module_path.rsplit(".", 1)
    if len(parts) == 2:
        parent, name = parts
        escaped_parent = re.escape(parent)
        escaped_name = re.escape(name)
        patterns.append(f"from {escaped_parent} import {escaped_name}")
        # With other imports on same line
        patterns.append(f"from {escaped_parent} import .*{escaped_name}")

    return patterns


def search_for_imports(
    module_path: str,
    search_dir: str | Path,
    exclude_patterns: list[str] | None = None,
    timeout: int = 10,
) -> tuple[list[str], list[str]]:
    """Search for import statements of a module using grep.

    Args:
        module_path: The module path to search for (e.g., "utils.helpers")
        search_dir: Directory to search in
        exclude_patterns: Patterns to exclude from results
        timeout: Timeout in seconds for each grep command

    Returns:
        tuple: (list of files importing this module, list of patterns found)
    """
    if exclude_patterns is None:
        exclude_patterns = [".venv", "__pycache__", ".git", "node_modules", ".tox"]

    importers: set[str] = set()
    patterns_found: list[str] = []

    for pattern in find_import_patterns(module_path):
        try:
            # Use grep -E for extended regex support
            cmd = [
                "grep",
                "-r",
                "-l",
                "-E",
                pattern,
                "--include=*.py",
                str(search_dir),
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                # Skip excluded patterns
                if any(excl in line for excl in exclude_patterns):
                    continue
                importers.add(line)
                if pattern not in patterns_found:
                    patterns_found.append(pattern)

        except subprocess.TimeoutExpired:
            pass  # Timeout is acceptable, just skip this pattern
        except FileNotFoundError:
            pass  # grep not available
        except Exception:
            pass  # Other errors are acceptable

    return list(importers), patterns_found


def verify_orphaned_module(
    module_name: str,
    file_path: str | Path,
    project_root: str | Path,
) -> VerificationResult:
    """Verify if a module is truly orphaned using grep.

    Args:
        module_name: The module name (e.g., "utils.helpers")
        file_path: Path to the module file
        project_root: Root directory of the project

    Returns:
        VerificationResult with determination of true/false positive
    """
    importers, patterns = search_for_imports(module_name, project_root)

    # Exclude self-imports (the file importing itself)
    file_path_str = str(file_path)
    importers = [imp for imp in importers if imp != file_path_str]

    if importers:
        return VerificationResult(
            module_name=module_name,
            file_path=str(file_path),
            is_orphaned=False,
            importer_count=len(importers),
            sample_importers=importers[:5],
            import_patterns_found=patterns,
            confidence="likely_false_positive",
        )
    else:
        return VerificationResult(
            module_name=module_name,
            file_path=str(file_path),
            is_orphaned=True,
            importer_count=0,
            sample_importers=[],
            import_patterns_found=[],
            confidence="confirmed",
        )


def verify_all_orphans(
    orphaned_files: list[dict[str, object]],
    project_root: str | Path,
) -> VerificationReport:
    """Verify all reported orphaned files.

    Args:
        orphaned_files: List of orphan dicts with 'file' and 'module_name' keys
        project_root: Root directory of the project

    Returns:
        VerificationReport with false positives and confirmed orphans
    """
    false_positives: list[VerificationResult] = []
    confirmed_orphans: list[VerificationResult] = []

    for orphan in orphaned_files:
        file_path = str(orphan.get("file", ""))
        module_name = str(orphan.get("module_name", ""))

        if not file_path or not module_name:
            continue

        result = verify_orphaned_module(module_name, file_path, project_root)

        if result.is_orphaned:
            confirmed_orphans.append(result)
        else:
            false_positives.append(result)

    total = len(orphaned_files)
    fp_rate = len(false_positives) / total if total > 0 else 0

    return VerificationReport(
        total_reported_orphaned=total,
        false_positives=false_positives,
        confirmed_orphans=confirmed_orphans,
        false_positive_rate=fp_rate,
    )
