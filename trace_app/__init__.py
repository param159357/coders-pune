"""TRACE application package.

Named ``trace_app`` rather than ``trace`` so it does not shadow Python's
standard-library ``trace`` module, which a top-level ``trace/`` package would
silently take precedence over.
"""

__all__ = ["models", "services", "ai", "utils"]
