"""Helper utilities."""

from datetime import datetime


def format_date(dt: datetime) -> str:
    """Format a datetime object."""
    return dt.strftime("%Y-%m-%d")


def parse_config(config_str: str) -> dict:
    """Parse a configuration string."""
    return {"parsed": config_str}


def legacy_format_date(dt):
    """Legacy date formatter - no longer used anywhere."""
    return dt.strftime("%d/%m/%Y")


def old_parser(data):
    """Old parser function - deprecated and unused."""
    return data.split(",")


class DeprecatedHelper:
    """Old helper class that is no longer used."""

    def __init__(self):
        self.cache = {}

    def get_cached(self, key):
        """Get a cached value."""
        return self.cache.get(key)

    def set_cached(self, key, value):
        """Set a cached value."""
        self.cache[key] = value
