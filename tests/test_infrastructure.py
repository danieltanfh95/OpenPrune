"""Tests for infrastructure file entrypoint detection."""

import tempfile
from pathlib import Path

import pytest

from openprune.detection.infrastructure import InfrastructureDetector


class TestDockerfileDetection:
    """Tests for Dockerfile parsing."""

    def test_detects_python_entrypoint(self, tmp_path: Path) -> None:
        """Detect ENTRYPOINT with python command."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text('ENTRYPOINT ["python", "app.py"]')

        # Create the target file
        (tmp_path / "app.py").write_text("# app")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "python"
        assert entrypoints[0].target_module == "app.py"
        assert entrypoints[0].target_file == tmp_path / "app.py"

    def test_detects_gunicorn_entrypoint(self, tmp_path: Path) -> None:
        """Detect gunicorn module:app syntax."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text('ENTRYPOINT ["gunicorn", "-c", "config.py", "src.app:app"]')

        # Create the target file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("app = Flask(__name__)")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "gunicorn"
        assert entrypoints[0].target_module == "src.app:app"
        assert entrypoints[0].target_file == src_dir / "app.py"

    def test_detects_celery_command(self, tmp_path: Path) -> None:
        """Detect celery -A module worker command."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text('CMD ["celery", "-A", "tasks.celery", "worker"]')

        # Create the target file
        (tmp_path / "tasks.py").write_text("celery = Celery()")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "celery"
        assert entrypoints[0].target_module == "tasks.celery"
        assert entrypoints[0].target_file == tmp_path / "tasks.py"

    def test_detects_flask_app_env(self, tmp_path: Path) -> None:
        """Detect ENV FLASK_APP=path."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("ENV FLASK_APP=src/app.py")

        # Create the target file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("app = Flask(__name__)")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "flask"
        assert entrypoints[0].target_module == "src/app.py"

    def test_follows_shell_script_entrypoint(self, tmp_path: Path) -> None:
        """Follow shell script referenced in ENTRYPOINT."""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text('ENTRYPOINT ["run.sh"]')

        # Create shell script that runs Python
        (tmp_path / "run.sh").write_text("#!/bin/bash\npython -m myapp.main")

        # Create the target file
        myapp_dir = tmp_path / "myapp"
        myapp_dir.mkdir()
        (myapp_dir / "main.py").write_text("# main")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        # Should find the python command inside the shell script
        python_eps = [e for e in entrypoints if e.command_type == "python"]
        assert len(python_eps) >= 1
        assert any(e.target_module == "myapp.main" for e in python_eps)


class TestShellScriptDetection:
    """Tests for shell script parsing."""

    def test_detects_python_m_module(self, tmp_path: Path) -> None:
        """Detect python -m module.name."""
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/bash\npython -m mymodule.app")

        # Create target
        mymodule_dir = tmp_path / "mymodule"
        mymodule_dir.mkdir()
        (mymodule_dir / "app.py").write_text("# app")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "python"
        assert entrypoints[0].target_module == "mymodule.app"

    def test_detects_python_script_path(self, tmp_path: Path) -> None:
        """Detect python path/to/script.py."""
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/bash\npython src/worker.py")

        # Create target
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "worker.py").write_text("# worker")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "python"
        assert entrypoints[0].target_module == "src/worker.py"

    def test_skips_comments(self, tmp_path: Path) -> None:
        """Skip commented lines in shell scripts."""
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/bash\n# python -m old.app\npython -m new.app")

        # Create target
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (new_dir / "app.py").write_text("# app")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].target_module == "new.app"

    def test_detects_celery_beat(self, tmp_path: Path) -> None:
        """Detect celery beat command."""
        script = tmp_path / "run_scheduler.sh"
        script.write_text("#!/bin/bash\ncelery -A src.tasks.celery beat --pidfile=")

        # Create target
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "tasks.py").write_text("celery = Celery()")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "celery"
        assert entrypoints[0].target_module == "src.tasks.celery"


