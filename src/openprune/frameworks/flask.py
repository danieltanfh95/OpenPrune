"""Flask-specific framework handler."""

from openprune.frameworks.base import DecoratorPattern, FrameworkHandler


class FlaskHandler(FrameworkHandler):
    """Handler for Flask applications."""

    @property
    def name(self) -> str:
        return "Flask"

    @property
    def decorator_patterns(self) -> list[DecoratorPattern]:
        return [
            DecoratorPattern(
                pattern="route",
                entrypoint_type="flask_route",
                description="Flask route handler",
            ),
            DecoratorPattern(
                pattern=".get(",
                entrypoint_type="flask_route",
                description="Flask GET handler",
            ),
            DecoratorPattern(
                pattern=".post(",
                entrypoint_type="flask_route",
                description="Flask POST handler",
            ),
            DecoratorPattern(
                pattern=".put(",
                entrypoint_type="flask_route",
                description="Flask PUT handler",
            ),
            DecoratorPattern(
                pattern=".delete(",
                entrypoint_type="flask_route",
                description="Flask DELETE handler",
            ),
            DecoratorPattern(
                pattern=".patch(",
                entrypoint_type="flask_route",
                description="Flask PATCH handler",
            ),
            DecoratorPattern(
                pattern="before_request",
                entrypoint_type="flask_hook",
                description="Flask before request hook",
            ),
            DecoratorPattern(
                pattern="after_request",
                entrypoint_type="flask_hook",
                description="Flask after request hook",
            ),
            DecoratorPattern(
                pattern="teardown_request",
                entrypoint_type="flask_hook",
                description="Flask teardown request hook",
            ),
            DecoratorPattern(
                pattern="before_first_request",
                entrypoint_type="flask_hook",
                description="Flask before first request hook",
            ),
            DecoratorPattern(
                pattern="errorhandler",
                entrypoint_type="flask_errorhandler",
                description="Flask error handler",
            ),
            DecoratorPattern(
                pattern="cli.command",
                entrypoint_type="flask_cli",
                description="Flask CLI command",
            ),
        ]

    @property
    def factory_functions(self) -> list[str]:
        return ["create_app", "make_app", "app_factory"]
