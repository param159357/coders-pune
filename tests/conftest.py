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
from trace_app.security.authz import Principal  # noqa: E402
from trace_app.services.auth_service import AuthService  # noqa: E402
from trace_app.services.project_service import ProjectService  # noqa: E402

#: Default actor for tests that are not themselves about access control.
#: Named rather than implicit, so a test that needs a weaker role has to say
#: so and a missing principal can never silently mean "allow everything".
OWNER = Principal(user_id="usr_test_owner", email="owner@test.local", role="owner")


def principal(role: str, project_id: str | None = None) -> Principal:
    """Build a principal for a given role, for access-control tests."""
    return Principal(
        user_id=f"usr_test_{role}",
        email=f"{role}@test.local",
        role=role,
        project_id=project_id,
    )


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


# --------------------------------------------------------------------------
# Document fixtures
#
# Fixtures are generated rather than committed as binaries, so the suite stays
# reproducible and the repository stays free of opaque test blobs.
# --------------------------------------------------------------------------

@pytest.fixture()
def make_pdf(tmp_path: Path):
    """Build a text PDF. ``pages`` is a list of page body strings."""

    def _make(name: str, pages: list[str], encrypt: bool = False) -> Path:
        import pymupdf

        document = pymupdf.open()
        for body in pages:
            page = document.new_page()
            if body:
                page.insert_text((72, 100), body, fontsize=11)
        path = tmp_path / name
        if encrypt:
            document.save(
                str(path),
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="owner",
                user_pw="user",
            )
        else:
            document.save(str(path))
        document.close()
        return path

    return _make


@pytest.fixture()
def make_docx(tmp_path: Path):
    def _make(
        name: str, paragraphs: list[str], table: list[list[str]] | None = None
    ) -> Path:
        from docx import Document

        document = Document()
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
        if table:
            built = document.add_table(rows=len(table), cols=len(table[0]))
            for row_index, row in enumerate(table):
                for column_index, value in enumerate(row):
                    built.cell(row_index, column_index).text = value
        path = tmp_path / name
        document.save(str(path))
        return path

    return _make


@pytest.fixture()
def isolated_storage(tmp_path: Path, monkeypatch):
    """Point config's upload directory at a temporary folder."""
    import config

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_dir)
    return upload_dir


@pytest.fixture()
def owner() -> Principal:
    return OWNER


@pytest.fixture()
def auth(connection) -> AuthService:
    return AuthService(connection)


@pytest.fixture()
def account(auth: AuthService) -> dict:
    """A registered account used as the acting user."""
    return auth.register("tester@test.local", "test-password", "Tester")
