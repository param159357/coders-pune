"""SQLite connection handling.

Every connection enables foreign-key enforcement, which SQLite leaves off by
default. All callers use parameterised queries; no SQL is ever built by string
interpolation of user input.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import config


class DatabaseError(RuntimeError):
    """Raised when a database operation fails in a way the UI should report."""


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a configured SQLite connection.

    Rows come back as ``sqlite3.Row`` so callers can use column names, and
    foreign keys are enforced for real.
    """
    path = Path(db_path) if db_path is not None else config.DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(
            str(path),
            timeout=15.0,
            check_same_thread=False,
        )
    except sqlite3.Error as exc:  # pragma: no cover - environment dependent
        raise DatabaseError(f"Could not open the database at {path}: {exc}") from exc

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception."""
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
