"""Shared pytest fixtures.

Every test runs against its own temporary SQLite file so tests never touch the
developer's real ``data/trace.db``.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Make the repository root importable when pytest is run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.connection import get_connection  # noqa: E402
from database.schema import initialise_database  # noqa: E402
from trace_app.services.project_service import ProjectService  # noqa: E402


@pytest.fixture()
def connection(tmp_path: Path) -> sqlite3.Connection:
    """A fresh, schema-initialised database on a temporary path."""
    conn = get_connection(tmp_path / "test_trace.db")
    initialise_database(conn)
    yield conn
    conn.close()


@pytest.fixture()
def project_service(connection: sqlite3.Connection) -> ProjectService:
    return ProjectService(connection)


@pytest.fixture()
def project(project_service: ProjectService) -> dict:
    return project_service.create_project(
        "Test Project", "A project used by the test suite", "SAMURAI"
    )
