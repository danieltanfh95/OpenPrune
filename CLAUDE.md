# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
poetry install

# Run tests
poetry run pytest                         # All tests with coverage
poetry run pytest tests/test_visitor.py   # Single test file
poetry run pytest -k "test_name"          # Single test by name

# Linting
poetry run ruff check src/
poetry run ruff check --fix src/          # Auto-fix issues

# Run the CLI
poetry run openprune run ./path/to/project        # Full pipeline (detect + analyze)
poetry run openprune detect ./path/to/project     # Generate .openprune/config.json
poetry run openprune analyze ./path/to/project    # Generate .openprune/results.json
poetry run openprune verify ./path/to/project     # LLM verification (interactive)
poetry run openprune verify ./path --batch        # LLM verification (oneshot)

# Run on external project (cd to target, use its poetry environment)
cd /path/to/target && poetry run openprune . detect
cd /path/to/target && poetry run openprune . analyze
```

## Coding Conventions

- **No TYPE_CHECKING guards** — Import dependencies directly, even for type annotations
- **No try/except import hacks** — Add proper dependencies to pyproject.toml instead of conditional imports
- **Use tomli** — Always use `import tomli` for TOML parsing (it's a dependency for all Python versions)
- **Ruff for linting** — Run `poetry run ruff check --fix src/` before committing

## Architecture

OpenPrune is a dead code detector for Python Flask+Celery apps that uses AST analysis followed by optional LLM verification.

### Three-Phase Pipeline

```
detect → analyze → verify
   ↓         ↓         ↓
config.json  results.json  verified.json
```

All outputs go to `.openprune/` directory in the target project.

### Core Data Flow

1. **Detection** (`src/openprune/detection/`)
   - `ArchetypeDetector` scans for framework imports (Flask, Celery, FastAPI, etc.)
   - `detect_entrypoints()` finds decorated functions via plugins (`@app.route`, `@celery.task`, etc.)
   - `LintingDetector` reads pyproject.toml/ruff.toml for noqa patterns

2. **Analysis** (`src/openprune/analysis/`)
   - `DeadCodeVisitor` (visitor.py) walks AST to collect all `Symbol` definitions and `Usage` references
   - Tracks `caller` field in `Usage` to build call graphs for reachability analysis
   - `SuspicionScorer` (scoring.py) assigns confidence scores 0-100 based on usage patterns

3. **Verification** (`src/openprune/verification/`)
   - `batch.py` - Oneshot mode: sends all items + file contents in single LLM prompt
   - `session.py` - Interactive mode: exec's into LLM CLI with system prompt

### Key Models (`src/openprune/models/`)

- `Symbol` - A Python definition (function, class, variable, import) with location and decorators
- `Usage` - A reference to a symbol with context (call, import, attribute) and caller
- `DependencyNode` - Wraps Symbol with scoring results (confidence, reasons)
- `DeadCodeItem` - Final output item with confidence and suggested action

### Entrypoint-Based Reachability

The analyzer builds a call graph from `Usage.caller` fields, then walks from entrypoints (functions with framework decorators) to find reachable symbols. Unreachable symbols get +30 confidence boost. Entire orphaned files (not imported by any reachable module) get 100% confidence.

### Confidence Scoring

- 100%: Orphaned file or unreachable from entrypoints
- 80-99%: High confidence dead code
- 50-79%: Medium - needs verification
- 0-49%: Low - likely used (0% = entrypoint)

Penalties reduce confidence: dunder methods (-40), entrypoint decorators (-20), name found in usages (-40).

### Test Fixtures

`tests/fixtures/flask_app/` contains a sample Flask+Celery app with intentional dead code for testing. Running `openprune analyze` on it should find ~42 items across confidence levels.
