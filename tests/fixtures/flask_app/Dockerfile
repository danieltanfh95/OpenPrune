FROM python:3.11-slim

WORKDIR /app

COPY . .

ENV FLASK_APP=app.py

ENTRYPOINT ["gunicorn", "-c", "gunicorn_config.py", "app:create_app()"]
