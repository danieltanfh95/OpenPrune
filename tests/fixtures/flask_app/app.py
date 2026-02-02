"""Sample Flask application for testing."""

from flask import Flask, jsonify

from .models import User, Post, Comment, db

app = Flask(__name__)


def create_app(config_name="default"):
    """Application factory."""
    application = Flask(__name__)
    application.config["DEBUG"] = True
    return application


@app.route("/")
def index():
    """Home page."""
    return jsonify({"message": "Hello, World!"})


@app.route("/users/<int:user_id>", methods=["GET", "POST"])
def get_user(user_id):
    """Get a user by ID - uses User.query (Flask-SQLAlchemy pattern)."""
    user = User.query.get_or_404(user_id)
    return jsonify({"user_id": user.id, "username": user.username})


@app.route("/users/<int:user_id>/posts")
def get_user_posts(user_id):
    """Get posts for a user - uses session.query pattern."""
    posts = db.session.query(Post).filter_by(user_id=user_id).all()
    return jsonify([{"id": p.id, "title": p.title} for p in posts])


@app.route("/posts/<int:post_id>/comments")
def get_post_comments(post_id):
    """Get comments for a post - uses Comment.query pattern."""
    comments = Comment.query.filter_by(post_id=post_id).all()
    return jsonify([{"id": c.id, "body": c.body} for c in comments])


@app.route("/admin")
def admin_panel():
    """Admin panel - this is used."""
    return jsonify({"admin": True})


@app.before_request
def before_request_handler():
    """Run before each request."""
    pass


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"error": "Not found"}), 404


def unused_helper_function():
    """This function is never called - should be detected as dead code."""
    return "I am never used"


def another_unused_function(x, y):
    """Another unused function."""
    return x + y


class UnusedClass:
    """This class is never instantiated."""

    def __init__(self):
        self.value = 42

    def unused_method(self):
        """This method is never called."""
        return self.value


# This import is never used
import json  # noqa: F401
from datetime import datetime  # Unused import


# Used constant
API_VERSION = "1.0.0"

# Unused constant
DEPRECATED_CONSTANT = "old_value"


if __name__ == "__main__":
    app.run()
