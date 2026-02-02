"""SQLAlchemy models for testing ORM usage detection."""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    """User model - USED via queries in app.py."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)

    # Relationship to posts - used
    posts = db.relationship("Post", backref="author", lazy="dynamic")

    def __repr__(self):
        return f"<User {self.username}>"


class Post(db.Model):
    """Post model - USED via relationship from User."""

    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    def __repr__(self):
        return f"<Post {self.title}>"


class Comment(db.Model):
    """Comment model - USED via ForeignKey reference to posts."""

    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False)


class UnusedModel(db.Model):
    """This model is NEVER used anywhere - should be flagged as dead code."""

    __tablename__ = "unused_table"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    value = db.Column(db.Integer)

    def unused_method(self):
        """Method on unused model."""
        return self.value * 2


class AnotherUnusedModel(db.Model):
    """Another model that is never queried or referenced."""

    __tablename__ = "another_unused"

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.JSON)


class DeprecatedUserProfile(db.Model):
    """Old user profile model - no longer used after refactoring."""

    __tablename__ = "deprecated_profiles"

    id = db.Column(db.Integer, primary_key=True)
    bio = db.Column(db.Text)
    # Note: No ForeignKey to users - this is intentionally orphaned
