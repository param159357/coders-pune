"""Human review of proposed handover items.

AI proposes; a person decides. Nothing here approves anything on its own, and
there is deliberately no "approve everything" operation — a reviewer who has
not looked at an item should not be able to accept it by accident.

Every change is written to ``review_events`` with its previous and new value,
so the handover carries a record of who changed what, not just a final state.
Each mutation is scoped to the reconstruction that owns the item, so an item
ID alone cannot reach across projects.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from database.repositories import (
    EvidenceRepository,
    HandoverItemRepository,
    ReviewEventRepository,
)
from trace_app.models import (
    CATEGORY_ORDER,
    EvidenceStatus,
    ReviewAction,
    ReviewStatus,
    category_label,
)
from trace_app.security.authz import Capability, Principal, require

MAX_ITEM_TEXT = 800
MAX_NOTE = 1000


class ReviewError(ValueError):
    """A rejected review action. The message is safe to show to a user."""


@dataclass
class ReviewProgress:
    proposed: int = 0
    approved: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        return self.proposed + self.approved + self.rejected

    @property
    def reviewed(self) -> int:
        return self.approved + self.rejected

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.proposed == 0

    @property
    def fraction(self) -> float:
        return (self.reviewed / self.total) if self.total else 0.0


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    trimmed = " ".join(str(value).split())
    return trimmed[:limit] if trimmed else None


class ReviewService:
    """Handover items, scoped to one actor.

    A role without VIEW_ALL_ITEMS does not merely have items hidden from its
    screen: :meth:`list_items` filters to approved items before returning, so
    an unapproved item is never handed out at all.
    """

    def __init__(self, connection: sqlite3.Connection, principal: Principal) -> None:
        self._connection = connection
        self._principal = principal
        self._items = HandoverItemRepository(connection)
        self._evidence = EvidenceRepository(connection)
        self._events = ReviewEventRepository(connection)

    @property
    def principal(self) -> Principal:
        return self._principal

    def can_review(self) -> bool:
        return self._principal.can(Capability.REVIEW_ITEMS)

    def sees_only_approved(self) -> bool:
        return not self._principal.can(Capability.VIEW_ALL_ITEMS)

    # ------------------------------------------------------------------ read
    def _require_item(self, item_id: str, reconstruction_id: str) -> dict[str, Any]:
        """Fetch an item for modification, checking the role first."""
        require(self._principal, Capability.REVIEW_ITEMS)
        item = self._items.get(item_id, reconstruction_id)
        if item is None:
            raise ReviewError("That handover item could not be found.")
        return item

    def list_items(self, reconstruction_id: str) -> list[dict[str, Any]]:
        """Items for a reconstruction, each with its evidence attached.

        Narrowed to approved items for a role that cannot see the rest. The
        filtering happens here, not in the interface, so there is no route by
        which a viewer receives an item still under review.
        """
        require(self._principal, Capability.VIEW_APPROVED)
        items = self._items.list_for_reconstruction(reconstruction_id)

        if not self._principal.can(Capability.VIEW_ALL_ITEMS):
            items = [
                item
                for item in items
                if str(item["review_status"]) == ReviewStatus.APPROVED
            ]

        if self._principal.can(Capability.VIEW_EVIDENCE):
            evidence = self._evidence.list_for_items([item["id"] for item in items])
        else:
            evidence = {}
        for item in items:
            item["evidence"] = evidence.get(item["id"], [])
        return items

    def grouped_items(
        self, reconstruction_id: str
    ) -> list[tuple[str, str, list[dict[str, Any]]]]:
        """Items grouped into the specification's category order.

        Returns ``(category, label, items)`` for categories that have content.
        An empty category is omitted rather than shown as a reassuring blank.
        """
        items = self.list_items(reconstruction_id)
        by_category: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_category.setdefault(str(item["category"]), []).append(item)

        ordered: list[tuple[str, str, list[dict[str, Any]]]] = []
        for category in CATEGORY_ORDER:
            if category in by_category:
                ordered.append((category, category_label(category), by_category.pop(category)))
        # Anything unexpected still gets shown rather than silently dropped.
        for category, group in by_category.items():
            ordered.append((category, category_label(category), group))
        return ordered

    def progress(self, reconstruction_id: str) -> ReviewProgress:
        """Review counts, limited to what this role is allowed to know.

        A role that cannot see unapproved items is not told how many there
        are either -- reporting "22 awaiting a decision" to someone who may
        not see those 22 leaks their existence and offers filters that can
        only ever come back empty.
        """
        require(self._principal, Capability.VIEW_APPROVED)
        counts = self._items.count_by_review_status(reconstruction_id)
        approved = counts.get(str(ReviewStatus.APPROVED), 0)

        if not self._principal.can(Capability.VIEW_ALL_ITEMS):
            return ReviewProgress(proposed=0, approved=approved, rejected=0)

        return ReviewProgress(
            proposed=counts.get(str(ReviewStatus.PROPOSED), 0),
            approved=approved,
            rejected=counts.get(str(ReviewStatus.REJECTED), 0),
        )

    def history(self, item_id: str) -> list[dict[str, Any]]:
        return self._events.list_for_item(item_id)

    def approved_items(self, reconstruction_id: str) -> list[dict[str, Any]]:
        """Approved items only, in category order -- the basis of the export."""
        return [
            item
            for item in self.list_items(reconstruction_id)
            if item["review_status"] == ReviewStatus.APPROVED
        ]

    # --------------------------------------------------------------- mutate
    def edit_text(
        self, item_id: str, reconstruction_id: str, new_text: str | None
    ) -> dict[str, Any]:
        """Replace an item's statement, keeping the previous value on record."""
        item = self._require_item(item_id, reconstruction_id)
        cleaned = _clean(new_text, MAX_ITEM_TEXT)
        if not cleaned:
            raise ReviewError("An item cannot be left empty.")
        if cleaned == item["text"]:
            return item

        self._items.update_fields(item_id, text=cleaned)
        self._events.record(
            handover_item_id=item_id,
            action=ReviewAction.EDITED,
            previous_value=item["text"],
            new_value=cleaned,
        )
        return self._require_item(item_id, reconstruction_id)

    def set_status(
        self,
        item_id: str,
        reconstruction_id: str,
        status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Approve, reject, or reopen a single item."""
        if status not in {
            str(ReviewStatus.APPROVED),
            str(ReviewStatus.REJECTED),
            str(ReviewStatus.PROPOSED),
        }:
            raise ReviewError("That is not a valid review status.")

        item = self._require_item(item_id, reconstruction_id)
        previous = str(item["review_status"])
        if previous == status and note is None:
            return item

        cleaned_note = _clean(note, MAX_NOTE)
        updates: dict[str, Any] = {"review_status": status}
        if cleaned_note is not None:
            updates["reviewer_note"] = cleaned_note
        self._items.update_fields(item_id, **updates)

        if status == ReviewStatus.APPROVED:
            action = ReviewAction.APPROVED
        elif status == ReviewStatus.REJECTED:
            action = ReviewAction.REJECTED
        else:
            action = ReviewAction.REOPENED

        self._events.record(
            handover_item_id=item_id,
            action=action,
            previous_value=previous,
            new_value=status,
            note=cleaned_note,
        )
        return self._require_item(item_id, reconstruction_id)

    def approve(
        self, item_id: str, reconstruction_id: str, note: str | None = None
    ) -> dict[str, Any]:
        return self.set_status(item_id, reconstruction_id, ReviewStatus.APPROVED, note)

    def reject(
        self, item_id: str, reconstruction_id: str, note: str | None = None
    ) -> dict[str, Any]:
        return self.set_status(item_id, reconstruction_id, ReviewStatus.REJECTED, note)

    def reopen(
        self, item_id: str, reconstruction_id: str, note: str | None = None
    ) -> dict[str, Any]:
        return self.set_status(item_id, reconstruction_id, ReviewStatus.PROPOSED, note)

    def set_note(
        self, item_id: str, reconstruction_id: str, note: str | None
    ) -> dict[str, Any]:
        """Attach or clear a reviewer note without changing the review status."""
        item = self._require_item(item_id, reconstruction_id)
        cleaned = _clean(note, MAX_NOTE)
        if cleaned == item["reviewer_note"]:
            return item

        self._items.update_fields(item_id, reviewer_note=cleaned)
        self._events.record(
            handover_item_id=item_id,
            action=ReviewAction.NOTE_ADDED,
            previous_value=item["reviewer_note"],
            new_value=cleaned,
            note=cleaned,
        )
        return self._require_item(item_id, reconstruction_id)


def needs_attention(item: dict[str, Any]) -> bool:
    """Whether an item's evidence warrants a closer look before approval."""
    return str(item.get("evidence_status")) in {
        str(EvidenceStatus.UNSUPPORTED),
        str(EvidenceStatus.CONFLICTING),
        str(EvidenceStatus.NEEDS_REVIEW),
        str(EvidenceStatus.PARTIALLY_SUPPORTED),
    }
