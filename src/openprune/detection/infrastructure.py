"""Infrastructure file entrypoint detection.

Detects Python entrypoints from:
- Dockerfile (ENTRYPOINT, CMD, ENV FLASK_APP)
- docker-compose.yml (command, entrypoint)
- .gitlab-ci.yml (script sections)
- Shell scripts (.sh files)
- Procfile (Heroku)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class InfraEntrypoint:
    """An entrypoint found in infrastructure files."""

    source_file: Path  # e.g., Dockerfile
    source_line: int
    command_type: str  # "python", "gunicorn", "celery", "flask"
    target_module: str  # e.g., "src.app", "src.tasks.celery"
    target_file: Path | None  # Resolved file path if determinable


# Regex patterns to extract Python targets from commands
PYTHON_PATTERNS = [
    # python -m module.name (with optional flags before -m)
    (r"python3?\s+(?:-\w\s+)*-m\s+([\w.]+)", "python"),
    # python path/to/script.py
    (r"python3?\s+([\w/.-]+\.py)", "python"),
    # gunicorn module:app or gunicorn -c config module:app
    (r"gunicorn\s+(?:-[\w-]+(?:\s+\S+)?\s+)*([\w.]+:\w+)", "gunicorn"),
    # celery -A module.name worker/beat
    (r"celery\s+-A\s+([\w.]+)", "celery"),
    # uvicorn module:app
    (r"uvicorn\s+([\w.]+:\w+)", "uvicorn"),
]


class InfrastructureDetector:
    """Detect Python entrypoints from infrastructure files."""

    def __init__(self) -> None:
        self._flask_app_env: str | None = None

    def detect(self, project_path: Path) -> list[InfraEntrypoint]:
        """Detect all infrastructure entrypoints in a project."""
        entrypoints: list[InfraEntrypoint] = []
        entrypoints.extend(self._scan_dockerfiles(project_path))
        entrypoints.extend(self._scan_docker_compose(project_path))
        entrypoints.extend(self._scan_gitlab_ci(project_path))
        entrypoints.extend(self._scan_shell_scripts(project_path))
        entrypoints.extend(self._scan_procfiles(project_path))
        return entrypoints

    def _scan_dockerfiles(self, project_path: Path) -> list[InfraEntrypoint]:
        """Scan Dockerfile for ENTRYPOINT, CMD, and ENV FLASK_APP."""
        entrypoints: list[InfraEntrypoint] = []

        for dockerfile in project_path.glob("Dockerfile*"):
            try:
                content = dockerfile.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for line_no, line in enumerate(content.splitlines(), 1):
                line = line.strip()

                # Skip comments
                if line.startswith("#"):
                    continue

                # Check for ENV FLASK_APP=...
                if match := re.search(r"ENV\s+FLASK_APP[=\s]+([\w/.]+)", line):
                    self._flask_app_env = match.group(1)
                    entrypoints.append(
                        InfraEntrypoint(
                            source_file=dockerfile,
                            source_line=line_no,
                            command_type="flask",
                            target_module=match.group(1),
                            target_file=self._resolve_target(
                                match.group(1), project_path
                            ),
                        )
                    )

                # Check for ENTRYPOINT or CMD
                if line.startswith(("ENTRYPOINT", "CMD")):
                    # Handle JSON array format: ENTRYPOINT ["python", "app.py"]
                    if match := re.search(r'\[([^\]]+)\]', line):
                        cmd_parts = [
                            p.strip().strip('"').strip("'")
                            for p in match.group(1).split(",")
                        ]
                        cmd_line = " ".join(cmd_parts)
                    else:
                        # Shell format: ENTRYPOINT python app.py
                        cmd_line = re.sub(r"^(ENTRYPOINT|CMD)\s+", "", line)

                    # Check if it's a shell script reference
                    if cmd_line.endswith(".sh"):
                        script_path = project_path / cmd_line
                        if script_path.exists():
                            entrypoints.extend(
                                self._scan_single_shell_script(script_path, project_path)
                            )
                    else:
                        # Check for Python patterns
                        for pattern, cmd_type in PYTHON_PATTERNS:
                            if match := re.search(pattern, cmd_line):
                                entrypoints.append(
                                    InfraEntrypoint(
                                        source_file=dockerfile,
                                        source_line=line_no,
                                        command_type=cmd_type,
                                        target_module=match.group(1),
                                        target_file=self._resolve_target(
                                            match.group(1), project_path
                                        ),
                                    )
                                )
                                break

        return entrypoints

    def _scan_docker_compose(self, project_path: Path) -> list[InfraEntrypoint]:
        """Scan docker-compose*.yml for command and entrypoint."""
        if not HAS_YAML:
            return []

        entrypoints: list[InfraEntrypoint] = []

        for compose_file in project_path.glob("docker-compose*.yml"):
            try:
                content = compose_file.read_text(encoding="utf-8")
                data = yaml.safe_load(content)
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                continue

            if not isinstance(data, dict):
                continue

            services = data.get("services", {})
            if not isinstance(services, dict):
                continue

            for service_name, service_config in services.items():
                if not isinstance(service_config, dict):
                    continue

                # Check entrypoint
                entrypoint = service_config.get("entrypoint")
                if entrypoint:
                    entrypoints.extend(
                        self._parse_compose_command(
                            entrypoint, compose_file, service_name, project_path
                        )
                    )

                # Check command
                command = service_config.get("command")
                if command:
                    entrypoints.extend(
                        self._parse_compose_command(
                            command, compose_file, service_name, project_path
                        )
                    )

                # Check environment for FLASK_APP
                env = service_config.get("environment", {})
                if isinstance(env, dict) and "FLASK_APP" in env:
                    flask_app = env["FLASK_APP"]
                    entrypoints.append(
                        InfraEntrypoint(
                            source_file=compose_file,
                            source_line=0,
                            command_type="flask",
                            target_module=flask_app,
                            target_file=self._resolve_target(flask_app, project_path),
                        )
                    )

        return entrypoints

    def _parse_compose_command(
        self,
        command: str | list,
        source_file: Path,
        _service_name: str,
        project_path: Path,
    ) -> list[InfraEntrypoint]:
        """Parse a docker-compose command/entrypoint value."""
        entrypoints: list[InfraEntrypoint] = []

        # Convert list to string
        if isinstance(command, list):
            cmd_line = " ".join(str(c) for c in command)
        else:
            cmd_line = str(command)

        # Check if it's a shell script reference
        if cmd_line.strip().endswith(".sh"):
            script_path = project_path / cmd_line.strip()
            if script_path.exists():
                return self._scan_single_shell_script(script_path, project_path)

        # Check for Python patterns
        for pattern, cmd_type in PYTHON_PATTERNS:
            if match := re.search(pattern, cmd_line):
                entrypoints.append(
                    InfraEntrypoint(
                        source_file=source_file,
                        source_line=0,
                        command_type=cmd_type,
                        target_module=match.group(1),
                        target_file=self._resolve_target(match.group(1), project_path),
                    )
                )

        return entrypoints

    def _scan_gitlab_ci(self, project_path: Path) -> list[InfraEntrypoint]:
        """Scan .gitlab-ci.yml for Python commands in script sections."""
        if not HAS_YAML:
            return []

        entrypoints: list[InfraEntrypoint] = []
        ci_file = project_path / ".gitlab-ci.yml"

        if not ci_file.exists():
            return []

        try:
            content = ci_file.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return []

        if not isinstance(data, dict):
            return []

        # Recursively find all 'script' keys
        entrypoints.extend(
            self._extract_scripts_from_yaml(data, ci_file, project_path)
        )

        return entrypoints

    def _extract_scripts_from_yaml(
        self, data: dict, source_file: Path, project_path: Path
    ) -> list[InfraEntrypoint]:
        """Recursively extract Python commands from YAML script sections."""
        entrypoints: list[InfraEntrypoint] = []

        for key, value in data.items():
            if key == "script" and isinstance(value, list):
                for script_line in value:
                    if not isinstance(script_line, str):
                        continue
                    for pattern, cmd_type in PYTHON_PATTERNS:
                        if match := re.search(pattern, script_line):
                            entrypoints.append(
                                InfraEntrypoint(
                                    source_file=source_file,
                                    source_line=0,
                                    command_type=cmd_type,
                                    target_module=match.group(1),
                                    target_file=self._resolve_target(
                                        match.group(1), project_path
                                    ),
                                )
                            )
            elif isinstance(value, dict):
                entrypoints.extend(
                    self._extract_scripts_from_yaml(value, source_file, project_path)
                )

        return entrypoints

    def _scan_shell_scripts(self, project_path: Path) -> list[InfraEntrypoint]:
        """Scan .sh files for Python commands."""
        entrypoints: list[InfraEntrypoint] = []

        for sh_file in project_path.rglob("*.sh"):
            # Skip common non-source directories
            if any(
                part in sh_file.parts
                for part in ["__pycache__", ".venv", "venv", ".git", "node_modules"]
            ):
                continue

            entrypoints.extend(
                self._scan_single_shell_script(sh_file, project_path)
            )

        return entrypoints

    def _scan_single_shell_script(
        self, sh_file: Path, project_path: Path
    ) -> list[InfraEntrypoint]:
        """Scan a single shell script for Python commands."""
        entrypoints: list[InfraEntrypoint] = []

        try:
            content = sh_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        for line_no, line in enumerate(content.splitlines(), 1):
            line = line.strip()

            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            for pattern, cmd_type in PYTHON_PATTERNS:
                if match := re.search(pattern, line):
                    entrypoints.append(
                        InfraEntrypoint(
                            source_file=sh_file,
                            source_line=line_no,
                            command_type=cmd_type,
                            target_module=match.group(1),
                            target_file=self._resolve_target(
                                match.group(1), project_path
                            ),
                        )
                    )

        return entrypoints

    def _scan_procfiles(self, project_path: Path) -> list[InfraEntrypoint]:
        """Scan Procfile for Python commands."""
        entrypoints: list[InfraEntrypoint] = []
        procfile = project_path / "Procfile"

        if not procfile.exists():
            return []

        try:
            content = procfile.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        for line_no, line in enumerate(content.splitlines(), 1):
            line = line.strip()

            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            # Procfile format: process_type: command
            if ":" in line:
                cmd_line = line.split(":", 1)[1].strip()

                for pattern, cmd_type in PYTHON_PATTERNS:
                    if match := re.search(pattern, cmd_line):
                        entrypoints.append(
                            InfraEntrypoint(
                                source_file=procfile,
                                source_line=line_no,
                                command_type=cmd_type,
                                target_module=match.group(1),
                                target_file=self._resolve_target(
                                    match.group(1), project_path
                                ),
                            )
                        )

        return entrypoints

    def _resolve_target(self, target: str, project_root: Path) -> Path | None:
        """Convert a module string or file path to a file path."""
        # Handle module:object syntax (gunicorn/uvicorn)
        if ":" in target:
            target = target.split(":")[0]

        # If it's already a file path
        if target.endswith(".py"):
            file_path = project_root / target
            if file_path.exists():
                return file_path
            return None

        # Convert dots to path separators for module paths
        rel_path = target.replace(".", "/")

        # Try as file (e.g., src.app -> src/app.py)
        file_path = project_root / f"{rel_path}.py"
        if file_path.exists():
            return file_path

        # Try as package (e.g., src.tasks -> src/tasks/__init__.py)
        pkg_path = project_root / rel_path / "__init__.py"
        if pkg_path.exists():
            return pkg_path

        # Try progressively shorter paths to handle module.attribute patterns
        # e.g., src.tasks.celery could be src/tasks.py with celery attribute
        parts = target.split(".")
        for i in range(len(parts) - 1, 0, -1):
            partial_path = "/".join(parts[:i])
            file_path = project_root / f"{partial_path}.py"
            if file_path.exists():
                return file_path
            pkg_path = project_root / partial_path / "__init__.py"
            if pkg_path.exists():
                return pkg_path

        return None
