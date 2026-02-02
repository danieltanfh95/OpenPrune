"""Application archetype detection."""

import ast
import sys
from pathlib import Path

import tomli

from openprune.detection.entrypoints import detect_entrypoints
from openprune.detection.infrastructure import InfrastructureDetector
from openprune.detection.linting import LintingDetector
from openprune.exclusion import FileExcluder
from openprune.models.archetype import (
    ArchetypeResult,
    Entrypoint,
    EntrypointType,
    FrameworkDetection,
)
from openprune.plugins import get_registry


def _get_import_indicators() -> dict[str, str]:
    """Get import indicators from plugins plus base patterns."""
    # Start with plugin-provided indicators
    indicators = get_registry().get_all_import_indicators()

    # Add base patterns for frameworks without plugins yet
    base_patterns = {
        "fastapi": "fastapi",
        "FastAPI": "fastapi",
        "django": "django",
        "click": "click",
        "typer": "typer",
    }
    for name, fw in base_patterns.items():
        if name not in indicators:
            indicators[name] = fw

    return indicators


def _get_requirements_patterns() -> dict[str, str]:
    """Get requirements patterns from plugins plus base patterns."""
    # Note: flask-restplus and flask-restx now have their own type via plugin
    patterns: dict[str, str] = {
        "flask": "flask",
        "flask-restplus": "flask_restplus",
        "flask-restx": "flask_restplus",
        "celery": "celery",
        "fastapi": "fastapi",
        "django": "django",
    }
    return patterns


