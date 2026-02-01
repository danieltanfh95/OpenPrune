# OpenPrune

Dead code detection for Python Flask+Celery applications with LLM-assisted verification.

## Rationale

Existing tools like `vulture`, `deadcode`, and `flake8` don't adequately handle framework-specific patterns (Flask routes, Celery tasks).

OpenPrune:

1. **Auto-detects** Python app types to find entrypoints (Flask routes, Celery tasks, CLI commands)
2. **Performs AST-level analysis** to trace symbol usage from entrypoints
3. **Hands off to an LLM** for verification—static analysis does the heavy lifting, LLM resolves edge cases

This approach avoids flaky context engineering and token wastage from feeding entire codebases to LLMs.

## How It Works

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   detect    │ ──▶ │   analyze   │ ──▶ │   verify    │
└─────────────┘     └─────────────┘     └─────────────┘
      │                   │                   │
      ▼                   ▼                   ▼
 config.json         results.json       verified.json
```

1. **Detection** — Scans for framework patterns (Flask `@app.route`, Celery `@task`, Click commands) and identifies entrypoints. Outputs `.openprune/config.json`.

2. **Analysis** — Builds an AST-based dependency graph starting from entrypoints. Walks imports and function calls to find orphaned symbols. Scores each candidate by:
   - Reference count (zero usages = high confidence)
   - Import relationships (not imported elsewhere)
   - Git history (files with no recent commits)
   - Framework patterns (missing expected decorators)

3. **Verification** — Launches an LLM session with pre-built context. The LLM reviews candidates and marks each as DELETE, KEEP, or UNCERTAIN. This catches dynamic patterns that static analysis misses.

## Limitations

Static analysis cannot detect:

- **Dynamic imports** — `importlib.import_module()`, `__import__()`
- **Reflection** — `getattr(obj, "method_name")`, `globals()["func"]()`
- **String-based references** — Template engines, ORM column names, serializers
- **Plugin systems** — Entry points loaded by external code
- **Monkey patching** — Runtime modifications to classes/modules

This is why LLM verification exists—the LLM can reason about whether a symbol might be used dynamically based on naming conventions, surrounding code, and framework patterns.

## Workflow

```
openprune detect  → .openprune/config.json   # Detect frameworks & entrypoints
openprune analyze → .openprune/results.json  # Find dead code candidates
openprune verify  → .openprune/verified.json # LLM-assisted verification
```

## Installation

```bash
pip install openprune
```

## Quick Start

```bash
# Run full pipeline (detect + analyze)
openprune run ./my-project

# Or step by step:
openprune detect ./my-project   # Generate config
openprune analyze ./my-project  # Find dead code
openprune verify ./my-project   # Verify with LLM
```

## Commands

### `openprune run`

Run full detection and analysis in one step.

```bash
openprune run ./my-project
openprune run ./my-project --verbose  # Show detailed results
```

### `openprune detect`

Detect frameworks and generate `.openprune/config.json`.

```bash
openprune detect ./my-project
```

Detects:
- Flask (routes, blueprints, CLI commands)
- Flask-RESTPlus/Flask-RESTX (Resource classes, HTTP methods)
- Celery (tasks, shared tasks)
- Click CLI commands
- Main blocks (`if __name__ == "__main__"`)
- Infrastructure files (Dockerfile, docker-compose.yml, .gitlab-ci.yml, shell scripts)

### `openprune analyze`

Analyze code using existing config to find dead code candidates.

```bash
openprune analyze ./my-project
openprune analyze ./my-project --verbose  # Show all candidates
```

Outputs `.openprune/results.json` with confidence scores (0–100%).

### `openprune verify`

Launch an LLM session to verify dead code candidates.

```bash
# Interactive mode (default) — drops into LLM session
openprune verify ./my-project

# Use a different LLM CLI
openprune verify ./my-project --llm kimi
openprune verify ./my-project --llm opencode

# Non-interactive batch mode
openprune verify ./my-project --batch

# Preview what would be sent to LLM
openprune verify ./my-project --dry-run

