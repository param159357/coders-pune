"""Server-side verification of every evidence reference a model produces.

Nothing the model says about evidence is taken on trust. For each reference:

1. the source ID must resolve to a real source **in this project** — a
   fabricated or cross-project ID is discarded, never stored;
2. the excerpt must actually occur in that source's extracted text;
3. the location is recomputed from the segment the excerpt was found in, so a
   wrong page number is corrected rather than displayed as fact.

The resulting evidence status can only ever be *lowered* relative to what the
model claimed. Verification removes unwarranted confidence; it never adds any.
A claim with nothing verifiable behind it becomes ``unsupported`` no matter how
confident the model was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from trace_app.ai.schemas import ConflictNote, EvidenceRef, HandoverItemDraft
from trace_app.models import EvidenceStatus, ItemCategory
from trace_app.services.extraction_service import Segment, segments_from_json

#: An excerpt shorter than this cannot meaningfully support anything.
MIN_EXCERPT_CHARS = 12

_PUNCTUATION_EDGES = " \t\r\n\"'“”‘’`.,;:!?()[]{}…-–—"


def normalise(text: str) -> str:
    """Lower-case and collapse whitespace so quoting style cannot defeat a match."""
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _match_key(text: str) -> str:
    """Normalised form with decorative edge punctuation removed."""
    return normalise(text).strip(_PUNCTUATION_EDGES)


@dataclass
class VerifiedEvidence:
    """One evidence reference after checking it against the real source."""

    source_id: str
    source_display_name: str
    excerpt: str
    location_label: str | None
    claimed_location: str | None
    verified: bool
    reason: str | None = None
    location_corrected: bool = False
    start_offset: int | None = None
    end_offset: int | None = None

    @property
    def storable(self) -> bool:
        """Whether this row may be written to the evidence table.

        Only references to a real source in this project are stored; the
        foreign key guarantees the rest.
        """
        return bool(self.source_id and self.source_display_name)


@dataclass
class VerifiedItem:
    category: str
    text: str
    evidence: list[VerifiedEvidence] = field(default_factory=list)
    evidence_status: str = EvidenceStatus.UNSUPPORTED
    claimed_evidence_status: str = EvidenceStatus.NEEDS_REVIEW
    owner: str | None = None
    due_date: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def verified_count(self) -> int:
        return sum(1 for item in self.evidence if item.verified)

    @property
    def storable_evidence(self) -> list[VerifiedEvidence]:
        return [item for item in self.evidence if item.storable]


@dataclass
class VerificationReport:
    """Aggregate counts, surfaced to the user so the checking is visible."""

    evidence_total: int = 0
    evidence_verified: int = 0
    fabricated_source_ids: int = 0
    excerpt_not_found: int = 0
    locations_corrected: int = 0
    items_downgraded: int = 0

    def as_notes(self) -> list[str]:
        notes: list[str] = []
        if self.evidence_total:
            notes.append(
                f"Checked {self.evidence_total} evidence reference(s) against the "
                f"project's sources; {self.evidence_verified} matched the source "
                "text exactly."
            )
        if self.fabricated_source_ids:
            notes.append(
                f"{self.fabricated_source_ids} reference(s) named a source that "
                "does not exist in this project and were discarded."
            )
        if self.excerpt_not_found:
            notes.append(
                f"{self.excerpt_not_found} excerpt(s) could not be found in the "
                "source they were attributed to and are not treated as support."
            )
        if self.locations_corrected:
            notes.append(
                f"{self.locations_corrected} location reference(s) were corrected "
                "to the place the excerpt actually appears."
            )
        if self.items_downgraded:
            notes.append(
                f"{self.items_downgraded} item(s) had their evidence status "
                "lowered because the evidence did not hold up."
            )
        return notes


class SourceIndex:
    """Searchable view of one project's sources. The only lookup allowed.

    Built from rows already scoped to a single project, so a source ID from
    another project simply is not present and cannot resolve.
    """

    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self._sources: dict[str, dict[str, Any]] = {}
        self._normalised_text: dict[str, str] = {}
        self._segments: dict[str, list[Segment]] = {}

        for source in sources:
            source_id = str(source["id"])
            self._sources[source_id] = source
            self._normalised_text[source_id] = normalise(source.get("extracted_text"))
            self._segments[source_id] = segments_from_json(source.get("segments_json"))

    def __contains__(self, source_id: object) -> bool:
        return str(source_id) in self._sources

    def __len__(self) -> int:
        return len(self._sources)

    @property
    def source_ids(self) -> list[str]:
        return list(self._sources)

    def display_name(self, source_id: str) -> str:
        return str(self._sources.get(source_id, {}).get("display_name", ""))

    def get(self, source_id: str) -> dict[str, Any] | None:
        return self._sources.get(source_id)

    def locate(self, source_id: str, excerpt: str) -> tuple[bool, str | None, Segment | None]:
        """Find ``excerpt`` in a source.

        Returns ``(found, location_label, segment)``. When the excerpt spans a
        segment boundary it is still found, but reported without a single
        precise location rather than being attributed to the wrong one.
        """
        needle = _match_key(excerpt)
        if len(needle) < MIN_EXCERPT_CHARS:
            return False, None, None

        for segment in self._segments.get(source_id, []):
            if needle in _match_key(segment.text):
                return True, segment.location_label, segment
        if needle in self._normalised_text.get(source_id, ""):
            return True, "spans multiple locations", None
        return False, None, None


def resolve_evidence_status(
    claimed: str, verified_count: int, total_count: int
) -> str:
    """Decide an item's evidence status from what actually verified.

    The model's own assessment acts as a ceiling. This function can lower it
    and never raises it, so a model that calls everything "supported" gains
    nothing by doing so.
    """
    if total_count == 0 or verified_count == 0:
        return EvidenceStatus.UNSUPPORTED

    if claimed == EvidenceStatus.CONFLICTING:
        # A conflict needs at least two verified references to be a conflict.
        return (
            EvidenceStatus.CONFLICTING
            if verified_count >= 2
            else EvidenceStatus.NEEDS_REVIEW
        )

    # Never upgrade a cautious model.
    if claimed in (EvidenceStatus.UNSUPPORTED, EvidenceStatus.NEEDS_REVIEW):
        return claimed

    if verified_count < total_count:
        return EvidenceStatus.PARTIALLY_SUPPORTED
    return claimed


def verify_reference(
    reference: EvidenceRef, index: SourceIndex, report: VerificationReport
) -> VerifiedEvidence:
    """Check a single claimed reference against the real sources."""
    report.evidence_total += 1
    source_id = str(reference.source_id)

    if source_id not in index:
        report.fabricated_source_ids += 1
        return VerifiedEvidence(
            source_id=source_id,
            source_display_name="",
            excerpt=reference.excerpt,
            location_label=None,
            claimed_location=reference.location,
            verified=False,
            reason=(
                "This reference names a source that does not exist in this "
                "project, so it was discarded."
            ),
        )

    found, true_location, segment = index.locate(source_id, reference.excerpt)
    if not found:
        report.excerpt_not_found += 1
        return VerifiedEvidence(
            source_id=source_id,
            source_display_name=index.display_name(source_id),
            excerpt=reference.excerpt,
            location_label=None,
            claimed_location=reference.location,
            verified=False,
            reason=(
                "This quotation could not be found in the source it was "
                "attributed to, so it does not count as support."
            ),
        )

    claimed_location = reference.location
    corrected = bool(
        claimed_location
        and true_location
        and normalise(claimed_location) != normalise(true_location)
    )
    if corrected:
        report.locations_corrected += 1

    report.evidence_verified += 1
    return VerifiedEvidence(
        source_id=source_id,
        source_display_name=index.display_name(source_id),
        excerpt=reference.excerpt,
        location_label=true_location,
        claimed_location=claimed_location,
        verified=True,
        location_corrected=corrected,
        start_offset=segment.start_offset if segment else None,
        end_offset=segment.end_offset if segment else None,
    )


def verify_item(
    draft: HandoverItemDraft, index: SourceIndex, report: VerificationReport
) -> VerifiedItem:
    """Verify one drafted item and settle its true evidence status."""
    evidence = [verify_reference(ref, index, report) for ref in draft.evidence]
    verified_count = sum(1 for item in evidence if item.verified)

    claimed = str(draft.evidence_status)
    resolved = resolve_evidence_status(claimed, verified_count, len(evidence))

    notes: list[str] = []
    if resolved != claimed:
        report.items_downgraded += 1
        notes.append(
            f"The model proposed “{claimed.replace('_', ' ')}”; verification "
            f"against the sources gives “{resolved.replace('_', ' ')}”."
        )
    if any(item.location_corrected for item in evidence):
        notes.append("One or more source locations were corrected during checking.")

    # An owner or date is only kept when something actually verified backs it up.
    owner = draft.owner if verified_count else None
    due_date = draft.due_date if verified_count else None
    if draft.owner and not owner:
        notes.append("A proposed owner was dropped because no evidence verified.")
    if draft.due_date and not due_date:
        notes.append("A proposed date was dropped because no evidence verified.")

    return VerifiedItem(
        category=str(draft.category),
        text=draft.text,
        evidence=evidence,
        evidence_status=resolved,
        claimed_evidence_status=claimed,
        owner=owner,
        due_date=due_date,
        notes=notes,
    )


def verify_conflict(
    conflict: ConflictNote, index: SourceIndex, report: VerificationReport
) -> VerifiedItem:
    """Turn a reported disagreement into a reviewable open question.

    Both sides are preserved. TRACE does not choose between them.
    """
    evidence = [verify_reference(ref, index, report) for ref in conflict.evidence]
    verified_count = sum(1 for item in evidence if item.verified)
    status = resolve_evidence_status(
        EvidenceStatus.CONFLICTING, verified_count, len(evidence)
    )
    return VerifiedItem(
        category=str(ItemCategory.OPEN_QUESTION),
        text=conflict.description,
        evidence=evidence,
        evidence_status=status,
        claimed_evidence_status=str(EvidenceStatus.CONFLICTING),
        notes=[
            "The sources disagree here. Both references are kept; TRACE does "
            "not decide which is correct."
        ],
    )