class TestDockerComposeDetection:
    """Tests for docker-compose.yml parsing."""

    def test_detects_command_list(self, tmp_path: Path) -> None:
        """Detect command as list."""
        pytest.importorskip("yaml")

        compose = tmp_path / "docker-compose.yml"
        compose.write_text("""
services:
  api:
    command: ["python", "-m", "flask", "run"]
""")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        python_eps = [e for e in entrypoints if e.command_type == "python"]
        assert len(python_eps) == 1
        assert python_eps[0].target_module == "flask"

    def test_detects_entrypoint_string(self, tmp_path: Path) -> None:
        """Detect entrypoint as string."""
        pytest.importorskip("yaml")

        compose = tmp_path / "docker-compose.yml"
        compose.write_text("""
services:
  worker:
    entrypoint: python worker.py
""")

        # Create target
        (tmp_path / "worker.py").write_text("# worker")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].target_module == "worker.py"

    def test_follows_shell_script_entrypoint(self, tmp_path: Path) -> None:
        """Follow shell script in docker-compose entrypoint."""
        pytest.importorskip("yaml")

        compose = tmp_path / "docker-compose.yml"
        compose.write_text("""
services:
  scheduler:
    entrypoint: run_scheduler.sh
""")

        # Create shell script
        (tmp_path / "run_scheduler.sh").write_text("#!/bin/bash\npython -m celery -A tasks beat")

        # Create target
        (tmp_path / "tasks.py").write_text("# tasks")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        # Should find celery command from the shell script
        celery_eps = [e for e in entrypoints if e.command_type == "celery"]
        assert len(celery_eps) >= 1


class TestProcfileDetection:
    """Tests for Procfile parsing."""

    def test_detects_web_gunicorn(self, tmp_path: Path) -> None:
        """Detect gunicorn in Procfile."""
        procfile = tmp_path / "Procfile"
        procfile.write_text("web: gunicorn app:application")

        # Create target
        (tmp_path / "app.py").write_text("application = Flask(__name__)")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "gunicorn"
        assert entrypoints[0].target_module == "app:application"

    def test_detects_worker_celery(self, tmp_path: Path) -> None:
        """Detect celery worker in Procfile."""
        procfile = tmp_path / "Procfile"
        procfile.write_text("worker: celery -A tasks.celery worker")

        # Create target
        (tmp_path / "tasks.py").write_text("celery = Celery()")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        assert len(entrypoints) == 1
        assert entrypoints[0].command_type == "celery"


class TestModuleResolution:
    """Tests for module path resolution."""

    def test_resolves_simple_module(self, tmp_path: Path) -> None:
        """Resolve simple module name to file."""
        (tmp_path / "app.py").write_text("# app")

        detector = InfrastructureDetector()
        result = detector._resolve_target("app", tmp_path)

        assert result == tmp_path / "app.py"

    def test_resolves_dotted_module(self, tmp_path: Path) -> None:
        """Resolve dotted module path to file."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("# app")

        detector = InfrastructureDetector()
        result = detector._resolve_target("src.app", tmp_path)

        assert result == src_dir / "app.py"

    def test_resolves_module_with_attribute(self, tmp_path: Path) -> None:
        """Resolve module.attribute pattern (e.g., src.tasks.celery)."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "tasks.py").write_text("celery = Celery()")

        detector = InfrastructureDetector()
        result = detector._resolve_target("src.tasks.celery", tmp_path)

        assert result == src_dir / "tasks.py"

    def test_resolves_gunicorn_syntax(self, tmp_path: Path) -> None:
        """Resolve module:object syntax."""
        (tmp_path / "app.py").write_text("application = Flask(__name__)")

        detector = InfrastructureDetector()
        result = detector._resolve_target("app:application", tmp_path)

        assert result == tmp_path / "app.py"

    def test_resolves_package_init(self, tmp_path: Path) -> None:
        """Resolve package to __init__.py."""
        pkg_dir = tmp_path / "mypackage"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("# package")

        detector = InfrastructureDetector()
        result = detector._resolve_target("mypackage", tmp_path)

        assert result == pkg_dir / "__init__.py"

    def test_returns_none_for_stdlib(self, tmp_path: Path) -> None:
        """Return None for stdlib modules that don't exist locally."""
        detector = InfrastructureDetector()
        result = detector._resolve_target("flask", tmp_path)

        assert result is None

    def test_resolves_direct_file_path(self, tmp_path: Path) -> None:
        """Resolve direct .py file path."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "worker.py").write_text("# worker")

        detector = InfrastructureDetector()
        result = detector._resolve_target("src/worker.py", tmp_path)

        assert result == src_dir / "worker.py"


class TestGitlabCIDetection:
    """Tests for .gitlab-ci.yml parsing."""

    def test_detects_script_python_commands(self, tmp_path: Path) -> None:
        """Detect Python commands in script sections."""
        pytest.importorskip("yaml")

        ci_file = tmp_path / ".gitlab-ci.yml"
        ci_file.write_text("""
test-job:
  script:
    - python -m pytest ./tests/
    - python manage.py migrate
""")

        # Create targets
        (tmp_path / "manage.py").write_text("# django manage")

        detector = InfrastructureDetector()
        entrypoints = detector.detect(tmp_path)

        # Should detect pytest and manage.py
        modules = [e.target_module for e in entrypoints]
        assert "pytest" in modules
        assert "manage.py" in modules
