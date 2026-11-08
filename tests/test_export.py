"""Handover export tests (spec sections 5.6, 12 and 14)."""

from __future__ import annotations

import sqlite3

import pytest

from database.repositories import (
    EvidenceRepository,
    HandoverItemRepository,
    ReconstructionRepository,
)
from trace_app.models import EvidenceStatus, ReviewStatus
from trace_app.services.document_service import DocumentService
from trace_app.services.report_service import (
    REDACTION,
    ReportOptions,
    build_markdown,
    build_pdf,
    collect_report_data,
    redact,
    report_filename,
)
from trace_app.services.review_service import ReviewService

BRIEF = (
    "The initial release focuses on event discovery.\n\n"
    "Payment integration was postponed until after the first release."
)


@pytest.fixture()
def exportable(connection: sqlite3.Connection, isolated_storage, project: dict):
    """One approved, one rejected, one unreviewed item."""
    del isolated_storage
    source = DocumentService(connection).upload(
        project["id"], "brief.txt", BRIEF.encode()
    ).source
    reconstruction_id = ReconstructionRepository(connection).create(
        project_id=project["id"], status="draft_ready",
        model_provider="gemini", model_name="gemini-2.5-flash", source_count=1,
    )
    items = HandoverItemRepository(connection)
    evidence = EvidenceRepository(connection)

    approved = items.create(
        reconstruction_id, "decision", "Payment integration was postponed.",
        EvidenceStatus.SUPPORTED, ReviewStatus.PROPOSED, sort_order=0,
    )
    evidence.create(
        approved, source["id"],
        "Payment integration was postponed until after the first release.",
        location_label="paragraph 2", verified=True,
    )
    rejected = items.create(
        reconstruction_id, "risk", "A hallucinated risk about staffing.",
        EvidenceStatus.UNSUPPORTED, ReviewStatus.PROPOSED, sort_order=1,
    )
    unreviewed = items.create(
        reconstruction_id, "blocker", "An unreviewed blocker.",
        EvidenceStatus.PARTIALLY_SUPPORTED, ReviewStatus.PROPOSED, sort_order=2,
    )
    evidence.create(
        unreviewed, source["id"], "a quotation that was never in the source",
        location_label="paragraph 1", verified=False,
        support_note="This quotation could not be found in the source.",
    )
    connection.commit()

    reviews = ReviewService(connection)
    reviews.approve(approved, reconstruction_id)
    reviews.reject(rejected, reconstruction_id, note="No source supports this.")

    reconstruction = ReconstructionRepository(connection).get(
        reconstruction_id, project["id"]
    )
    return {
        "project": project,
        "reconstruction": reconstruction,
        "items": reviews.list_items(reconstruction_id),
        "ids": {"approved": approved, "rejected": rejected, "unreviewed": unreviewed},
        "missing": ["No launch date is documented in any source."],
    }


def _build(exportable, options: ReportOptions):
    data = collect_report_data(
        project=exportable["project"],
        reconstruction=exportable["reconstruction"],
        items=exportable["items"],
        missing_information=exportable["missing"],
        options=options,
    )
    return data, build_markdown(data, options)


# --------------------------------------------------------------------------
# What is included
# --------------------------------------------------------------------------

def test_default_export_contains_approved_items_only(exportable) -> None:
    data, markdown = _build(exportable, ReportOptions())
    assert len(data.approved) == 1
    assert data.unreviewed == []
    assert data.rejected_count == 1
    assert "Payment integration was postponed." in markdown
    assert "hallucinated risk" not in markdown
    assert "An unreviewed blocker." not in markdown


def test_a_rejected_item_is_excluded_even_with_unreviewed_enabled(
    exportable,
) -> None:
    """There is no option that lets a rejected item into a report."""
    _, markdown = _build(exportable, ReportOptions(include_unreviewed=True))
    assert "hallucinated risk" not in markdown
    assert "An unreviewed blocker." in markdown


def test_unreviewed_items_are_segregated_and_labelled(exportable) -> None:
    data, markdown = _build(exportable, ReportOptions(include_unreviewed=True))
    assert len(data.unreviewed) == 1
    assert "Unreviewed items — NOT APPROVED" in markdown
    assert "**[UNREVIEWED]** An unreviewed blocker." in markdown
    # The approved item must not acquire the unreviewed marker.
    approved_section = markdown.split("Unreviewed items")[0]
    assert "[UNREVIEWED]" not in approved_section


def test_evidence_references_are_included_with_their_verified_state(
    exportable,
) -> None:
    _, markdown = _build(exportable, ReportOptions(include_unreviewed=True))
    assert "brief.txt — paragraph 2 (verified)" in markdown
    assert "(NOT VERIFIED)" in markdown
    assert "Payment integration was postponed until after the first release." in markdown


