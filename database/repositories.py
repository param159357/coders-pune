"""Data access for TRACE.

Every query is parameterised. Reads that cross the project boundary take the
owning ``project_id`` explicitly so a caller cannot accidentally reach another
project's rows (spec section 12).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from database.connection import transaction
from trace_app.models import ReconstructionStatus, new_id, utc_now


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class ProjectRepository:
    """Create and read project workspaces."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        name: str,
        description: str | None = None,
        team_label: str | None = None,
        is_demo: bool = False,
    ) -> dict[str, Any]:
        """Insert a project and return the stored row.

        Callers are expected to have validated and trimmed ``name`` via
        ``ProjectService``; this method does not re-validate business rules.
        """
        project_id = new_id("proj")
        now = utc_now()
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO projects"
                " (id, name, description, team_label, is_demo, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    name,
                    description,
                    team_label,
                    1 if is_demo else 0,
                    now,
                    now,
                ),
            )
        stored = self.get(project_id)
        if stored is None:  # pragma: no cover - would indicate a failed insert
            raise RuntimeError("Project insert succeeded but the row is missing")
        return stored

    def get(self, project_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return _row_to_dict(row)

    def get_by_name(self, name: str) -> dict[str, Any] | None:
        """Case-insensitive name lookup, used to avoid reseeding the demo."""
        row = self._connection.execute(
            "SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return _row_to_dict(row)

    def list_all(self) -> list[dict[str, Any]]:
        """Projects newest first, each with its real source and review counts.

        Counts are computed by the database rather than invented in the UI, so
        the dashboard never shows a metric that is not backed by a row.
        """
        rows = self._connection.execute(
            """
            SELECT
                p.*,
                (SELECT COUNT(*) FROM sources s
                  WHERE s.project_id = p.id) AS source_count,
                (SELECT COUNT(*) FROM sources s
                  WHERE s.project_id = p.id
                    AND s.extraction_status IN ('extracted', 'partially_extracted')
                ) AS extracted_count,
                (SELECT r.status FROM reconstructions r
                  WHERE r.project_id = p.id
                  ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1
                ) AS latest_reconstruction_status
            FROM projects p
            ORDER BY p.created_at DESC, p.rowid DESC
            """
        ).fetchall()

        projects: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["latest_reconstruction_status"] = (
                record.get("latest_reconstruction_status")
                or ReconstructionStatus.NOT_STARTED.value
            )
            projects.append(record)
        return projects

    def touch(self, project_id: str) -> None:
        """Bump ``updated_at`` after a change inside the project."""
        with transaction(self._connection):
            self._connection.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (utc_now(), project_id),
            )


class SourceRepository:
    """Create and read uploaded source documents.

    Every read that returns source rows is scoped by ``project_id``, so a
    caller cannot reach another project's sources even with a valid source ID.
    """

    #: Columns returned when the (potentially large) extracted text is not
    #: needed, which keeps list screens cheap.
    _SUMMARY_COLUMNS = (
        "id, project_id, display_name, stored_name, file_type, file_size,"
        " uploaded_at, extraction_status, extraction_error, extraction_notes,"
        " char_count, page_count, content_hash"
    )

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        project_id: str,
        display_name: str,
        stored_name: str,
        file_type: str,
        file_size: int,
        extraction_status: str,
        extracted_text: str | None = None,
        extraction_error: str | None = None,
        extraction_notes: str | None = None,
        segments_json: str | None = None,
        char_count: int = 0,
        page_count: int | None = None,
        content_hash: str | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        new_source_id = source_id or new_id("src")
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO sources (id, project_id, display_name, stored_name,"
                " file_type, file_size, uploaded_at, extraction_status,"
                " extraction_error, extraction_notes, extracted_text,"
                " segments_json, char_count, page_count, content_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_source_id,
                    project_id,
                    display_name,
                    stored_name,
                    file_type,
                    file_size,
                    utc_now(),
                    str(extraction_status),
                    extraction_error,
                    extraction_notes,
                    extracted_text,
                    segments_json,
                    char_count,
                    page_count,
                    content_hash,
                ),
            )
        stored = self.get(new_source_id, project_id)
        if stored is None:  # pragma: no cover - would indicate a failed insert
            raise RuntimeError("Source insert succeeded but the row is missing")
        return stored

    def get(self, source_id: str, project_id: str) -> dict[str, Any] | None:
        """Fetch one source, enforcing that it belongs to ``project_id``."""
        row = self._connection.execute(
            "SELECT * FROM sources WHERE id = ? AND project_id = ?",
            (source_id, project_id),
        ).fetchone()
        return _row_to_dict(row)

    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        """Source summaries for a project, oldest first, without the full text."""
        rows = self._connection.execute(
            f"SELECT {self._SUMMARY_COLUMNS} FROM sources"
            " WHERE project_id = ? ORDER BY uploaded_at ASC, rowid ASC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_usable_for_project(self, project_id: str) -> list[dict[str, Any]]:
        """Full rows for sources that actually yielded text.

        This is the only set the reconstruction pipeline is allowed to read.
        """
        rows = self._connection.execute(
            "SELECT * FROM sources WHERE project_id = ?"
            " AND extraction_status IN ('extracted', 'partially_extracted')"
            " AND extracted_text IS NOT NULL AND TRIM(extracted_text) <> ''"
            " ORDER BY uploaded_at ASC, rowid ASC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def find_by_hash(
        self, project_id: str, content_hash: str
    ) -> dict[str, Any] | None:
        """Detect a byte-identical re-upload within the same project."""
        if not content_hash:
            return None
        row = self._connection.execute(
            f"SELECT {self._SUMMARY_COLUMNS} FROM sources"
            " WHERE project_id = ? AND content_hash = ? LIMIT 1",
            (project_id, content_hash),
        ).fetchone()
        return _row_to_dict(row)

    def count_for_project(self, project_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS c FROM sources WHERE project_id = ?", (project_id,)
        ).fetchone()
        return int(row["c"])

    def delete(self, source_id: str, project_id: str) -> None:
        """Remove a source. Refused by the database if evidence still cites it."""
        with transaction(self._connection):
            self._connection.execute(
                "DELETE FROM sources WHERE id = ? AND project_id = ?",
                (source_id, project_id),
            )


class ReconstructionRepository:
    """Draft reconstructions produced for a project."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        project_id: str,
        status: str,
        model_provider: str | None,
        model_name: str | None,
        source_count: int,
        validated_json: str | None = None,
        processing_notes: str | None = None,
        is_demo_preview: bool = False,
    ) -> str:
        reconstruction_id = new_id("rec")
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO reconstructions (id, project_id, created_at,"
                " model_provider, model_name, status, validated_json,"
                " processing_notes, source_count, is_demo_preview)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reconstruction_id,
                    project_id,
                    utc_now(),
                    model_provider,
                    model_name,
                    str(status),
                    validated_json,
                    processing_notes,
                    source_count,
                    1 if is_demo_preview else 0,
                ),
            )
        return reconstruction_id

    def get(self, reconstruction_id: str, project_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM reconstructions WHERE id = ? AND project_id = ?",
            (reconstruction_id, project_id),
        ).fetchone()
        return _row_to_dict(row)

    def latest_for_project(self, project_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM reconstructions WHERE project_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        return _row_to_dict(row)

    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT id, created_at, status, model_provider, model_name,"
            " source_count, is_demo_preview FROM reconstructions"
            " WHERE project_id = ? ORDER BY created_at DESC, rowid DESC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]


class HandoverItemRepository:
    """Proposed handover items and their review state."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        reconstruction_id: str,
        category: str,
        text: str,
        evidence_status: str,
        review_status: str,
        owner: str | None = None,
        due_date: str | None = None,
        reviewer_note: str | None = None,
        sort_order: int = 0,
    ) -> str:
        item_id = new_id("item")
        now = utc_now()
        self._connection.execute(
            "INSERT INTO handover_items (id, reconstruction_id, category, text,"
            " review_status, evidence_status, owner, due_date, reviewer_note,"
            " sort_order, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                reconstruction_id,
                str(category),
                text,
                str(review_status),
                str(evidence_status),
                owner,
                due_date,
                reviewer_note,
                sort_order,
                now,
                now,
            ),
        )
        return item_id

    def list_for_reconstruction(self, reconstruction_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM handover_items WHERE reconstruction_id = ?"
            " ORDER BY sort_order ASC, rowid ASC",
            (reconstruction_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, item_id: str, reconstruction_id: str) -> dict[str, Any] | None:
        """Fetch an item, scoped to the reconstruction that owns it."""
        row = self._connection.execute(
            "SELECT * FROM handover_items WHERE id = ? AND reconstruction_id = ?",
            (item_id, reconstruction_id),
        ).fetchone()
        return _row_to_dict(row)

    def update_fields(self, item_id: str, **fields: Any) -> None:
        """Update a whitelisted set of columns on one item."""
        allowed = {"text", "review_status", "owner", "due_date", "reviewer_note"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with transaction(self._connection):
            self._connection.execute(
                f"UPDATE handover_items SET {assignments}, updated_at = ?"
                " WHERE id = ?",
                (*updates.values(), utc_now(), item_id),
            )

    def count_by_review_status(self, reconstruction_id: str) -> dict[str, int]:
        rows = self._connection.execute(
            "SELECT review_status, COUNT(*) AS c FROM handover_items"
            " WHERE reconstruction_id = ? GROUP BY review_status",
            (reconstruction_id,),
        ).fetchall()
        return {row["review_status"]: int(row["c"]) for row in rows}


class EvidenceRepository:
    """Verified evidence rows attached to handover items."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        handover_item_id: str,
        source_id: str,
        excerpt: str,
        location_label: str | None = None,
        start_offset: int | None = None,
        end_offset: int | None = None,
        support_note: str | None = None,
        verified: bool = False,
    ) -> str:
        evidence_id = new_id("ev")
        self._connection.execute(
            "INSERT INTO evidence (id, handover_item_id, source_id,"
            " location_label, excerpt, start_offset, end_offset, support_note,"
            " verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                evidence_id,
                handover_item_id,
                source_id,
                location_label,
                excerpt,
                start_offset,
                end_offset,
                support_note,
                1 if verified else 0,
            ),
        )
        return evidence_id

    def list_for_items(self, item_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Evidence for many items at once, joined to its source's name."""
        if not item_ids:
            return {}
        placeholders = ", ".join("?" for _ in item_ids)
        rows = self._connection.execute(
            "SELECT e.*, s.display_name AS source_display_name,"
            " s.file_type AS source_file_type"
            " FROM evidence e JOIN sources s ON s.id = e.source_id"
            f" WHERE e.handover_item_id IN ({placeholders})"
            " ORDER BY e.rowid ASC",
            item_ids,
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["handover_item_id"], []).append(dict(row))
        return grouped


class ReviewEventRepository:
    """Append-only audit trail of reviewer actions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record(
        self,
        handover_item_id: str,
        action: str,
        previous_value: str | None = None,
        new_value: str | None = None,
        note: str | None = None,
    ) -> str:
        event_id = new_id("evt")
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO review_events (id, handover_item_id, action,"
                " previous_value, new_value, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    handover_item_id,
                    str(action),
                    previous_value,
                    new_value,
                    note,
                    utc_now(),
                ),
            )
        return event_id

    def list_for_item(self, handover_item_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM review_events WHERE handover_item_id = ?"
            " ORDER BY created_at ASC, rowid ASC",
            (handover_item_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_for_reconstruction(self, reconstruction_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS c FROM review_events re"
            " JOIN handover_items hi ON hi.id = re.handover_item_id"
            " WHERE hi.reconstruction_id = ?",
            (reconstruction_id,),
        ).fetchone()
        return int(row["c"])
