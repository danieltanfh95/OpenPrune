"""Sample Celery tasks for testing."""

from celery import Celery, shared_task

celery = Celery("tasks")


@celery.task
def send_email(to, subject, body):
    """Send an email task."""
    print(f"Sending email to {to}: {subject}")
    return True


@shared_task
def process_data(data):
    """Process some data."""
    return {"processed": data}


@celery.task(bind=True, max_retries=3)
def retry_task(self, value):
    """A task that can retry."""
    try:
        return value * 2
    except Exception as exc:
        self.retry(exc=exc)


def unused_task_helper():
    """Helper function that is never used."""
    return "unused"


class TaskUtils:
    """Utility class for tasks - never instantiated."""

    @staticmethod
    def format_result(result):
        """Format a result - never called."""
        return str(result)
