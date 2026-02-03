"""Web dashboard for Smollama nodes.

Provides a local web interface for monitoring readings, observations,
and memories using FastAPI and HTMX.
"""

from .app import create_app

__all__ = ["create_app"]
