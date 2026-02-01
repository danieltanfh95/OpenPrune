#!/bin/bash
# Start the Celery worker

python -m celery -A tasks.celery worker --loglevel=info
