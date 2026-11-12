"""Human review tests (spec sections 5.5 and 14)."""

from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import OWNER

from database.repositories import (
    EvidenceRepository,
    HandoverItemRepository,
    ReconstructionRepository,
)
from trace_app.models import EvidenceStatus, ReviewStatus
from trace_app.services.document_service import DocumentService
from trace_app.services.review_service import (
    ReviewError,
    ReviewService,
    needs_attention,
)

BRIEF = (
    "The initial release focuses on event discovery.\n\n"
    "Payment integration was postponed until after the first release."
)


@pytest.fixture()
def draft(connection: sqlite3.Connection, isolated_storage, project: dict):
    """A reconstruction with three items of differing evidence strength."""
    del isolated_storage
    source = DocumentService(connection, OWNER).upload(
        project["id"], "brief.txt", BRIEF.encode()
    ).source

    reconstruction_id = ReconstructionRepository(connection).create(
        project_id=project["id"],
        status="draft_ready",
        model_provider="fake",
        model_name="fake-model",
        source_count=1,
    )
    items = HandoverItemRepository(connection)
    evidence = EvidenceRepository(connection)

    supported = items.create(
        reconstruction_id, "decision", "Payment integration was postponed.",
        EvidenceStatus.SUPPORTED, ReviewStatus.PROPOSED, sort_order=0,
    )
    evidence.create(
        supported, source["id"],
        "Payment integration was postponed until after the first release.",
        location_label="paragraph 2", verified=True,
    )
    unsupported = items.create(
        reconstruction_id, "risk", "The project may miss its deadline.",
        EvidenceStatus.UNSUPPORTED, ReviewStatus.PROPOSED, sort_order=1,
    )
    overview = items.create(
        reconstruction_id, "project_overview", "A campus events portal.",
        EvidenceStatus.PARTIALLY_SUPPORTED, ReviewStatus.PROPOSED, sort_order=2,
    )
    connection.commit()

    return {
        "connection": connection,
        "project": project,
        "reconstruction_id": reconstruction_id,
        "supported": supported,
        "unsupported": unsupported,
        "overview": overview,
        "service": ReviewService(connection, OWNER),
    }


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def test_items_arrive_with_their_evidence_attached(draft) -> None:
    items = {i["id"]: i for i in draft["service"].list_items(draft["reconstruction_id"])}
    assert len(items[draft["supported"]]["evidence"]) == 1
    assert items[draft["supported"]]["evidence"][0]["source_display_name"] == "brief.txt"
    assert items[draft["unsupported"]]["evidence"] == []


def test_items_are_grouped_in_specification_order(draft) -> None:
    """The overview comes first, then decisions, then risks."""
    groups = draft["service"].grouped_items(draft["reconstruction_id"])
    assert [category for category, _, _ in groups] == [
        "project_overview",
        "decision",
        "risk",
    ]
    assert groups[0][1] == "Project overview"


def test_empty_categories_are_omitted(draft) -> None:
    categories = {c for c, _, _ in draft["service"].grouped_items(draft["reconstruction_id"])}
    assert "blocker" not in categories


# --------------------------------------------------------------------------
# Nothing is approved automatically
# --------------------------------------------------------------------------

def test_every_item_starts_as_proposed(draft) -> None:
    items = draft["service"].list_items(draft["reconstruction_id"])
    assert {i["review_status"] for i in items} == {str(ReviewStatus.PROPOSED)}
    assert draft["service"].approved_items(draft["reconstruction_id"]) == []


def test_there_is_no_bulk_approve_operation() -> None:
    """Approving everything at once would defeat the review step."""
    assert not any(
        "all" in name.lower() and "approve" in name.lower()
        for name in dir(ReviewService)
    )


# --------------------------------------------------------------------------
# Approve, reject, reopen
# --------------------------------------------------------------------------

def test_approving_an_item_persists_and_is_recorded(draft) -> None:
    service = draft["service"]
    service.approve(draft["supported"], draft["reconstruction_id"])

    item = service.list_items(draft["reconstruction_id"])
    approved = next(i for i in item if i["id"] == draft["supported"])
    assert approved["review_status"] == ReviewStatus.APPROVED

    history = service.history(draft["supported"])
    assert history[-1]["action"] == "approved"
    assert history[-1]["previous_value"] == "proposed"
    assert history[-1]["new_value"] == "approved"


def test_rejecting_with_a_note_stores_the_reason(draft) -> None:
    service = draft["service"]
    service.reject(
        draft["unsupported"], draft["reconstruction_id"], note="No source says this."
    )
    item = next(
        i
        for i in service.list_items(draft["reconstruction_id"])
        if i["id"] == draft["unsupported"]
    )
    assert item["review_status"] == ReviewStatus.REJECTED
    assert item["reviewer_note"] == "No source says this."
    assert service.history(draft["unsupported"])[-1]["note"] == "No source says this."


