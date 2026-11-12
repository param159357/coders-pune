"""Domain enums, labels, and identifiers shared across TRACE.

The string values here are the values written to SQLite and produced by the
model, so they are part of TRACE's contract. The human-readable labels live
beside them because the UI must show text, not only colour.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum


# --------------------------------------------------------------------------
# Identifiers and timestamps
# --------------------------------------------------------------------------

def new_id(prefix: str) -> str:
    """Return a short, stable, prefixed identifier such as ``src_9f2a1c4b``."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def utc_now() -> str:
    """A single consistent timestamp format for every table and report."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_timestamp(raw: str | None) -> str:
    """Render a stored timestamp for display, tolerating odd input."""
    if not raw:
        return "Unknown"
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return str(raw)
    return parsed.strftime("%d %b %Y, %H:%M UTC")


# --------------------------------------------------------------------------
# Status enums
# --------------------------------------------------------------------------

class ExtractionStatus(StrEnum):
    """Lifecycle of a single uploaded source document."""

    UPLOADED = "uploaded"
    EXTRACTED = "extracted"
    PARTIALLY_EXTRACTED = "partially_extracted"
    NO_TEXT_EXTRACTED = "no_text_extracted"
    FAILED = "failed"


class ReconstructionStatus(StrEnum):
    NOT_STARTED = "not_started"
    PROCESSING = "processing"
    DRAFT_READY = "draft_ready"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class EvidenceStatus(StrEnum):
    """How well the cited sources actually support a claim.

    This is deliberately *not* a model-confidence score; see spec section 6.3.
    """

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"
    NEEDS_REVIEW = "needs_review"


class ItemCategory(StrEnum):
    PROJECT_OVERVIEW = "project_overview"
    DECISION = "decision"
    COMPLETED_WORK = "completed_work"
    PENDING_WORK = "pending_work"
    BLOCKER = "blocker"
    DEPENDENCY = "dependency"
    RISK = "risk"
    OPEN_QUESTION = "open_question"
    RESPONSIBILITY = "responsibility"
    NEXT_ACTION = "next_action"


class RelationType(StrEnum):
    """How one handover item bears on another.

    Deliberately few. Each one has to be something a source can actually
    state, because a relationship is only drawn when a quotation supports it.
    """

    BLOCKS = "blocks"
    DEPENDS_ON = "depends_on"
    RESULTED_IN = "resulted_in"
    ADDRESSES = "addresses"
    OWNED_BY = "owned_by"
    RELATES_TO = "relates_to"


RELATION_LABELS: dict[str, str] = {
    RelationType.BLOCKS: "blocks",
    RelationType.DEPENDS_ON: "depends on",
    RelationType.RESULTED_IN: "resulted in",
    RelationType.ADDRESSES: "addresses",
    RelationType.OWNED_BY: "owned by",
    RelationType.RELATES_TO: "relates to",
}

RELATION_DESCRIPTIONS: dict[str, str] = {
    RelationType.BLOCKS: "The first item is preventing the second from progressing.",
    RelationType.DEPENDS_ON: "The first item cannot complete until the second does.",
    RelationType.RESULTED_IN: "The first item caused or produced the second.",
    RelationType.ADDRESSES: "The first item is the proposed response to the second.",
    RelationType.OWNED_BY: "The first item is the responsibility named by the second.",
    RelationType.RELATES_TO: "The sources connect these two, without saying how.",
}


def relation_label(value: str | None) -> str:
    return RELATION_LABELS.get(value or "", (value or "relates to").replace("_", " "))


class ReviewAction(StrEnum):
    EDITED = "edited"
    APPROVED = "approved"
    REJECTED = "rejected"
    REOPENED = "reopened"
    NOTE_ADDED = "note_added"


# --------------------------------------------------------------------------
# Display labels -- the UI shows these, never the raw enum value
# --------------------------------------------------------------------------

EXTRACTION_LABELS: dict[str, str] = {
    ExtractionStatus.UPLOADED: "Uploaded",
    ExtractionStatus.EXTRACTED: "Extracted",
    ExtractionStatus.PARTIALLY_EXTRACTED: "Partially extracted",
    ExtractionStatus.NO_TEXT_EXTRACTED: "No text found",
    ExtractionStatus.FAILED: "Failed",
}

RECONSTRUCTION_LABELS: dict[str, str] = {
    ReconstructionStatus.NOT_STARTED: "Not started",
    ReconstructionStatus.PROCESSING: "Processing",
    ReconstructionStatus.DRAFT_READY: "Draft ready",
    ReconstructionStatus.FAILED: "Failed",
}

REVIEW_LABELS: dict[str, str] = {
    ReviewStatus.PROPOSED: "Proposed",
    ReviewStatus.APPROVED: "Approved",
    ReviewStatus.REJECTED: "Rejected",
}

EVIDENCE_LABELS: dict[str, str] = {
    EvidenceStatus.SUPPORTED: "Supported",
    EvidenceStatus.PARTIALLY_SUPPORTED: "Partially supported",
    EvidenceStatus.CONFLICTING: "Conflicting",
    EvidenceStatus.UNSUPPORTED: "Unsupported",
    EvidenceStatus.NEEDS_REVIEW: "Needs review",
}

#: Section order for the review screen and the exported report.
CATEGORY_LABELS: dict[str, str] = {
    ItemCategory.PROJECT_OVERVIEW: "Project overview",
    ItemCategory.DECISION: "Decisions",
    ItemCategory.COMPLETED_WORK: "Completed work",
    ItemCategory.PENDING_WORK: "Pending work",
    ItemCategory.BLOCKER: "Blockers",
    ItemCategory.DEPENDENCY: "Dependencies",
    ItemCategory.RISK: "Risks",
    ItemCategory.OPEN_QUESTION: "Open questions",
    ItemCategory.RESPONSIBILITY: "Responsibilities and owners",
    ItemCategory.NEXT_ACTION: "Recommended next actions",
}

CATEGORY_ORDER: list[str] = list(CATEGORY_LABELS.keys())

#: Phrase used wherever a source does not establish a fact. Never inferred.
NOT_SPECIFIED = "Not specified in the provided sources."


def extraction_label(value: str | None) -> str:
    return EXTRACTION_LABELS.get(value or "", "Unknown")


def reconstruction_label(value: str | None) -> str:
    return RECONSTRUCTION_LABELS.get(value or "", "Not started")


def review_label(value: str | None) -> str:
    return REVIEW_LABELS.get(value or "", "Unknown")


def evidence_label(value: str | None) -> str:
    return EVIDENCE_LABELS.get(value or "", "Unknown")


def category_label(value: str | None) -> str:
    return CATEGORY_LABELS.get(value or "", (value or "Other").replace("_", " ").title())
