"""The reconstruction pipeline.

Deterministic order, as specified:

1. load only this project's authorised sources that actually yielded text,
2. chunk them, preserving source and location metadata,
3. ask the model for candidate handover items as structured JSON,
4. validate the reply with Pydantic, discarding anything malformed,
5. verify every source ID and excerpt against the real source rows,
6. lower the evidence status of anything that did not hold up,
7. save the result as a draft for human review.

Steps 4 to 6 happen whatever produced the output, so a model cannot talk its
way past them. When no provider is configured, step 3 is replaced by a
deterministic keyword matcher that is clearly labelled as such everywhere it
appears.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import config
from database.connection import transaction
from database.repositories import (
    EvidenceRepository,
    HandoverItemRepository,
    ProjectRepository,
    ReconstructionRepository,
    RelationshipRepository,
    SourceRepository,
)
from trace_app.ai import offline_preview
from trace_app.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from trace_app.ai.provider import (
    LLMBadResponse,
    LLMError,
    LLMNotConfigured,
    LLMProvider,
    build_provider,
)
from trace_app.ai.schemas import ReconstructionDraft
from trace_app.models import (
    EvidenceStatus,
    ReconstructionStatus,
    ReviewStatus,
)
from trace_app.security.authz import Capability, Principal, require
from trace_app.services.evidence_service import (
    SourceIndex,
    VerificationReport,
    VerifiedItem,
    VerifiedRelationship,
    normalise,
    verify_conflict,
    verify_item,
    verify_relationship,
)
from trace_app.utils.text_chunking import (
    batch_chunks,
    chunk_sources,
    render_batch,
)

logger = logging.getLogger(__name__)

PREVIEW_PROVIDER_LABEL = "deterministic-preview"


class ReconstructionError(RuntimeError):
    """A failure worth showing to the user as-is."""


@dataclass
class ReconstructionOutcome:
    ok: bool
    message: str
    reconstruction_id: str | None = None
    item_count: int = 0
    is_preview: bool = False
    notes: list[str] = field(default_factory=list)


def _merge_drafts(drafts: list[ReconstructionDraft]) -> ReconstructionDraft:
    """Combine per-batch drafts, de-duplicating without losing evidence.

    Two items are the same when their category and normalised text match. Their
    evidence lists are unioned, so a claim supported from two batches keeps
    both references rather than one overwriting the other.
    """
    if len(drafts) == 1:
        return drafts[0]

    # Each batch invents its own refs ("i1", "i2"), so the same handle means
    # different items in different batches. Namespace them before merging, or
    # a relationship from batch two would attach to batch one's item.
    for position, draft in enumerate(drafts):
        prefix = f"b{position}:"
        for item in draft.items:
            if item.ref:
                item.ref = prefix + item.ref
        for relationship in draft.relationships:
            relationship.from_ref = prefix + relationship.from_ref
            relationship.to_ref = prefix + relationship.to_ref

    merged_items: dict[tuple[str, str], Any] = {}
    for draft in drafts:
        for item in draft.items:
            key = (str(item.category), normalise(item.text))
            existing = merged_items.get(key)
            if existing is None:
                merged_items[key] = item.model_copy(deep=True)
                continue
            seen = {
                (ref.source_id, normalise(ref.excerpt)) for ref in existing.evidence
            }
            for ref in item.evidence:
                signature = (ref.source_id, normalise(ref.excerpt))
                if signature not in seen:
                    existing.evidence.append(ref)
                    seen.add(signature)

    conflicts: list[Any] = []
    seen_conflicts: set[str] = set()
    for draft in drafts:
        for conflict in draft.conflicts:
            key = normalise(conflict.description)
            if key not in seen_conflicts:
                seen_conflicts.add(key)
                conflicts.append(conflict)

    missing: list[str] = []
    seen_missing: set[str] = set()
    for draft in drafts:
        for entry in draft.missing_information:
            key = normalise(entry)
            if key not in seen_missing:
                seen_missing.add(key)
                missing.append(entry)

    relationships: list[Any] = []
    for draft in drafts:
        relationships.extend(draft.relationships)

    return ReconstructionDraft(
        items=list(merged_items.values()),
        relationships=relationships,
        conflicts=conflicts,
        missing_information=missing,
    )


class ReconstructionService:
    """The reconstruction pipeline, scoped to one actor."""

    def __init__(self, connection: sqlite3.Connection, principal: Principal) -> None:
        self._connection = connection
        self._principal = principal
        self._sources = SourceRepository(connection)
        self._reconstructions = ReconstructionRepository(connection)
        self._items = HandoverItemRepository(connection)
        self._evidence = EvidenceRepository(connection)
        self._relationships = RelationshipRepository(connection)
        self._projects = ProjectRepository(connection)

    # ------------------------------------------------------------------ read
    def latest(self, project_id: str) -> dict[str, Any] | None:
        return self._reconstructions.latest_for_project(project_id)

    def status_for_project(self, project_id: str) -> str:
        latest = self.latest(project_id)
        return (
            str(latest["status"])
            if latest
            else str(ReconstructionStatus.NOT_STARTED)
        )

    # ------------------------------------------------------------------- run
    def can_run(self) -> bool:
        return self._principal.can(Capability.RUN_RECONSTRUCTION)

    def run(
        self, project: dict[str, Any], provider: LLMProvider | None = None
    ) -> ReconstructionOutcome:
        """Run the pipeline for one project and persist a draft."""
        project_id = str(project["id"])
        require(self._principal, Capability.RUN_RECONSTRUCTION, project_id)
        sources = self._sources.list_usable_for_project(project_id)

        if not sources:
            return ReconstructionOutcome(
                ok=False,
                message=(
                    "This project has no sources with extracted text. Upload at "
                    "least one readable PDF, DOCX, or TXT file first."
                ),
            )

        index = SourceIndex(sources)
        chunks = chunk_sources(sources)
        batches = batch_chunks(chunks)
        catalogue = [(str(s["id"]), str(s["display_name"])) for s in sources]

        settings = config.get_llm_config()
        notes: list[str] = []
        use_preview = False

        if provider is None:
            try:
                provider = build_provider(settings)
            except LLMNotConfigured:
                use_preview = True

        provider_error: str | None = None

        if use_preview:
            draft = offline_preview.build_preview(sources)
            notes.append(
                "No AI provider is configured, so this draft was produced by "
                "deterministic keyword matching. No AI model was called."
            )
            model_provider, model_name = PREVIEW_PROVIDER_LABEL, "rule-based"
        else:
            assert provider is not None
            try:
                draft, batch_notes = self._run_model(
                    provider, batches, catalogue, str(project["name"])
                )
            except LLMError as exc:
                # The provider is unreachable. Rather than leaving the user at a
                # dead end, fall back to the deterministic matcher -- which is
                # labelled as not-from-a-model everywhere it appears, and runs
                # through exactly the same validation and evidence
                # verification. The provider's own error is kept and shown, so
                # nobody can mistake this for a successful model run.
                logger.warning("Falling back to the preview: %s", exc)
                provider_error = str(exc)
                use_preview = True
                draft = offline_preview.build_preview(sources)
                notes.append(
                    f"The AI provider could not be reached ({exc}) This draft "
                    "was produced instead by deterministic keyword matching. "
                    "No AI model was called."
                )
                model_provider, model_name = PREVIEW_PROVIDER_LABEL, "rule-based"
            else:
                notes.extend(batch_notes)
                # Record the model that actually answered, which may be a
                # fallback, so a report never names a model that did not write
                # it.
                model_provider = settings.provider
                model_name = getattr(provider, "model_used", None) or settings.model
                notes.extend(getattr(provider, "notes", []) or [])

        # --- verification: nothing below trusts what produced the draft ---
        report = VerificationReport()
        verified_items = [verify_item(item, index, report) for item in draft.items]
        item_count = len(verified_items)
        verified_items.extend(
            verify_conflict(conflict, index, report) for conflict in draft.conflicts
        )
        verified_relationships = [
            verify_relationship(relationship, index, report)
            for relationship in draft.relationships
        ]
        notes.extend(report.as_notes())

        if len(chunks) > len(batches):
            notes.append(
                f"{len(sources)} source(s) were split into {len(chunks)} chunks "
                f"across {len(batches)} request(s)."
            )

        reconstruction_id = self._persist(
            project_id=project_id,
            draft=draft,
            verified_items=verified_items,
            source_count=len(sources),
            model_provider=model_provider,
            model_name=model_name,
            notes=notes,
            is_preview=use_preview,
            draft_items=draft.items[:item_count],
            relationships=verified_relationships,
            verification={
                "evidence_total": report.evidence_total,
                "evidence_verified": report.evidence_verified,
                "fabricated_source_ids": report.fabricated_source_ids,
                "excerpt_not_found": report.excerpt_not_found,
                "locations_corrected": report.locations_corrected,
                "items_downgraded": report.items_downgraded,
                "relationships_proposed": report.relationships_proposed,
                "relationships_verified": report.relationships_verified,
                "relationships_discarded": report.relationships_discarded,
            },
        )
        self._projects.touch(project_id)

        stored = sum(1 for item in verified_items if item.text)
        if provider_error:
            message = (
                "The AI provider was unavailable, so TRACE produced a clearly "
                "labelled deterministic draft instead. No AI model was called."
            )
        elif stored == 0:
            message = (
                "The reconstruction completed but produced no items the sources "
                "support. Nothing was invented to fill the gap."
            )
        else:
            message = (
                f"Draft ready: {stored} item(s) proposed from {len(sources)} "
                "source(s). Every item needs review before export."
            )

        return ReconstructionOutcome(
            ok=True,
            message=message,
            reconstruction_id=reconstruction_id,
            item_count=stored,
            is_preview=use_preview,
            notes=notes,
        )

    # --------------------------------------------------------------- model
    def _run_model(
        self,
        provider: LLMProvider,
        batches: list[list],
        catalogue: list[tuple[str, str]],
        project_name: str,
    ) -> tuple[ReconstructionDraft, list[str]]:
        """Call the provider once per batch and validate each reply."""
        drafts: list[ReconstructionDraft] = []
        notes: list[str] = []
        failures = 0
        last_error: LLMError | None = None

        for position, batch in enumerate(batches, start=1):
            user_prompt = build_user_prompt(
                rendered_sources=render_batch(batch),
                source_catalogue=catalogue,
                project_name=project_name,
                batch_number=position,
                batch_total=len(batches),
            )
            try:
                payload = provider.generate_json(SYSTEM_PROMPT, user_prompt)
                drafts.append(ReconstructionDraft.model_validate(payload))
            except LLMBadResponse as exc:
                failures += 1
                last_error = exc
                logger.warning("Batch %s returned unusable JSON: %s", position, exc)
                notes.append(
                    f"Part {position} of {len(batches)} returned a reply TRACE "
                    "could not read and was skipped."
                )
            except LLMError as exc:
                failures += 1
                last_error = exc
                logger.warning("Batch %s failed: %s", position, exc)
                notes.append(f"Part {position} of {len(batches)} failed: {exc}")
            except Exception as exc:
                # A Pydantic failure means the reply did not match the schema.
                failures += 1
                last_error = LLMBadResponse(
                    "The model's reply did not match the required structure."
                )
                logger.warning("Batch %s failed validation: %s", position, exc)
                notes.append(
                    f"Part {position} of {len(batches)} did not match the "
                    "required structure and was discarded."
                )

        if not drafts:
            raise last_error or LLMBadResponse(
                "The model produced no usable output."
            )
        if failures:
            notes.append(
                f"{failures} of {len(batches)} part(s) of the source material "
                "could not be processed, so this draft is incomplete."
            )
        return _merge_drafts(drafts), notes

    # ------------------------------------------------------------- persist
    def _persist(
        self,
        project_id: str,
        draft: ReconstructionDraft,
        verified_items: list[VerifiedItem],
        source_count: int,
        model_provider: str | None,
        model_name: str | None,
        notes: list[str],
        is_preview: bool,
        verification: dict[str, int] | None = None,
        draft_items: list[Any] | None = None,
        relationships: list[VerifiedRelationship] | None = None,
    ) -> str:
        """Write the reconstruction, its items, and its verified evidence.

        Only evidence naming a real source in this project is stored; a
        reference to a fabricated ID is dropped here rather than relying on the
        foreign key to catch it.
        """
        payload = {
            "missing_information": list(draft.missing_information),
            "notes": list(notes),
            "is_demo_preview": is_preview,
            # Structured counts so the review screen can show what was checked
            # as figures rather than buried prose.
            "verification": verification,
        }

        reconstruction_id = self._reconstructions.create(
            project_id=project_id,
            status=ReconstructionStatus.DRAFT_READY,
            model_provider=model_provider,
            model_name=model_name,
            source_count=source_count,
            validated_json=json.dumps(payload),
            processing_notes="\n".join(notes) if notes else None,
            is_demo_preview=is_preview,
        )

        # Maps the model's own refs onto TRACE's identifiers, so relationships
        # can be attached. The refs themselves are never stored.
        ref_to_item_id: dict[str, str] = {}
        drafted = list(draft_items or [])

        with transaction(self._connection):
            for position, item in enumerate(verified_items):
                item_id = self._items.create(
                    reconstruction_id=reconstruction_id,
                    category=item.category,
                    text=item.text,
                    evidence_status=item.evidence_status,
                    review_status=ReviewStatus.PROPOSED,
                    owner=item.owner,
                    due_date=item.due_date,
                    reviewer_note=" ".join(item.notes) if item.notes else None,
                    sort_order=position,
                )
                if position < len(drafted) and drafted[position].ref:
                    ref_to_item_id[str(drafted[position].ref)] = item_id

                for evidence in item.storable_evidence:
                    self._evidence.create(
                        handover_item_id=item_id,
                        source_id=evidence.source_id,
                        excerpt=evidence.excerpt,
                        location_label=evidence.location_label
                        or evidence.claimed_location,
                        start_offset=evidence.start_offset,
                        end_offset=evidence.end_offset,
                        support_note=evidence.reason,
                        verified=evidence.verified,
                        source_url=evidence.source_url,
                    )

            for relationship in relationships or []:
                source = ref_to_item_id.get(relationship.from_ref)
                target = ref_to_item_id.get(relationship.to_ref)
                # An endpoint that does not resolve means the model referred
                # to an item it did not produce. Nothing to draw.
                if not source or not target or source == target:
                    continue
                self._relationships.create(
                    reconstruction_id=reconstruction_id,
                    from_item_id=source,
                    to_item_id=target,
                    relation=relationship.relation,
                    source_id=(
                        relationship.evidence.source_id
                        if relationship.evidence.storable
                        else None
                    ),
                    location_label=relationship.evidence.location_label,
                    excerpt=relationship.evidence.excerpt,
                    source_url=relationship.evidence.source_url,
                    verified=relationship.verified,
                    reason=relationship.reason,
                )

        return reconstruction_id

    # ----------------------------------------------------------- summaries
    def evidence_summary(self, reconstruction_id: str) -> dict[str, int]:
        """Counts per evidence status, derived from stored rows."""
        rows = self._connection.execute(
            "SELECT evidence_status, COUNT(*) AS c FROM handover_items"
            " WHERE reconstruction_id = ? GROUP BY evidence_status",
            (reconstruction_id,),
        ).fetchall()
        summary = {str(status): 0 for status in EvidenceStatus}
        for row in rows:
            summary[str(row["evidence_status"])] = int(row["c"])
        return summary

    def verification_counts(self, reconstruction: dict[str, Any]) -> dict[str, int]:
        """Counts of what evidence checking actually did, for display."""
        try:
            payload = json.loads(reconstruction.get("validated_json") or "{}")
        except (TypeError, ValueError):
            return {}
        counts = payload.get("verification")
        return counts if isinstance(counts, dict) else {}

    def relationships(self, reconstruction_id: str) -> list[dict[str, Any]]:
        """Drawable relationships for a reconstruction."""
        require(self._principal, Capability.VIEW_APPROVED)
        return self._relationships.list_verified(reconstruction_id)

    def relationship_counts(self, reconstruction_id: str) -> tuple[int, int]:
        return self._relationships.count_for_reconstruction(reconstruction_id)

    def missing_information(self, reconstruction: dict[str, Any]) -> list[str]:
        try:
            payload = json.loads(reconstruction.get("validated_json") or "{}")
        except (TypeError, ValueError):
            return []
        entries = payload.get("missing_information")
        return [str(entry) for entry in entries] if isinstance(entries, list) else []
