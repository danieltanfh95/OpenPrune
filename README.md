# OpenPrune

Interactive CLI for detecting dead code in Python Flask+Celery applications.

## Installation

```bash
pip install openprune
```

## Usage

```bash
# Interactive mode (recommended)
openprune ./my-project

# Just detect frameworks and generate config
openprune detect ./my-project

# Run analysis with existing config
openprune analyze ./my-project

# Show results from previous run
openprune show openprune-results.json
```

## Features

- Detects Flask routes, Celery tasks, and other framework entrypoints
- AST-based analysis for accurate dead code detection
- Suspicion scoring based on multiple factors:
  - Symbol usage across files
  - Framework decorator patterns
  - File age (git commit history or mtime)
- Interactive CLI with Rich output
- JSON output for integration with other tools