def test_reopening_returns_an_item_to_proposed(draft) -> None:
    service = draft["service"]
    service.approve(draft["supported"], draft["reconstruction_id"])
    service.reopen(draft["supported"], draft["reconstruction_id"], note="Checking again.")

    item = next(
        i
        for i in service.list_items(draft["reconstruction_id"])
        if i["id"] == draft["supported"]
    )
    assert item["review_status"] == ReviewStatus.PROPOSED
    actions = [event["action"] for event in service.history(draft["supported"])]
    assert actions == ["approved", "reopened"]


def test_an_invalid_status_is_refused(draft) -> None:
    with pytest.raises(ReviewError):
        draft["service"].set_status(
            draft["supported"], draft["reconstruction_id"], "rubber-stamped"
        )


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------

def test_editing_text_keeps_the_previous_value_on_record(draft) -> None:
    service = draft["service"]
    original = "Payment integration was postponed."
    service.edit_text(
        draft["supported"],
        draft["reconstruction_id"],
        "Payment integration was postponed to release two.",
    )
    item = next(
        i
        for i in service.list_items(draft["reconstruction_id"])
        if i["id"] == draft["supported"]
    )
    assert item["text"] == "Payment integration was postponed to release two."

    event = service.history(draft["supported"])[-1]
    assert event["action"] == "edited"
    assert event["previous_value"] == original


def test_an_item_cannot_be_edited_to_empty(draft) -> None:
    for blank in ["", "   ", None]:
        with pytest.raises(ReviewError):
            draft["service"].edit_text(
                draft["supported"], draft["reconstruction_id"], blank
            )


def test_an_unchanged_edit_records_no_event(draft) -> None:
    service = draft["service"]
    service.edit_text(
        draft["supported"], draft["reconstruction_id"], "Payment integration was postponed."
    )
    assert service.history(draft["supported"]) == []


def test_editing_does_not_change_the_evidence_status(draft) -> None:
    """A reviewer's wording change must not silently re-rate the evidence."""
    service = draft["service"]
    service.edit_text(
        draft["unsupported"], draft["reconstruction_id"], "Reworded entirely."
    )
    item = next(
        i
        for i in service.list_items(draft["reconstruction_id"])
        if i["id"] == draft["unsupported"]
    )
    assert item["evidence_status"] == EvidenceStatus.UNSUPPORTED


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

def test_a_note_can_be_set_without_changing_the_status(draft) -> None:
    service = draft["service"]
    service.set_note(draft["overview"], draft["reconstruction_id"], "Check with Ops.")
    item = next(
        i
        for i in service.list_items(draft["reconstruction_id"])
        if i["id"] == draft["overview"]
    )
    assert item["reviewer_note"] == "Check with Ops."
    assert item["review_status"] == ReviewStatus.PROPOSED
    assert service.history(draft["overview"])[-1]["action"] == "note_added"


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------

def test_progress_tracks_the_review_state(draft) -> None:
    service = draft["service"]
    progress = service.progress(draft["reconstruction_id"])
    assert (progress.total, progress.proposed, progress.reviewed) == (3, 3, 0)
    assert not progress.complete

    service.approve(draft["supported"], draft["reconstruction_id"])
    service.reject(draft["unsupported"], draft["reconstruction_id"])
    progress = service.progress(draft["reconstruction_id"])
    assert (progress.approved, progress.rejected, progress.proposed) == (1, 1, 1)
    assert not progress.complete
    assert progress.fraction == pytest.approx(2 / 3)

    service.approve(draft["overview"], draft["reconstruction_id"])
    assert service.progress(draft["reconstruction_id"]).complete


def test_approved_items_excludes_rejected_and_unreviewed(draft) -> None:
    service = draft["service"]
    service.approve(draft["supported"], draft["reconstruction_id"])
    service.reject(draft["unsupported"], draft["reconstruction_id"])

    approved = service.approved_items(draft["reconstruction_id"])
    assert [item["id"] for item in approved] == [draft["supported"]]


# --------------------------------------------------------------------------
# Scoping
# --------------------------------------------------------------------------

def test_an_item_cannot_be_reached_through_another_reconstruction(draft) -> None:
    other = ReconstructionRepository(draft["connection"]).create(
        project_id=draft["project"]["id"],
        status="draft_ready",
        model_provider="fake",
        model_name="fake-model",
        source_count=1,
    )
    with pytest.raises(ReviewError):
        draft["service"].approve(draft["supported"], other)
    with pytest.raises(ReviewError):
        draft["service"].edit_text(draft["supported"], other, "Hijacked.")


def test_an_unknown_item_id_is_refused(draft) -> None:
    with pytest.raises(ReviewError):
        draft["service"].approve("item_nonexistent", draft["reconstruction_id"])


# --------------------------------------------------------------------------
# Attention flag
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status, expected",
    [
        (EvidenceStatus.SUPPORTED, False),
        (EvidenceStatus.PARTIALLY_SUPPORTED, True),
        (EvidenceStatus.CONFLICTING, True),
        (EvidenceStatus.UNSUPPORTED, True),
        (EvidenceStatus.NEEDS_REVIEW, True),
    ],
)
def test_weak_evidence_is_flagged_for_attention(status, expected) -> None:
    assert needs_attention({"evidence_status": str(status)}) is expected
