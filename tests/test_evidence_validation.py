"""Evidence verification tests (spec sections 6 and 14).

These are the tests that matter most. TRACE's claim is that a model cannot
make an unsupported statement look supported, so each test here takes the
position of a model that is trying to do exactly that.
"""

from __future__ import annotations

import json

import pytest

from trace_app.ai.schemas import ConflictNote, EvidenceRef, HandoverItemDraft
from trace_app.models import EvidenceStatus, ItemCategory
from trace_app.services.evidence_service import (
    SourceIndex,
    VerificationReport,
    normalise,
    resolve_evidence_status,
    verify_conflict,
    verify_item,
)

SOURCE_TEXT = (
    "The initial release focuses on event discovery and student registration.\n\n"
    "Payment integration was postponed until after the first release.\n\n"
    "Registration validation remains unfinished."
)


def _segments() -> str:
    cursor = 0
    segments = []
    for position, block in enumerate(SOURCE_TEXT.split("\n\n"), start=1):
        segments.append(
            {
                "index": position - 1,
                "location_label": f"page {position}",
                "text": block,
                "start_offset": cursor,
                "end_offset": cursor + len(block),
            }
        )
        cursor += len(block) + 2
    return json.dumps(segments)


@pytest.fixture()
def index() -> SourceIndex:
    return SourceIndex(
        [
            {
                "id": "src_real0001",
                "display_name": "project_brief.txt",
                "extracted_text": SOURCE_TEXT,
                "segments_json": _segments(),
            }
        ]
    )


def _item(**kwargs) -> HandoverItemDraft:
    defaults = {
        "category": ItemCategory.DECISION,
        "text": "A claim.",
        "evidence": [],
        "evidence_status": EvidenceStatus.SUPPORTED,
    }
    return HandoverItemDraft(**{**defaults, **kwargs})


# --------------------------------------------------------------------------
# Valid evidence
# --------------------------------------------------------------------------

def test_an_exact_quotation_verifies(index: SourceIndex) -> None:
    report = VerificationReport()
    result = verify_item(
        _item(
            text="Payment integration was postponed.",
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    location="page 2",
                    excerpt="Payment integration was postponed until after the first release.",
                )
            ],
        ),
        index,
        report,
    )
    assert result.evidence[0].verified
    assert result.evidence[0].location_label == "page 2"
    assert result.evidence_status == EvidenceStatus.SUPPORTED
    assert report.evidence_verified == 1


def test_quoting_style_does_not_defeat_a_match(index: SourceIndex) -> None:
    """Different casing, spacing, and wrapping quotes still match."""
    report = VerificationReport()
    result = verify_item(
        _item(
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    location="page 2",
                    excerpt='  "PAYMENT   integration was postponed"  ',
                )
            ]
        ),
        index,
        report,
    )
    assert result.evidence[0].verified


# --------------------------------------------------------------------------
# Fabrication
# --------------------------------------------------------------------------

def test_a_fabricated_source_id_is_rejected_and_never_stored(
    index: SourceIndex,
) -> None:
    report = VerificationReport()
    result = verify_item(
        _item(
            evidence=[
                EvidenceRef(
                    source_id="src_doesnotexist",
                    location="page 1",
                    excerpt="The initial release focuses on event discovery.",
                )
            ]
        ),
        index,
        report,
    )
    assert not result.evidence[0].verified
    assert not result.evidence[0].storable  # never written to the evidence table
    assert result.evidence_status == EvidenceStatus.UNSUPPORTED
    assert report.fabricated_source_ids == 1


def test_a_paraphrased_excerpt_does_not_count_as_support(index: SourceIndex) -> None:
    """The quotation must be in the source, not merely consistent with it."""
    report = VerificationReport()
    result = verify_item(
        _item(
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    location="page 2",
                    excerpt="The team decided to delay payments to a later phase.",
                )
            ]
        ),
        index,
        report,
    )
    assert not result.evidence[0].verified
    assert result.evidence_status == EvidenceStatus.UNSUPPORTED
    assert report.excerpt_not_found == 1
    # The reference is still storable (the source is real) but marked unverified,
    # so a reviewer can see what was claimed and why it was rejected.
    assert result.evidence[0].storable
    assert "could not be found" in result.evidence[0].reason