class ArchetypeDetector:
    """Detect application frameworks and patterns."""

    def __init__(self, include_ignored: bool = False) -> None:
        self._include_ignored = include_ignored
        self.linting_detector = LintingDetector()
        self.infra_detector = InfrastructureDetector(include_ignored=include_ignored)
        # Get indicators from plugins
        self._import_indicators = _get_import_indicators()
        self._requirements_patterns = _get_requirements_patterns()

    def detect(self, project_path: Path) -> ArchetypeResult:
        """Main detection entry point."""
        # Create excluder for this detection run
        self._excluder = FileExcluder(
            project_path, include_ignored=self._include_ignored
        )

        frameworks = self._detect_frameworks(project_path)
        entrypoints = self._detect_entrypoints(project_path)
        linting = self.linting_detector.detect(project_path)
        python_ver = self._detect_python_version(project_path)

        # Mark entrypoints based on detected frameworks
        self._mark_framework_entrypoints(entrypoints, frameworks)

        return ArchetypeResult(
            frameworks=frameworks,
            entrypoints=entrypoints,
            linting_config=linting,
            python_version=python_ver,
            project_root=project_path,
        )

    def _detect_frameworks(self, path: Path) -> list[FrameworkDetection]:
        """Scan imports and requirements for framework indicators."""
        detections: dict[str, FrameworkDetection] = {}

        # Check pyproject.toml dependencies
        pyproject = path / "pyproject.toml"
        if pyproject.exists():
            self._scan_pyproject(pyproject, detections)

        # Check requirements*.txt
        for req_file in path.glob("requirements*.txt"):
            self._scan_requirements(req_file, detections)

        # Scan Python files for imports
        for py_file in path.rglob("*.py"):
            # Use FileExcluder for exclusion logic
            if self._excluder.should_exclude(py_file):
                continue
            self._scan_imports(py_file, detections)

        return list(detections.values())

    def _detect_entrypoints(self, path: Path) -> list[Entrypoint]:
        """Find all entrypoints in the project using plugins and infrastructure files."""
        entrypoints: list[Entrypoint] = []

        # Initialize plugins with project root for CI-aware detection
        registry = get_registry()
        for plugin in registry.all_plugins():
            if hasattr(plugin, "set_project_root"):
                plugin.set_project_root(path)

        # Detect from Python files using plugins
        for py_file in path.rglob("*.py"):
            # Use FileExcluder for exclusion logic
            if self._excluder.should_exclude(py_file):
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))

                # Use plugin-based detection
                file_entrypoints = detect_entrypoints(tree, py_file)
                entrypoints.extend(file_entrypoints)
            except (SyntaxError, UnicodeDecodeError):
                continue

        # Detect from infrastructure files (Dockerfile, docker-compose, etc.)
        infra_entrypoints = self.infra_detector.detect(path)
        for ie in infra_entrypoints:
            if ie.target_file and ie.target_file.exists():
                # Determine entrypoint type based on command type
                ep_type = (
                    EntrypointType.SCRIPT_ENTRYPOINT
                    if ie.target_module.endswith(".py")
                    else EntrypointType.INFRA_ENTRYPOINT
                )
                entrypoints.append(
                    Entrypoint(
                        type=ep_type,
                        name=ie.target_module,
                        file=ie.target_file,
                        line=1,  # Module-level entrypoint
                    )
                )

        return entrypoints

    def _detect_python_version(self, path: Path) -> str:
        """Detect Python version from project configuration."""
        # Try pyproject.toml
        pyproject = path / "pyproject.toml"
        if pyproject.exists():
            try:
                with open(pyproject, "rb") as f:
                    data = tomli.load(f)
                requires = data.get("project", {}).get("requires-python", "")
                if requires:
                    # Extract version from ">=3.11" or similar
                    for part in requires.replace(">=", "").replace("<=", "").split(","):
                        part = part.strip()
                        if part and part[0].isdigit():
                            return part
            except Exception:
                pass

        # Fall back to current Python version
        return f"{sys.version_info.major}.{sys.version_info.minor}"

    def _scan_pyproject(
        self, path: Path, detections: dict[str, FrameworkDetection]
    ) -> None:
        """Extract framework indicators from pyproject.toml."""
        try:
            with open(path, "rb") as f:
                data = tomli.load(f)
        except Exception:
            return

        # Check dependencies
        deps = data.get("project", {}).get("dependencies", [])
        deps.extend(
            data.get("tool", {}).get("poetry", {}).get("dependencies", {}).keys()
        )

        for dep in deps:
            dep_name = (
                dep.split("[")[0]
                .split(">")[0]
                .split("<")[0]
                .split("=")[0]
                .lower()
                .strip()
            )
            if dep_name in self._requirements_patterns:
                fw = self._requirements_patterns[dep_name]
                if fw not in detections:
                    detections[fw] = FrameworkDetection(
                        framework=fw,
                        confidence=0.8,
                        evidence=[f"pyproject.toml: {dep_name}"],
                    )
                else:
                    detections[fw].evidence.append(f"pyproject.toml: {dep_name}")

    def _scan_requirements(
        self, path: Path, detections: dict[str, FrameworkDetection]
    ) -> None:
        """Scan requirements.txt for framework indicators."""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Extract package name
            pkg_name = (
                line.split("[")[0]
                .split(">")[0]
                .split("<")[0]
                .split("=")[0]
                .lower()
                .strip()
            )
            if pkg_name in self._requirements_patterns:
                fw = self._requirements_patterns[pkg_name]
                if fw not in detections:
                    detections[fw] = FrameworkDetection(
                        framework=fw,
                        confidence=0.7,
                        evidence=[f"{path.name}: {pkg_name}"],
                    )
                else:
                    detections[fw].evidence.append(f"{path.name}: {pkg_name}")

    def _scan_imports(
        self, file: Path, detections: dict[str, FrameworkDetection]
    ) -> None:
        """Parse a Python file and extract import statements."""
        try:
            source = file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return

        for node in ast.walk(tree):
            match node:
                case ast.Import(names=names):
                    for alias in names:
                        self._check_import(alias.name, file, detections)
                case ast.ImportFrom(module=module) if module:
                    self._check_import(module.split(".")[0], file, detections)

    def _check_import(
        self,
        name: str,
        file: Path,
        detections: dict[str, FrameworkDetection],
    ) -> None:
        """Check if an import indicates a framework."""
        if name in self._import_indicators:
            fw = self._import_indicators[name]
            if fw not in detections:
                detections[fw] = FrameworkDetection(
                    framework=fw,
                    confidence=0.0,
                    evidence=[],
                )
            detections[fw].evidence.append(str(file))
            detections[fw].confidence = min(1.0, detections[fw].confidence + 0.1)

    def _mark_framework_entrypoints(
        self, entrypoints: list[Entrypoint], frameworks: list[FrameworkDetection]
    ) -> None:
        """Mark entrypoints that belong to detected frameworks."""
        framework_types = {fw.framework for fw in frameworks}

        # Check for flask-related frameworks (flask, flask_restplus, etc.)
        has_flask = any(fw.startswith("flask") for fw in framework_types)
        has_celery = "celery" in framework_types

        for ep in entrypoints:
            # Flask entrypoints
            if ep.type.name.startswith("FLASK") and has_flask:
                pass  # Already marked

            # Celery entrypoints
            if ep.type.name.startswith("CELERY") and has_celery:
                pass  # Already marked
