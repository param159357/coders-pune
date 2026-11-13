"""Upload pipeline and project-isolation tests (spec sections 5.3, 12, 14)."""

from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import OWNER

from trace_app.models import ExtractionStatus, new_id, utc_now
from trace_app.services.document_service import DocumentService, extraction_summary
from trace_app.services.project_service import ProjectService


@pytest.fixture()
def documents(connection: sqlite3.Connection, isolated_storage) -> DocumentService:
    del isolated_storage  # applied via monkeypatch; required for ordering
    return DocumentService(connection, OWNER)


TXT = b"Initial release focuses on event discovery.\n\nPayment was postponed."


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_uploading_a_txt_stores_extracts_and_records_it(
    documents: DocumentService, project: dict, isolated_storage
) -> None:
    outcome = documents.upload(project["id"], "brief.txt", TXT)
    assert outcome.accepted
    source = outcome.source
    assert source["display_name"] == "brief.txt"
    assert source["extraction_status"] == ExtractionStatus.EXTRACTED
    assert source["char_count"] > 0
    assert "event discovery" in source["extracted_text"]
    # The file landed on disk under a generated name.
    assert (isolated_storage / source["stored_name"]).is_file()


def test_stored_filename_is_generated_not_taken_from_the_upload(
    documents: DocumentService, project: dict
) -> None:
    outcome = documents.upload(project["id"], "../../etc/passwd.txt", TXT)
    assert outcome.accepted
    source = outcome.source
    # Display name is sanitised, stored name is TRACE's own identifier.
    assert source["display_name"] == "passwd.txt"
    assert source["stored_name"] == f"{source['id']}.txt"
    assert ".." not in source["stored_name"]


def test_a_rejected_upload_writes_nothing_to_disk(
    documents: DocumentService, project: dict, isolated_storage
) -> None:
    outcome = documents.upload(project["id"], "payload.exe", b"MZ\x90\x00")
    assert not outcome.accepted
    assert outcome.source is None
    assert list(isolated_storage.iterdir()) == []


def test_identical_reupload_is_refused(
    documents: DocumentService, project: dict
) -> None:
    first = documents.upload(project["id"], "brief.txt", TXT)
    second = documents.upload(project["id"], "brief-copy.txt", TXT)
    assert first.accepted
    assert not second.accepted
    assert second.duplicate_of == first.source["id"]
    assert len(documents.list_sources(project["id"])) == 1


def test_the_same_file_may_exist_in_two_different_projects(
    documents: DocumentService, project_service: ProjectService
) -> None:
    alpha = project_service.create_project("Alpha")
    beta = project_service.create_project("Beta")
    assert documents.upload(alpha["id"], "brief.txt", TXT).accepted
    assert documents.upload(beta["id"], "brief.txt", TXT).accepted


# --------------------------------------------------------------------------
# Failures are recorded, not hidden
# --------------------------------------------------------------------------

def test_an_image_only_pdf_is_stored_but_marked_unusable(
    documents: DocumentService, project: dict, make_pdf
) -> None:
    path = make_pdf("scanned.pdf", ["", ""])
    outcome = documents.upload(project["id"], "scanned.pdf", path.read_bytes())
    assert outcome.accepted  # the upload is recorded
    assert outcome.source["extraction_status"] == ExtractionStatus.NO_TEXT_EXTRACTED
    assert outcome.source["extraction_error"]
    # ...but it is not offered to the reconstruction pipeline.
    assert documents.usable_sources(project["id"]) == []


def test_usable_sources_excludes_failed_and_empty_sources(
    documents: DocumentService, project: dict, make_pdf
) -> None:
    documents.upload(project["id"], "good.txt", TXT)
    documents.upload(project["id"], "scanned.pdf", make_pdf("s.pdf", [""]).read_bytes())
    documents.upload(project["id"], "corrupt.pdf", b"%PDF-1.4\nbroken")
    usable = documents.usable_sources(project["id"])
    assert [s["display_name"] for s in usable] == ["good.txt"]


def test_extraction_summary_counts_each_outcome(
    documents: DocumentService, project: dict, make_pdf
) -> None:
    documents.upload(project["id"], "good.txt", TXT)
    documents.upload(project["id"], "scanned.pdf", make_pdf("s.pdf", [""]).read_bytes())
    documents.upload(project["id"], "corrupt.pdf", b"%PDF-1.4\nbroken")
    summary = extraction_summary(documents.list_sources(project["id"]))
    assert summary == {"total": 3, "usable": 1, "failed": 1, "no_text": 1}


# --------------------------------------------------------------------------
# Project isolation
# --------------------------------------------------------------------------

def test_a_source_cannot_be_read_through_another_project(
    documents: DocumentService, project_service: ProjectService
) -> None:
    alpha = project_service.create_project("Alpha")
    beta = project_service.create_project("Beta")
    source = documents.upload(alpha["id"], "secret.txt", TXT).source

    assert documents.get_source(source["id"], alpha["id"]) is not None
    assert documents.get_source(source["id"], beta["id"]) is None
    assert documents.list_sources(beta["id"]) == []
    assert documents.usable_sources(beta["id"]) == []