def test_evidence_can_be_omitted(exportable) -> None:
    _, markdown = _build(exportable, ReportOptions(include_evidence=False))
    assert "paragraph 2 (verified)" not in markdown
    assert "Payment integration was postponed." in markdown  # the claim remains


def test_reviewer_notes_travel_with_approved_items(exportable) -> None:
    reviews_items = exportable["items"]
    for item in reviews_items:
        if item["id"] == exportable["ids"]["approved"]:
            item["reviewer_note"] = "Confirmed against the meeting notes."
    _, markdown = _build(exportable, ReportOptions())
    assert "Reviewer note: Confirmed against the meeting notes." in markdown


# --------------------------------------------------------------------------
# Honesty
# --------------------------------------------------------------------------

def test_an_export_with_nothing_approved_says_so(exportable) -> None:
    for item in exportable["items"]:
        item["review_status"] = str(ReviewStatus.PROPOSED)
    _, markdown = _build(exportable, ReportOptions())
    assert "No content was approved for this handover" in markdown
    assert "not an error" in markdown


def test_the_review_scope_is_stated(exportable) -> None:
    _, markdown = _build(exportable, ReportOptions())
    assert "**Approved items included:** 1" in markdown
    assert "**Rejected items:** 1 — excluded from this report" in markdown


def test_limitations_report_unverified_citations(exportable) -> None:
    _, markdown = _build(exportable, ReportOptions(include_unreviewed=True))
    assert "Limitations and items requiring confirmation" in markdown
    assert "No launch date is documented in any source." in markdown
    assert "do not guarantee that an interpretation" in markdown


def test_a_preview_report_says_no_model_was_called(exportable) -> None:
    exportable["reconstruction"] = dict(exportable["reconstruction"])
    exportable["reconstruction"]["is_demo_preview"] = 1
    _, markdown = _build(exportable, ReportOptions())
    assert "No AI model was called." in markdown


def test_a_model_report_names_the_model(exportable) -> None:
    _, markdown = _build(exportable, ReportOptions())
    assert "gemini / gemini-2.5-flash" in markdown
    assert "No AI model was called" not in markdown


# --------------------------------------------------------------------------
# Secret redaction
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuvwxyz12",
        "AIzaSyB1234567890abcdefghijklmnopqrstu",
        "ghp_abcdefghijklmnopqrstuvwxyz12",
        "xoxb-1234567890-abcdefghij",
        "API_KEY=supersecret",
        "password: hunter2",
    ],
)
def test_credential_shaped_text_is_redacted(secret) -> None:
    assert secret not in redact(f"The config says {secret} here.")
    assert REDACTION in redact(f"The config says {secret} here.")


def test_ordinary_text_is_left_alone() -> None:
    text = "The release depends on approval of the final event data format."
    assert redact(text) == text


def test_a_secret_pasted_into_an_item_never_reaches_the_report(
    exportable,
) -> None:
    for item in exportable["items"]:
        if item["id"] == exportable["ids"]["approved"]:
            item["text"] = "Use the key AIzaSyB1234567890abcdefghijklmnopqrstu to deploy."
    _, markdown = _build(exportable, ReportOptions())
    assert "AIzaSyB1234567890abcdefghijklmnopqrstu" not in markdown
    assert REDACTION in markdown


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def test_the_pdf_is_a_real_pdf(exportable) -> None:
    options = ReportOptions()
    data, _ = _build(exportable, options)
    pdf = build_pdf(data, options)
    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 1000


def test_the_pdf_handles_unreviewed_content_and_empty_reports(exportable) -> None:
    for options in (
        ReportOptions(include_unreviewed=True),
        ReportOptions(include_evidence=False),
    ):
        data, _ = _build(exportable, options)
        assert build_pdf(data, options).startswith(b"%PDF-")

    for item in exportable["items"]:
        item["review_status"] = str(ReviewStatus.PROPOSED)
    data, _ = _build(exportable, ReportOptions())
    assert build_pdf(data, ReportOptions()).startswith(b"%PDF-")


def test_pdf_generation_survives_markup_characters(exportable) -> None:
    """Angle brackets must not break ReportLab's mini-markup."""
    for item in exportable["items"]:
        item["text"] = "Use <b>bold</b> & <tags> in the brief."
        item["review_status"] = str(ReviewStatus.APPROVED)
    options = ReportOptions()
    data, markdown = _build(exportable, options)
    assert build_pdf(data, options).startswith(b"%PDF-")
    assert "<b>bold</b>" in markdown


# --------------------------------------------------------------------------
# Filenames
# --------------------------------------------------------------------------

def test_filenames_are_safe_and_descriptive() -> None:
    assert report_filename("CampusConnect", "md").startswith(
        "trace-handover-campusconnect-"
    )
    assert report_filename("../../etc/passwd", "pdf").endswith(".pdf")
    assert "/" not in report_filename("../../etc/passwd", "pdf")
    assert report_filename("", "md").startswith("trace-handover-handover-")
