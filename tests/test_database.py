"""Database and project-service tests (spec section 14)."""

from __future__ import annotations

import sqlite3

import pytest

from database.schema import SCHEMA_VERSION, initialise_database
from trace_app.models import ExtractionStatus, new_id, utc_now
from trace_app.services.project_service import ProjectService, ValidationError


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

def test_schema_creates_every_expected_table(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    tables = {row["name"] for row in rows}
    assert {
        "projects",
        "sources",
        "reconstructions",
        "handover_items",
        "evidence",
        "review_events",
        "schema_version",
    } <= tables


def test_schema_initialisation_is_idempotent(connection: sqlite3.Connection) -> None:
    assert initialise_database(connection) == SCHEMA_VERSION
    # Running it again must not duplicate migrations or raise.
    assert initialise_database(connection) == SCHEMA_VERSION
    count = connection.execute("SELECT COUNT(*) AS c FROM schema_version").fetchone()
    assert count["c"] == SCHEMA_VERSION


def test_foreign_keys_are_enforced(connection: sqlite3.Connection) -> None:
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# --------------------------------------------------------------------------
# Project creation and retrieval
# --------------------------------------------------------------------------

def test_create_and_retrieve_project(project_service: ProjectService) -> None:
    created = project_service.create_project("CampusConnect", "A portal", "SAMURAI")
    fetched = project_service.get_project(created["id"])
    assert fetched is not None
    assert fetched["name"] == "CampusConnect"
    assert fetched["description"] == "A portal"
    assert fetched["team_label"] == "SAMURAI"
    assert fetched["created_at"] and fetched["updated_at"]


def test_project_persists_across_new_connections(tmp_path) -> None:
    """A project must still be there after the connection is reopened."""
    from database.connection import get_connection

    db_path = tmp_path / "persist.db"
    first = get_connection(db_path)
    initialise_database(first)
    created = ProjectService(first).create_project("Persisted Project")
    first.close()

    second = get_connection(db_path)
    initialise_database(second)
    reloaded = ProjectService(second).get_project(created["id"])
    second.close()

    assert reloaded is not None
    assert reloaded["name"] == "Persisted Project"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", None])
def test_blank_project_names_are_rejected(
    project_service: ProjectService, blank
) -> None:
    with pytest.raises(ValidationError):
        project_service.create_project(blank)


def test_project_fields_are_trimmed(project_service: ProjectService) -> None:
    created = project_service.create_project(
        "  Spaced Name  ", "  spaced description  ", "  Team  "
    )
    assert created["name"] == "Spaced Name"
    assert created["description"] == "spaced description"
    assert created["team_label"] == "Team"


def test_blank_optional_fields_become_null(project_service: ProjectService) -> None:
    created = project_service.create_project("Only A Name", "   ", "")
    assert created["description"] is None
    assert created["team_label"] is None


def test_get_unknown_project_returns_none(project_service: ProjectService) -> None:
    assert project_service.get_project("proj_doesnotexist") is None
    assert project_service.get_project("") is None


def test_require_project_raises_for_unknown_id(
    project_service: ProjectService,
) -> None:
    with pytest.raises(ValidationError):
        project_service.require_project("proj_missing")


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------

def test_list_projects_is_newest_first(project_service: ProjectService) -> None:
    first = project_service.create_project("First")
    second = project_service.create_project("Second")
    listed = project_service.list_projects()
    assert [p["id"] for p in listed][:2] == [second["id"], first["id"]]


def test_list_projects_reports_real_source_counts(
    connection: sqlite3.Connection, project_service: ProjectService, project: dict
) -> None:
    """Dashboard counts come from rows, never from an invented metric."""
    listed = {p["id"]: p for p in project_service.list_projects()}
    assert listed[project["id"]]["source_count"] == 0
    assert listed[project["id"]]["extracted_count"] == 0
    assert listed[project["id"]]["latest_reconstruction_status"] == "not_started"

    _insert_source(connection, project["id"], ExtractionStatus.EXTRACTED)
    _insert_source(connection, project["id"], ExtractionStatus.FAILED)
    connection.commit()

    listed = {p["id"]: p for p in project_service.list_projects()}
    assert listed[project["id"]]["source_count"] == 2
    assert listed[project["id"]]["extracted_count"] == 1


def test_find_by_name_is_case_insensitive(project_service: ProjectService) -> None:
    created = project_service.create_project("CampusConnect")
    assert project_service.find_by_name("campusconnect")["id"] == created["id"]
    assert project_service.find_by_name("  CAMPUSCONNECT  ")["id"] == created["id"]
    assert project_service.find_by_name("Nope") is None


# --------------------------------------------------------------------------
# Source records and project/source relationships
# --------------------------------------------------------------------------

def _insert_source(
    connection: sqlite3.Connection,
    project_id: str,
    status: str = ExtractionStatus.EXTRACTED,
    text: str = "Some extracted text",
) -> str:
    source_id = new_id("src")
    connection.execute(
        "INSERT INTO sources (id, project_id, display_name, stored_name, file_type,"
        " file_size, uploaded_at, extraction_status, extracted_text, char_count)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_id,
            project_id,
            "notes.txt",
            f"{source_id}.txt",
            "txt",
            len(text),
            utc_now(),
            str(status),
            text,
            len(text),
        ),
    )
    return source_id