def test_a_claim_with_no_evidence_at_all_is_unsupported(index: SourceIndex) -> None:
    report = VerificationReport()
    result = verify_item(
        _item(text="The project will launch in March.", evidence=[]), index, report
    )
    assert result.evidence_status == EvidenceStatus.UNSUPPORTED


def test_a_confident_model_cannot_upgrade_itself(index: SourceIndex) -> None:
    """Claiming 'supported' with nothing behind it changes nothing."""
    report = VerificationReport()
    result = verify_item(
        _item(
            text="Everything is on track.",
            evidence_status=EvidenceStatus.SUPPORTED,
            evidence=[
                EvidenceRef(
                    source_id="src_real0001", excerpt="everything is on track"
                )
            ],
        ),
        index,
        report,
    )
    assert result.claimed_evidence_status == EvidenceStatus.SUPPORTED
    assert result.evidence_status == EvidenceStatus.UNSUPPORTED
    assert report.items_downgraded == 1


def test_a_cautious_model_is_never_upgraded(index: SourceIndex) -> None:
    """Verification lowers confidence; it must never raise it."""
    report = VerificationReport()
    result = verify_item(
        _item(
            evidence_status=EvidenceStatus.NEEDS_REVIEW,
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    excerpt="Registration validation remains unfinished.",
                )
            ],
        ),
        index,
        report,
    )
    assert result.evidence[0].verified
    assert result.evidence_status == EvidenceStatus.NEEDS_REVIEW


def test_mixed_evidence_is_only_partially_supported(index: SourceIndex) -> None:
    report = VerificationReport()
    result = verify_item(
        _item(
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    excerpt="Payment integration was postponed",
                ),
                EvidenceRef(
                    source_id="src_real0001", excerpt="A sentence that is not there"
                ),
            ]
        ),
        index,
        report,
    )
    assert result.verified_count == 1
    assert result.evidence_status == EvidenceStatus.PARTIALLY_SUPPORTED


def test_a_trivially_short_excerpt_cannot_support_anything(
    index: SourceIndex,
) -> None:
    report = VerificationReport()
    result = verify_item(
        _item(evidence=[EvidenceRef(source_id="src_real0001", excerpt="the")]),
        index,
        report,
    )
    assert not result.evidence[0].verified


# --------------------------------------------------------------------------
# Location correctness
# --------------------------------------------------------------------------

def test_a_wrong_page_number_is_corrected_not_displayed(index: SourceIndex) -> None:
    report = VerificationReport()
    result = verify_item(
        _item(
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    location="page 9",  # wrong
                    excerpt="Payment integration was postponed",
                )
            ]
        ),
        index,
        report,
    )
    evidence = result.evidence[0]
    assert evidence.verified
    assert evidence.claimed_location == "page 9"
    assert evidence.location_label == "page 2"  # the truth
    assert evidence.location_corrected
    assert report.locations_corrected == 1


def test_an_excerpt_spanning_segments_is_not_misattributed(
    index: SourceIndex,
) -> None:
    report = VerificationReport()
    result = verify_item(
        _item(
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    location="page 1",
                    excerpt=(
                        "student registration. Payment integration was postponed"
                    ),
                )
            ]
        ),
        index,
        report,
    )
    assert result.evidence[0].verified
    assert result.evidence[0].location_label == "spans multiple locations"


# --------------------------------------------------------------------------
# Cross-project isolation
# --------------------------------------------------------------------------

def test_a_source_from_another_project_cannot_be_cited() -> None:
    """The index holds one project's sources, so another project's ID is unknown."""
    alpha_index = SourceIndex(
        [
            {
                "id": "src_alpha",
                "display_name": "alpha.txt",
                "extracted_text": "Alpha content here.",
                "segments_json": None,
            }
        ]
    )
    report = VerificationReport()
    result = verify_item(
        _item(
            evidence=[
                EvidenceRef(source_id="src_beta", excerpt="Beta content here.")
            ]
        ),
        alpha_index,
        report,
    )
    assert "src_beta" not in alpha_index
    assert not result.evidence[0].verified
    assert not result.evidence[0].storable
    assert report.fabricated_source_ids == 1


