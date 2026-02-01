"""Celery-specific framework handler."""

from openprune.frameworks.base import DecoratorPattern, FrameworkHandler


class CeleryHandler(FrameworkHandler):
    """Handler for Celery applications."""

    @property
    def name(self) -> str:
        return "Celery"

    @property
    def decorator_patterns(self) -> list[DecoratorPattern]:
        return [
            DecoratorPattern(
                pattern=".task",
                entrypoint_type="celery_task",
                description="Celery task",
            ),
            DecoratorPattern(
                pattern="shared_task",
                entrypoint_type="celery_shared_task",
                description="Celery shared task",
            ),
            DecoratorPattern(
                pattern="task_success.connect",
                entrypoint_type="celery_signal",
                description="Celery task success signal",
            ),
            DecoratorPattern(
                pattern="task_failure.connect",
                entrypoint_type="celery_signal",
                description="Celery task failure signal",
            ),
            DecoratorPattern(
                pattern="task_prerun.connect",
                entrypoint_type="celery_signal",
                description="Celery task prerun signal",
            ),
            DecoratorPattern(
                pattern="task_postrun.connect",
                entrypoint_type="celery_signal",
                description="Celery task postrun signal",
            ),
            DecoratorPattern(
                pattern="worker_ready.connect",
                entrypoint_type="celery_signal",
                description="Celery worker ready signal",
            ),
            DecoratorPattern(
                pattern="celeryd_init.connect",
                entrypoint_type="celery_signal",
                description="Celery daemon init signal",
            ),
            DecoratorPattern(
                pattern="beat_init.connect",
                entrypoint_type="celery_signal",
                description="Celery beat init signal",
            ),
        ]

    @property
    def factory_functions(self) -> list[str]:
        return ["make_celery", "create_celery", "celery_factory"]