def test_source_record_round_trips(
    connection: sqlite3.Connection, project: dict
) -> None:
    source_id = _insert_source(connection, project["id"])
    connection.commit()
    row = connection.execute(
        "SELECT * FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    assert row["project_id"] == project["id"]
    assert row["display_name"] == "notes.txt"
    assert row["extraction_status"] == "extracted"
    assert row["char_count"] == len("Some extracted text")


def test_source_cannot_reference_a_missing_project(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_source(connection, "proj_does_not_exist")


def test_sources_are_isolated_per_project(
    connection: sqlite3.Connection, project_service: ProjectService
) -> None:
    """One project's sources must never appear under another project."""
    alpha = project_service.create_project("Alpha")
    beta = project_service.create_project("Beta")
    alpha_source = _insert_source(connection, alpha["id"], text="alpha only")
    _insert_source(connection, beta["id"], text="beta only")
    connection.commit()

    alpha_rows = connection.execute(
        "SELECT id FROM sources WHERE project_id = ?", (alpha["id"],)
    ).fetchall()
    assert [r["id"] for r in alpha_rows] == [alpha_source]


def test_evidence_cannot_reference_a_missing_source(
    connection: sqlite3.Connection, project: dict
) -> None:
    """Evidence rows are constrained to real sources (spec section 6.3)."""
    reconstruction_id = new_id("rec")
    connection.execute(
        "INSERT INTO reconstructions (id, project_id, created_at, status)"
        " VALUES (?, ?, ?, ?)",
        (reconstruction_id, project["id"], utc_now(), "draft_ready"),
    )
    item_id = new_id("item")
    connection.execute(
        "INSERT INTO handover_items (id, reconstruction_id, category, text,"
        " review_status, evidence_status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item_id,
            reconstruction_id,
            "decision",
            "A decision was taken.",
            "proposed",
            "supported",
            utc_now(),
            utc_now(),
        ),
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO evidence (id, handover_item_id, source_id, location_label,"
            " excerpt) VALUES (?, ?, ?, ?, ?)",
            (new_id("ev"), item_id, "src_fabricated", "page 2", "fake excerpt"),
        )


def test_a_source_in_use_by_evidence_cannot_be_deleted(
    connection: sqlite3.Connection, project: dict
) -> None:
    """ON DELETE RESTRICT keeps evidence trails intact (spec section 8)."""
    source_id = _insert_source(connection, project["id"])
    reconstruction_id = new_id("rec")
    connection.execute(
        "INSERT INTO reconstructions (id, project_id, created_at, status)"
        " VALUES (?, ?, ?, ?)",
        (reconstruction_id, project["id"], utc_now(), "draft_ready"),
    )
    item_id = new_id("item")
    connection.execute(
        "INSERT INTO handover_items (id, reconstruction_id, category, text,"
        " review_status, evidence_status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, reconstruction_id, "risk", "A risk.", "proposed", "supported",
         utc_now(), utc_now()),
    )
    connection.execute(
        "INSERT INTO evidence (id, handover_item_id, source_id, location_label,"
        " excerpt) VALUES (?, ?, ?, ?, ?)",
        (new_id("ev"), item_id, source_id, "paragraph 1", "Some extracted"),
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))