# --------------------------------------------------------------------------
# Conflicts
# --------------------------------------------------------------------------

def test_a_conflict_keeps_both_references(index: SourceIndex) -> None:
    report = VerificationReport()
    result = verify_conflict(
        ConflictNote(
            description="The sources disagree about the release scope.",
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    excerpt="The initial release focuses on event discovery",
                ),
                EvidenceRef(
                    source_id="src_real0001",
                    excerpt="Registration validation remains unfinished",
                ),
            ],
        ),
        index,
        report,
    )
    assert len(result.evidence) == 2
    assert all(evidence.verified for evidence in result.evidence)
    assert result.evidence_status == EvidenceStatus.CONFLICTING
    assert result.category == ItemCategory.OPEN_QUESTION
    assert "does not decide" in " ".join(result.notes)


def test_a_one_sided_conflict_needs_review_not_conflicting(
    index: SourceIndex,
) -> None:
    report = VerificationReport()
    result = verify_conflict(
        ConflictNote(
            description="Possibly a disagreement.",
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    excerpt="Payment integration was postponed",
                ),
                EvidenceRef(source_id="src_real0001", excerpt="not in the document"),
            ],
        ),
        index,
        report,
    )
    assert result.evidence_status == EvidenceStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# Owners and dates
# --------------------------------------------------------------------------

def test_an_unevidenced_owner_is_dropped(index: SourceIndex) -> None:
    """An owner the sources do not establish must not survive."""
    report = VerificationReport()
    result = verify_item(
        _item(
            category=ItemCategory.RESPONSIBILITY,
            text="Someone owns the accessibility review.",
            owner="Priya Sharma",
            due_date="2026-03-01",
            evidence=[
                EvidenceRef(source_id="src_real0001", excerpt="no such sentence")
            ],
        ),
        index,
        report,
    )
    assert result.owner is None
    assert result.due_date is None
    assert any("owner was dropped" in note for note in result.notes)


def test_an_evidenced_owner_is_kept(index: SourceIndex) -> None:
    report = VerificationReport()
    result = verify_item(
        _item(
            category=ItemCategory.RESPONSIBILITY,
            owner="The team",
            evidence=[
                EvidenceRef(
                    source_id="src_real0001",
                    excerpt="Payment integration was postponed",
                )
            ],
        ),
        index,
        report,
    )
    assert result.owner == "The team"


# --------------------------------------------------------------------------
# Status resolution table
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "claimed, verified, total, expected",
    [
        (EvidenceStatus.SUPPORTED, 0, 0, EvidenceStatus.UNSUPPORTED),
        (EvidenceStatus.SUPPORTED, 0, 3, EvidenceStatus.UNSUPPORTED),
        (EvidenceStatus.SUPPORTED, 1, 3, EvidenceStatus.PARTIALLY_SUPPORTED),
        (EvidenceStatus.SUPPORTED, 3, 3, EvidenceStatus.SUPPORTED),
        (EvidenceStatus.PARTIALLY_SUPPORTED, 2, 2, EvidenceStatus.PARTIALLY_SUPPORTED),
        (EvidenceStatus.CONFLICTING, 1, 2, EvidenceStatus.NEEDS_REVIEW),
        (EvidenceStatus.CONFLICTING, 2, 2, EvidenceStatus.CONFLICTING),
        (EvidenceStatus.UNSUPPORTED, 3, 3, EvidenceStatus.UNSUPPORTED),
        (EvidenceStatus.NEEDS_REVIEW, 3, 3, EvidenceStatus.NEEDS_REVIEW),
    ],
)
def test_status_resolution_never_upgrades(claimed, verified, total, expected) -> None:
    assert resolve_evidence_status(claimed, verified, total) == expected


def test_normalise_collapses_whitespace_and_case() -> None:
    assert normalise("  The   QUICK\n\nbrown  ") == "the quick brown"
    assert normalise(None) == ""
