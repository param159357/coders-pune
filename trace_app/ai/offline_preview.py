"""Deterministic, rule-based reconstruction used when no model is configured.

This is **not** a model and must never be presented as one. It is a plain
keyword matcher: it finds sentences in the uploaded sources that contain
category cue phrases and proposes each one as a handover item quoting itself.

Why it exists: the specification allows a clearly labelled deterministic
preview so the rest of the workflow — evidence inspection, review, approval,
export — can still be demonstrated when ``LLM_API_KEY`` is absent. Every
reconstruction produced this way is flagged ``is_demo_preview`` in the
database and labelled in the UI and in the exported report.

Because each item quotes the exact sentence it came from, this output passes
the same evidence verification as model output. It makes no judgements and
draws no inferences, which is precisely its limitation.
"""

from __future__ import annotations

import re
from typing import Any

from trace_app.ai.schemas import (
    EvidenceRef,
    HandoverItemDraft,
    ReconstructionDraft,
)
from trace_app.models import EvidenceStatus, ItemCategory
from trace_app.utils.text_chunking import _segments_for

LABEL = "Deterministic preview (no AI model was called)"

#: Cue phrases per category, checked case-insensitively against each sentence.
#: Order matters: the first category whose cue matches wins, so the ones that
#: describe trouble are checked before the ones that describe success. Without
#: this, "the queue is not finished" would be read as completed work.
_CUES: dict[str, tuple[str, ...]] = {
    ItemCategory.BLOCKER: (
        "blocked", "blocker", "cannot proceed", "waiting on", "waiting for",
        "prevents", "is stuck", "held up",
    ),
    ItemCategory.RISK: (
        "risk", "concern", "may fail", "could fail", "uncertain", "fragile",
        "vulnerable", "threat",
    ),
    ItemCategory.OPEN_QUESTION: (
        "unclear", "not documented", "no confirmed", "undecided", "tbd",
        "to be decided", "open question", "unknown whether", "not specified",
    ),
    ItemCategory.PENDING_WORK: (
        "remains", "unfinished", "pending", "in progress", "not yet",
        "still to", "outstanding", "incomplete", "to be done",
    ),
    ItemCategory.DEPENDENCY: (
        "depends on", "dependent on", "dependency", "requires approval",
        "subject to", "contingent on",
    ),
    ItemCategory.DECISION: (
        "decided", "decision", "selected", "chose", "chosen", "agreed",
        "postponed", "deferred", "opted", "will use", "approved to",
    ),
    ItemCategory.RESPONSIBILITY: (
        "responsible for", "owner", "owns", "assigned to", "is leading",
        "accountable",
    ),
    ItemCategory.NEXT_ACTION: (
        "next step", "should be", "must be", "needs to", "need to",
        "plan to", "recommended", "action required",
    ),
    ItemCategory.COMPLETED_WORK: (
        "is complete", "are complete", "completed", "finished", "has shipped",
        "was implemented", "has been implemented", "is done", "delivered",
    ),
}

#: Sentence splitter. A segment is one paragraph, so its internal newlines are
#: line wrapping, not sentence boundaries; they are collapsed first and only a
#: real terminator ends a sentence. Splitting on newlines would otherwise cut
#: wrapped sentences into meaningless fragments.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

#: Cues are matched on word boundaries, so "unfinished" does not count as
#: "finished" and "incomplete" does not count as "complete".
_CUE_PATTERNS: dict[str, re.Pattern[str]] = {
    category: re.compile(
        r"\b(?:" + "|".join(re.escape(cue) for cue in cues) + r")\b"
    )
    for category, cues in _CUES.items()
}

_MIN_SENTENCE_CHARS = 25
_MAX_SENTENCE_CHARS = 320
_MAX_ITEMS_PER_CATEGORY = 6

#: A descriptive sentence ("X is a Y") makes a far better stand-in for an
#: overview than a document title, which is what the first line usually is.
_DESCRIPTIVE = re.compile(r"\b(?:is|are|provides|serves|supports)\s+(?:a|an|the)\b")
_MIN_OVERVIEW_CHARS = 45