# Only verify high-confidence items
openprune verify ./my-project --min-confidence 80
```

**Supported LLM CLIs:**
- `claude` (default) — Anthropic's Claude Code CLI
- `kimi` — Kimi CLI
- `opencode` — OpenCode CLI

The LLM receives context about OpenPrune and can read/write files in `.openprune/`.

### `openprune show`

Display results from a previous analysis.

```bash
openprune show                           # Show .openprune/results.json
openprune show ./path/to/results.json    # Show specific file
openprune show --verbose                 # Detailed view
```

## Output Files

All outputs are stored in the `.openprune/` directory:

| File            | Description                                                  |
| --------------- | ------------------------------------------------------------ |
| `config.json`   | Detected frameworks, entrypoints, and analysis settings      |
| `results.json`  | Dead code candidates with confidence scores and reasons      |
| `verified.json` | LLM verification results with DELETE/KEEP/UNCERTAIN verdicts |

## Confidence Scoring

Each dead code candidate receives a confidence score based on:

- **No references found** — Symbol has zero usages
- **No external imports** — Not imported by other modules
- **Framework patterns** — Missing expected decorators
- **File age** — Old files with no recent commits

Higher confidence = more likely to be truly dead code.

## Example Output

```
╭─────────────────── Dead Code Summary ───────────────────╮
│                                                         │
│  Total candidates     12                                │
│  High confidence      5                                 │
│  Medium confidence    4                                 │
│  Low confidence       3                                 │
│                                                         │
│  Estimated removable  ~450 lines                        │
│                                                         │
╰─────────────────────────────────────────────────────────╯
```

## Noqa Support

OpenPrune respects `# noqa` comments, which is essential for:

- **Celery task registration** — Importing tasks to register them with the Celery app
- **Side-effect imports** — Modules that perform setup when imported
- **Re-exports** — Public API modules that import and expose symbols

```python
# These imports won't be flagged as dead code:
from app.tasks import send_email  # noqa: F401
import celery_config  # noqa
from typing import TYPE_CHECKING  # type: ignore
```

**Supported patterns:**
- `# noqa` — Suppress all checks
- `# noqa: F401` — Suppress specific code(s)
- `# type: ignore` — Type checking suppression

To disable noqa handling, set `respect_noqa: false` in the config.

## Configuration

Edit `.openprune/config.json` to customize:

```json
{
  "analysis": {
    "include": ["**/*.py"],
    "exclude": ["**/tests/**", "**/__pycache__/**"]
  },
  "linting": {
    "respect_noqa": true,
    "noqa_patterns": ["# noqa", "# type: ignore"],
    "ignore_decorators": ["@pytest.fixture", "@abstractmethod", "@property"]
  }
}
```

## Plugin System

OpenPrune uses a plugin architecture for framework detection. Built-in plugins include:

| Plugin | Detects |
|--------|---------|
| `flask` | `@app.route()`, `@bp.route()`, hooks, error handlers, CLI commands |
| `flask-restplus` | `Resource` subclasses, HTTP methods (`get`, `post`, etc.), `api.add_resource()` |
| `celery` | `@app.task`, `@shared_task`, signal handlers |

### Flask-RESTPlus/Flask-RESTX Support

OpenPrune automatically detects Flask-RESTPlus patterns:

```python
from flask_restplus import Resource

class UserResource(Resource):
    def get(self, user_id):      # Detected as entrypoint
        return get_user(user_id)

    def put(self, user_id):      # Detected as entrypoint
        return update_user(user_id)

# Route registration also detected
api.add_resource(UserResource, "/users/<user_id>")
```

HTTP methods (`get`, `post`, `put`, `delete`, `patch`, `head`, `options`) on `Resource` subclasses are automatically recognized as entrypoints and won't be flagged as dead code.

### Infrastructure File Detection

OpenPrune scans infrastructure configuration files to discover Python entrypoints that aren't visible through code analysis alone:

| File Type | Patterns Detected |
|-----------|-------------------|
| `Dockerfile` | `ENTRYPOINT`, `CMD`, `ENV FLASK_APP` |
| `docker-compose*.yml` | `command`, `entrypoint`, environment variables |
| `.gitlab-ci.yml` | `script` sections with Python commands |
| Shell scripts (`.sh`) | `python`, `gunicorn`, `celery`, `uvicorn` commands |
| `Procfile` | Heroku process definitions |

**Example patterns detected:**

```dockerfile
# Dockerfile
ENTRYPOINT ["gunicorn", "-c", "config.py", "src.app:app"]
CMD ["celery", "-A", "tasks.celery", "worker"]
ENV FLASK_APP=src/app.py
```

```yaml
# docker-compose.yml
services:
  api:
    command: ["python", "-m", "flask", "run"]
  worker:
    entrypoint: src/run_worker.sh
```

```bash
# run_scheduler.sh
python -m celery -A src.tasks.celery beat
python src/run_worker.py
```

Shell scripts referenced by `ENTRYPOINT` or `entrypoint` are automatically followed to extract the actual Python commands.

## License

MIT
