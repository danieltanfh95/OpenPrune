"""Application archetype detection."""

import ast
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from openprune.detection.entrypoints import EntrypointVisitor
from openprune.detection.linting import LintingDetector
from openprune.models.archetype import (
    ArchetypeResult,
    Entrypoint,
    FrameworkDetection,
    FrameworkType,
    LintingConfig,
)


class ArchetypeDetector:
    """Detect application frameworks and patterns."""

    # Framework indicators: import name -> framework
    IMPORT_INDICATORS: dict[str, FrameworkType] = {
        "flask": FrameworkType.FLASK,
        "Flask": FrameworkType.FLASK,
        "celery": FrameworkType.CELERY,
        "Celery": FrameworkType.CELERY,
        "fastapi": FrameworkType.FASTAPI,
        "FastAPI": FrameworkType.FASTAPI,
        "django": FrameworkType.DJANGO,
        "click": FrameworkType.CLICK,
        "typer": FrameworkType.TYPER,
    }

    # Requirements file patterns
    REQUIREMENTS_PATTERNS: dict[str, FrameworkType] = {
        "flask": FrameworkType.FLASK,
        "celery": FrameworkType.CELERY,
        "fastapi": FrameworkType.FASTAPI,
        "django": FrameworkType.DJANGO,
    }

    def __init__(self) -> None:
        self.linting_detector = LintingDetector()

    def detect(self, project_path: Path) -> ArchetypeResult:
        """Main detection entry point."""
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
        detections: dict[FrameworkType, FrameworkDetection] = {}

        # Check pyproject.toml dependencies
        pyproject = path / "pyproject.toml"
        if pyproject.exists():
            self._scan_pyproject(pyproject, detections)

        # Check requirements*.txt
        for req_file in path.glob("requirements*.txt"):
            self._scan_requirements(req_file, detections)

        # Scan Python files for imports
        for py_file in path.rglob("*.py"):
            # Skip common non-source directories
            if any(
                part in py_file.parts
                for part in ["__pycache__", ".venv", "venv", ".git", "node_modules"]
            ):
                continue
            self._scan_imports(py_file, detections)

        return list(detections.values())

    def _detect_entrypoints(self, path: Path) -> list[Entrypoint]:
        """Find all entrypoints in the project."""
        entrypoints: list[Entrypoint] = []

        for py_file in path.rglob("*.py"):
            # Skip common non-source directories
            if any(
                part in py_file.parts
                for part in ["__pycache__", ".venv", "venv", ".git", "node_modules"]
            ):
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))

                visitor = EntrypointVisitor(py_file)
                visitor.visit(tree)
                entrypoints.extend(visitor.entrypoints)
            except (SyntaxError, UnicodeDecodeError):
                continue

        return entrypoints

    def _detect_python_version(self, path: Path) -> str:
        """Detect Python version from project configuration."""
        # Try pyproject.toml
        pyproject = path / "pyproject.toml"
        if pyproject.exists():
            try:
                with open(pyproject, "rb") as f:
                    data = tomllib.load(f)
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
        self, path: Path, detections: dict[FrameworkType, FrameworkDetection]
    ) -> None:
        """Extract framework indicators from pyproject.toml."""
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            return

        # Check dependencies
        deps = data.get("project", {}).get("dependencies", [])
        deps.extend(data.get("tool", {}).get("poetry", {}).get("dependencies", {}).keys())

        for dep in deps:
            dep_name = dep.split("[")[0].split(">")[0].split("<")[0].split("=")[0].lower().strip()
            if dep_name in self.REQUIREMENTS_PATTERNS:
                fw = self.REQUIREMENTS_PATTERNS[dep_name]
                if fw not in detections:
                    detections[fw] = FrameworkDetection(
                        framework=fw,
                        confidence=0.8,
                        evidence=[f"pyproject.toml: {dep_name}"],
                    )
                else:
                    detections[fw].evidence.append(f"pyproject.toml: {dep_name}")

    def _scan_requirements(
        self, path: Path, detections: dict[FrameworkType, FrameworkDetection]
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
            pkg_name = line.split("[")[0].split(">")[0].split("<")[0].split("=")[0].lower().strip()
            if pkg_name in self.REQUIREMENTS_PATTERNS:
                fw = self.REQUIREMENTS_PATTERNS[pkg_name]
                if fw not in detections:
                    detections[fw] = FrameworkDetection(
                        framework=fw,
                        confidence=0.7,
                        evidence=[f"{path.name}: {pkg_name}"],
                    )
                else:
                    detections[fw].evidence.append(f"{path.name}: {pkg_name}")

    def _scan_imports(
        self, file: Path, detections: dict[FrameworkType, FrameworkDetection]
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
        detections: dict[FrameworkType, FrameworkDetection],
    ) -> None:
        """Check if an import indicates a framework."""
        if name in self.IMPORT_INDICATORS:
            fw = self.IMPORT_INDICATORS[name]
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

        for ep in entrypoints:
            # Flask entrypoints
            if ep.type.name.startswith("FLASK") and FrameworkType.FLASK in framework_types:
                pass  # Already marked

            # Celery entrypoints
            if ep.type.name.startswith("CELERY") and FrameworkType.CELERY in framework_types:
                pass  # Already marked