def _is_overview_candidate(sentence: str) -> bool:
    """Whether a sentence reads like a description rather than a heading."""
    return (
        len(sentence) >= _MIN_OVERVIEW_CHARS
        and sentence.endswith(".")
        and bool(_DESCRIPTIVE.search(sentence.lower()))
    )


def _sentences(text: str) -> list[str]:
    collapsed = " ".join(str(text or "").split())
    out: list[str] = []
    for raw in _SENTENCE.split(collapsed):
        sentence = " ".join(raw.split())
        if _MIN_SENTENCE_CHARS <= len(sentence) <= _MAX_SENTENCE_CHARS:
            out.append(sentence)
    return out


def build_preview(sources: list[dict[str, Any]]) -> ReconstructionDraft:
    """Build a draft by literal sentence matching. Never calls a model."""
    items: list[HandoverItemDraft] = []
    per_category: dict[str, int] = {}
    seen: set[str] = set()
    overview_added = False

    for source in sources:
        source_id = str(source["id"])
        for segment in _segments_for(source):
            for sentence in _sentences(segment.text):
                # A descriptive sentence stands in for an overview. It is
                # quoted, never written, so the preview invents nothing.
                if not overview_added and _is_overview_candidate(sentence):
                    items.append(
                        HandoverItemDraft(
                            category=ItemCategory.PROJECT_OVERVIEW,
                            text=sentence,
                            evidence=[
                                EvidenceRef(
                                    source_id=source_id,
                                    location=segment.location_label,
                                    excerpt=sentence,
                                )
                            ],
                            evidence_status=EvidenceStatus.SUPPORTED,
                        )
                    )
                    overview_added = True
                    seen.add(sentence.lower())
                    continue

                lowered = sentence.lower()
                if lowered in seen:
                    continue

                for category, pattern in _CUE_PATTERNS.items():
                    if per_category.get(category, 0) >= _MAX_ITEMS_PER_CATEGORY:
                        continue
                    if not pattern.search(lowered):
                        continue
                    items.append(
                        HandoverItemDraft(
                            category=category,
                            text=sentence,
                            evidence=[
                                EvidenceRef(
                                    source_id=source_id,
                                    location=segment.location_label,
                                    excerpt=sentence,
                                )
                            ],
                            # The item text *is* the quotation, so the source
                            # supports it literally. Whether it is the right
                            # thing to carry into a handover is the reviewer's
                            # call, which is the point of the review step.
                            evidence_status=EvidenceStatus.SUPPORTED,
                        )
                    )
                    per_category[category] = per_category.get(category, 0) + 1
                    seen.add(lowered)
                    break

    if not overview_added:
        # Nothing read like a description; fall back to the first full
        # sentence rather than leaving the overview empty.
        for source in sources:
            for segment in _segments_for(source):
                for sentence in _sentences(segment.text):
                    if sentence.endswith("."):
                        items.insert(
                            0,
                            HandoverItemDraft(
                                category=ItemCategory.PROJECT_OVERVIEW,
                                text=sentence,
                                evidence=[
                                    EvidenceRef(
                                        source_id=str(source["id"]),
                                        location=segment.location_label,
                                        excerpt=sentence,
                                    )
                                ],
                                evidence_status=EvidenceStatus.SUPPORTED,
                            ),
                        )
                        overview_added = True
                        break
                if overview_added:
                    break
            if overview_added:
                break

    # The overview belongs at the front, wherever in the sources it was found.
    items.sort(key=lambda item: 0 if item.category == ItemCategory.PROJECT_OVERVIEW else 1)

    return ReconstructionDraft(
        items=items,
        conflicts=[],
        missing_information=[
            "This draft was produced by deterministic keyword matching, not by "
            "an AI model. It quotes sentences that contain category cue words "
            "and makes no judgement about what they mean.",
            "It cannot detect contradictions between sources, cannot summarise, "
            "and cannot tell an important statement from an incidental one.",
            "Configure LLM_API_KEY and run the reconstruction again for a real "
            "analysis.",
        ],
    )
