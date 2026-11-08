"""Synthetic demo loading tests (spec sections 11 and 15)."""

from __future__ import annotations

import sqlite3

import pytest

from trace_app.services.demo_service import (
    DEMO_FILES,
    DEMO_PROJECT_NAME,
    DemoService,
)
from trace_app.services.document_service import DocumentService
from trace_app.services.project_service import ProjectService


@pytest.fixture()
def demo(connection: sqlite3.Connection, isolated_storage) -> DemoService:
    del isolated_storage
    return DemoService(connection)


def test_the_demo_source_files_are_present_in_the_repository(demo) -> None:
    assert demo.available()


def test_loading_creates_the_project_and_extracts_every_source(
    connection, demo
) -> None:
    result = demo.load()
    assert result.ok
    assert result.created
    assert result.files_added == len(DEMO_FILES)

    project = result.project
    assert project["name"] == DEMO_PROJECT_NAME
    assert project["is_demo"] == 1

    sources = DocumentService(connection).list_sources(project["id"])
    assert [s["display_name"] for s in sources] == list(DEMO_FILES)
    assert all(s["extraction_status"] == "extracted" for s in sources)
    assert all(s["char_count"] > 500 for s in sources)


def test_loading_twice_does_not_duplicate_anything(connection, demo) -> None:
    first = demo.load()
    second = demo.load()

    assert second.ok
    assert not second.created
    assert second.files_added == 0
    assert second.project["id"] == first.project["id"]
    assert len(ProjectService(connection).list_projects()) == 1
    assert len(DocumentService(connection).list_sources(first.project["id"])) == len(
        DEMO_FILES
    )


def test_loading_the_demo_leaves_user_projects_alone(connection, demo) -> None:
    projects = ProjectService(connection)
    mine = projects.create_project("My Real Project", "Please do not touch.")
    demo.load()

    still_there = projects.get_project(mine["id"])
    assert still_there is not None
    assert still_there["description"] == "Please do not touch."
    assert len(projects.list_projects()) == 2


def test_the_demo_is_flagged_as_synthetic(connection, demo) -> None:
    """A judge must be able to tell demo data from real data."""
    result = demo.load()
    assert result.project["is_demo"] == 1
    assert "fictional" in result.project["description"].lower()
    assert "no real institution" in result.project["description"].lower()


def test_the_demo_sources_evidence_the_documented_facts(connection, demo) -> None:
    """The facts the specification asks the demo to exercise are present."""
    result = demo.load()
    sources = DocumentService(connection).usable_sources(result.project["id"])
    combined = " ".join(str(s["extracted_text"]) for s in sources).lower()

    for fact in [
        "event discovery and student registration",
        "payment integration was postponed",
        "event listing interface is complete",
        "registration validation remains unfinished",
        "blocked by the absence of a configured test email service",
        "depends on approval of the final event data format",
        "no confirmed launch date is documented",
        "owner for the final accessibility review is not documented",
    ]:
        assert fact in combined, f"missing demo fact: {fact}"


def test_the_demo_contains_a_deliberate_contradiction(connection, demo) -> None:
    """Two sources disagree on event capacity, for the conflict path."""
    result = demo.load()
    sources = {
        s["display_name"]: str(s["extracted_text"]).lower()
        for s in DocumentService(connection).usable_sources(result.project["id"])
    }
    assert "maximum capacity for a single event is 500 attendees" in sources[
        "meeting_notes.txt"
    ]
    assert "capacity limit is implemented as 300 attendees" in sources[
        "development_status.txt"
    ]


def test_a_missing_demo_directory_is_reported_not_crashed(demo, monkeypatch) -> None:
    import trace_app.services.demo_service as module
    from pathlib import Path

    monkeypatch.setattr(module, "DEMO_DIR", Path("/nonexistent/demo/dir"))
    assert not demo.available()
    result = demo.load()
    assert not result.ok
    assert "not present in this deployment" in result.message
