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
- Celery (tasks, shared tasks)
- Click CLI commands
- Main blocks (`if __name__ == "__main__"`)

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
    "ignore_decorators": ["@pytest.fixture", "@abstractmethod"],
    "ignore_names": ["_*", "__*__", "test_*"]
  }
}
```

## License

MIT
