"""Pydantic models for structured reconstruction output.

Model output is untrusted until it has passed through here. Validation does
two things: it rejects anything structurally wrong (an invented category, a
malformed evidence reference), and it normalises what remains so the rest of
the pipeline works with predictable values.

Validation alone does **not** make a claim true. The source IDs and excerpts
that survive this layer are still checked against the real source rows by
``trace_app.services.evidence_service`` before any item is shown as supported.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trace_app.models import EvidenceStatus, ItemCategory, RelationType

MAX_ITEM_TEXT = 800
MAX_EXCERPT = 400
MAX_ITEMS = 120

#: Values the model may use to say "the sources do not establish this".
_NULL_WORDS = {
    "",
    "null",
    "none",
    "n/a",
    "na",
    "unknown",
    "not specified",
    "not stated",
    "not documented",
    "not specified in the provided sources",
    "unspecified",
    "tbd",
    "-",
}


def _blank_to_none(value: str | None) -> str | None:
    """Collapse the many ways a model says 'nothing here' into ``None``."""
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    if cleaned.lower().strip(" .") in _NULL_WORDS:
        return None
    return cleaned


class EvidenceRef(BaseModel):
    """A claimed pointer into a source document. Claimed, not yet verified."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=64)
    location: str | None = Field(default=None, max_length=120)
    excerpt: str = Field(min_length=1, max_length=MAX_EXCERPT)

    @field_validator("location", mode="before")
    @classmethod
    def _normalise_location(cls, value):
        return _blank_to_none(value)

    @field_validator("excerpt", mode="before")
    @classmethod
    def _normalise_excerpt(cls, value):
        if value is None:
            return value
        collapsed = " ".join(str(value).split())
        return collapsed[:MAX_EXCERPT]


class HandoverItemDraft(BaseModel):
    """One proposed handover item, before human review."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    #: A short handle the model assigns so relationships can refer to this
    #: item within the same reply. It is never stored: after validation the
    #: refs are mapped onto TRACE's own identifiers and discarded.
    ref: str | None = Field(default=None, max_length=32)
    category: ItemCategory
    text: str = Field(min_length=1, max_length=MAX_ITEM_TEXT)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=12)
    #: The model's own assessment. Treated as a ceiling, never as a guarantee:
    #: evidence verification may lower it but never raises it.
    evidence_status: EvidenceStatus = EvidenceStatus.NEEDS_REVIEW
    owner: str | None = Field(default=None, max_length=160)
    due_date: str | None = Field(default=None, max_length=60)

    @field_validator("text", mode="before")
    @classmethod
    def _normalise_text(cls, value):
        if value is None:
            return value
        return " ".join(str(value).split())[:MAX_ITEM_TEXT]

    @field_validator("owner", "due_date", mode="before")
    @classmethod
    def _normalise_optional(cls, value):
        return _blank_to_none(value)

    @field_validator("evidence_status", mode="before")
    @classmethod
    def _default_unknown_status(cls, value):
        """An unrecognised status becomes 'needs review', never 'supported'."""
        if value is None:
            return EvidenceStatus.NEEDS_REVIEW
        candidate = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        if candidate not in set(EvidenceStatus):
            return EvidenceStatus.NEEDS_REVIEW
        return candidate


class RelationshipDraft(BaseModel):
    """A claimed link between two items, with the quotation supporting it.

    Both endpoints are the model's own ``ref`` handles from the same reply. A
    relationship whose endpoints do not resolve, or whose quotation does not
    verify, is discarded rather than drawn.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    from_ref: str = Field(min_length=1, max_length=32)
    to_ref: str = Field(min_length=1, max_length=32)
    relation: RelationType
    evidence: EvidenceRef

    @field_validator("relation", mode="before")
    @classmethod
    def _default_unknown_relation(cls, value):
        """An unrecognised relation becomes the weakest one, not a guess."""
        if value is None:
            return RelationType.RELATES_TO
        candidate = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        if candidate not in set(RelationType):
            return RelationType.RELATES_TO
        return candidate


class ConflictNote(BaseModel):
    """A disagreement between sources. Both sides are preserved, never resolved."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    description: str = Field(min_length=1, max_length=MAX_ITEM_TEXT)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=12)

    @field_validator("description", mode="before")
    @classmethod
    def _normalise_description(cls, value):
        if value is None:
            return value
        return " ".join(str(value).split())[:MAX_ITEM_TEXT]


class ReconstructionDraft(BaseModel):
    """The full validated payload returned by one model call.

    The project overview is carried as an item with category
    ``project_overview`` rather than as a separate top-level field. This is a
    deliberate simplification of the shape sketched in the specification: it
    gives the overview the same review, approval, and evidence path as every
    other item, instead of a parallel one that would have to be special-cased
    in the review screen and the report.
    """

    model_config = ConfigDict(extra="ignore")

    items: list[HandoverItemDraft] = Field(default_factory=list, max_length=MAX_ITEMS)
    relationships: list[RelationshipDraft] = Field(
        default_factory=list, max_length=MAX_ITEMS
    )
    conflicts: list[ConflictNote] = Field(default_factory=list, max_length=40)
    missing_information: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("missing_information", mode="before")
    @classmethod
    def _normalise_missing(cls, value):
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for entry in value:
            text = _blank_to_none(entry if isinstance(entry, str) else str(entry))
            if text:
                cleaned.append(text[:MAX_ITEM_TEXT])
        return cleaned

    def items_for_category(self, category: str) -> list[HandoverItemDraft]:
        return [item for item in self.items if item.category == category]


#: Shown to the model so it returns the right shape. Kept in one place so the
#: prompt and the validator cannot drift apart.
JSON_SHAPE_HINT = """{
  "items": [
    {
      "ref": "i1",
      "category": "decision",
      "text": "One concise, self-contained statement.",
      "evidence": [
        {"source_id": "src_1a2b3c4d", "location": "page 3",
         "excerpt": "A short exact quotation copied from that source."}
      ],
      "evidence_status": "supported",
      "owner": null,
      "due_date": null
    }
  ],
  "relationships": [
    {"from_ref": "i2", "to_ref": "i3", "relation": "blocks",
     "evidence": {"source_id": "src_1a2b3c4d", "location": "page 4",
                  "excerpt": "A short exact quotation stating the link."}}
  ],
  "conflicts": [
    {"description": "Source A says X, source B says Y.",
     "evidence": [{"source_id": "src_1a2b3c4d", "location": "page 1",
                   "excerpt": "..."}]}
  ],
  "missing_information": [
    "No source states a launch date."
  ]
}"""
