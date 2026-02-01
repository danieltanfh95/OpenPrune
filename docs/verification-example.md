# OpenPrune Verification Example

This document shows the comparison between static analysis (`results.json`) and LLM verification (`verified.json`) for the `tests/fixtures/flask_app` example project.

## Summary

| Metric | Value |
|--------|-------|
| Total items analyzed | 42 |
| Orphaned files | 3 (deprecated.py, \_\_init\_\_.py, helpers.py) |
| LLM verdict: DELETE | 27 |
| LLM verdict: KEEP | 15 |
| LLM verdict: UNCERTAIN | 0 |

## Results by Confidence Level

| Confidence | Count | LLM Action |
|------------|-------|------------|
| **100%** (orphaned files) | 16 | All DELETE |
| **80-99%** (high) | 10 | 8 DELETE, 2 KEEP |
| **50-79%** (medium) | 4 | 3 DELETE, 1 KEEP |
| **0-49%** (low/entrypoints) | 12 | All KEEP |

## False Positives Caught by LLM

These items had high static analysis confidence but were correctly identified by the LLM as needing to be kept:

| Name | Confidence | Reason for KEEP |
|------|------------|-----------------|
| `app` | 90% | Flask app instance used for `@app.route` decorators |
| `celery` | 90% | Celery app instance used for `@celery.task` decorators |
| `Celery` | 80% | Import used to create celery instance on line 5 |
| `shared_task` | 80% | Import used as decorator on `process_data` |

## Entrypoints Correctly Preserved

All items with 0% confidence (entrypoints) were correctly marked as KEEP:

| Name | Type | Decorator |
|------|------|-----------|
| `index` | Flask route | `@app.route('/')` |
| `get_user` | Flask route | `@app.route('/users/<int:user_id>')` |
| `admin_panel` | Flask route | `@app.route('/admin')` |
| `before_request_handler` | Flask hook | `@app.before_request` |
| `not_found` | Flask error handler | `@app.errorhandler(404)` |
| `create_app` | Flask factory | Marked as entrypoint |
| `send_email` | Celery task | `@celery.task` |
| `process_data` | Celery task | `@shared_task` |
| `retry_task` | Celery task | `@celery.task(bind=True)` |

## Truly Dead Code Confirmed (DELETE)

### Orphaned Files (100% confidence)
All 16 symbols in the `utils/` directory were confirmed as deletable since the entire directory is never imported:

- `utils/deprecated.py`: `old_function_1`, `old_function_2`, `OldClass`, `old_method`
- `utils/__init__.py`: `format_date`, `parse_config`, `__all__`
- `utils/helpers.py`: `datetime`, `format_date`, `parse_config`, `legacy_format_date`, `old_parser`, `DeprecatedHelper`, `__init__`, `get_cached`, `set_cached`

### Unused Code in Main Files
- **Functions**: `unused_helper_function`, `another_unused_function`, `unused_task_helper`
- **Classes**: `UnusedClass`, `TaskUtils`
- **Methods**: `unused_method`, `format_result`
- **Constants**: `API_VERSION`, `DEPRECATED_CONSTANT`
- **Imports**: `datetime` (in app.py - imported but never used)

## Key Insights

1. **Static analysis accuracy**: High confidence items (>=80%) were 80% correct (8/10 truly dead)
2. **Entrypoint detection worked**: All 0% confidence items correctly identified as KEEP
3. **LLM caught 4 false positives**: Framework instances (`app`, `celery`) and their imports that static analysis couldn't trace through decorator usage
4. **Orphaned file detection perfect**: All 100% confidence items in orphaned files confirmed as DELETE

## Sample Verified Items

### DELETE Example (Truly Dead Code)
```json
{
  "qualified_name": "app.unused_helper_function",
  "name": "unused_helper_function",
  "type": "unused_function",
  "original_confidence": 90,
  "verdict": "delete",
  "llm_reasoning": "Function is explicitly documented as never called and has no references"
}
```

### KEEP Example (Entrypoint)
```json
{
  "qualified_name": "app.index",
  "name": "index",
  "type": "unused_function",
  "original_confidence": 0,
  "reasons": [
    "Base confidence for FUNCTION: 60",
    "Marked as entrypoint: -40",
    "Has entrypoint decorator 'app.route('/')': -20"
  ],
  "verdict": "keep",
  "llm_reasoning": "Flask route entrypoint (@app.route('/')) - called by HTTP requests"
}
```

### KEEP Example (False Positive Caught)
```json
{
  "qualified_name": "app.app",
  "name": "app",
  "type": "unused_variable",
  "original_confidence": 90,
  "reasons": [
    "Base confidence for VARIABLE: 60",
    "Not reachable from any entrypoint"
  ],
  "verdict": "keep",
  "llm_reasoning": "Flask application instance used for route decorators (@app.route, @app.before_request, @app.errorhandler) and app.run() at bottom of file"
}
```

## Running the Verification

```bash
# First, run the analysis
openprune analyze tests/fixtures/flask_app --verbose

# Then run LLM verification (oneshot mode)
openprune verify tests/fixtures/flask_app --batch --min-confidence 0

# Results are saved to:
# - .openprune/results.json (static analysis)
# - .openprune/verified.json (LLM verification)
```