def test_a_source_cannot_be_deleted_through_another_project(
    documents: DocumentService, project_service: ProjectService
) -> None:
    alpha = project_service.create_project("Alpha")
    beta = project_service.create_project("Beta")
    source = documents.upload(alpha["id"], "brief.txt", TXT).source

    ok, message = documents.delete_source(source["id"], beta["id"])
    assert not ok
    assert "could not be found" in message
    assert documents.get_source(source["id"], alpha["id"]) is not None


# --------------------------------------------------------------------------
# Deletion
# --------------------------------------------------------------------------

def test_deleting_a_source_removes_the_row_and_the_file(
    documents: DocumentService, project: dict, isolated_storage
) -> None:
    source = documents.upload(project["id"], "brief.txt", TXT).source
    stored = isolated_storage / source["stored_name"]
    assert stored.is_file()

    ok, _ = documents.delete_source(source["id"], project["id"])
    assert ok
    assert not stored.exists()
    assert documents.list_sources(project["id"]) == []


def test_a_source_cited_as_evidence_cannot_be_deleted(
    connection: sqlite3.Connection, documents: DocumentService, project: dict
) -> None:
    """Deleting a cited source would break an evidence trail."""
    source = documents.upload(project["id"], "brief.txt", TXT).source

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
        (item_id, reconstruction_id, "decision", "A decision.", "proposed",
         "supported", utc_now(), utc_now()),
    )
    connection.execute(
        "INSERT INTO evidence (id, handover_item_id, source_id, location_label,"
        " excerpt) VALUES (?, ?, ?, ?, ?)",
        (new_id("ev"), item_id, source["id"], "paragraph 1", "Initial release"),
    )
    connection.commit()

    ok, message = documents.delete_source(source["id"], project["id"])
    assert not ok
    assert "cited as evidence" in message
    assert documents.get_source(source["id"], project["id"]) is not None


# --------------------------------------------------------------------------
# Schema migration
# --------------------------------------------------------------------------

def test_a_version_1_database_upgrades_to_version_2(tmp_path) -> None:
    """The version counter applies outstanding migrations in order."""
    from database.connection import get_connection
    from database.schema import SCHEMA_VERSION, _MIGRATIONS, initialise_database

    path = tmp_path / "v1.db"
    connection = get_connection(path)
    # Simulate a database created before migration 2 existed.
    connection.executescript(_MIGRATIONS[0])
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (1, datetime('now'))"
    )
    connection.commit()

    columns = {r["name"] for r in connection.execute("PRAGMA table_info(sources)")}
    assert "segments_json" not in columns

    assert initialise_database(connection) == SCHEMA_VERSION
    columns = {r["name"] for r in connection.execute("PRAGMA table_info(sources)")}
    assert "segments_json" in columns
    assert "extraction_notes" in columns
    connection.close()


# --------------------------------------------------------------------------
# Not parsing the same bytes twice
# --------------------------------------------------------------------------

def test_an_identical_file_in_another_project_reuses_its_extraction(
    connection, isolated_storage, project_service, make_pdf
) -> None:
    """Parsing is the slow step; identical bytes give identical text."""
    del isolated_storage
    from trace_app.services.auth_service import AuthService

    auth = AuthService(connection)
    user = auth.register("reuse@test.local", "reuse-password")
    alpha = project_service.create_project("Alpha")
    beta = project_service.create_project("Beta")
    auth.register_owner(alpha["id"], user["id"])
    auth.register_owner(beta["id"], user["id"])

    data = make_pdf("spec.pdf", ["A specification that appears in two projects."]).read_bytes()

    first = DocumentService(connection, auth.principal_for(user, alpha["id"]))
    second = DocumentService(connection, auth.principal_for(user, beta["id"]))

    a = first.upload(alpha["id"], "spec.pdf", data)
    assert a.accepted and not a.reused_extraction

    b = second.upload(beta["id"], "spec.pdf", data)
    assert b.accepted and b.reused_extraction
    assert "not parsed again" in b.message
    # Same text, from the cache rather than the parser.
    assert b.source["extracted_text"] == a.source["extracted_text"]
    assert b.source["char_count"] == a.source["char_count"]


def test_extraction_is_not_reused_across_users(
    connection, isolated_storage, project_service, make_pdf
) -> None:
    """Reuse is scoped to projects the uploader already belongs to."""
    del isolated_storage
    from trace_app.services.auth_service import AuthService

    auth = AuthService(connection)
    owner = auth.register("owner-a@test.local", "a-password")
    stranger = auth.register("stranger@test.local", "b-password")
    alpha = project_service.create_project("Alpha")
    beta = project_service.create_project("Beta")
    auth.register_owner(alpha["id"], owner["id"])
    auth.register_owner(beta["id"], stranger["id"])

    data = make_pdf("spec.pdf", ["Shared bytes, separate people."]).read_bytes()
    DocumentService(connection, auth.principal_for(owner, alpha["id"])).upload(
        alpha["id"], "spec.pdf", data
    )
    outcome = DocumentService(
        connection, auth.principal_for(stranger, beta["id"])
    ).upload(beta["id"], "spec.pdf", data)

    assert outcome.accepted
    assert not outcome.reused_extraction  # parsed fresh for them
