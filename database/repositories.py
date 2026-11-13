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
        " char_count, page_count, content_hash, origin, origin_url"
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
        origin: str = "upload",
        origin_url: str | None = None,
    ) -> dict[str, Any]:
        new_source_id = source_id or new_id("src")
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO sources (id, project_id, display_name, stored_name,"
                " file_type, file_size, uploaded_at, extraction_status,"
                " extraction_error, extraction_notes, extracted_text,"
                " segments_json, char_count, page_count, content_hash,"
                " origin, origin_url)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    origin,
                    origin_url,
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

    def find_extraction_by_hash(
        self, content_hash: str, user_id: str
    ) -> dict[str, Any] | None:
        """A previous successful extraction of these exact bytes.

        Scoped to projects the user is a member of, so reuse never crosses a
        boundary the user could not already see across. Identical bytes give
        identical text, so copying a stored extraction is the same answer the
        parser would produce -- just without doing the work again.
        """
        if not content_hash:
            return None
        row = self._connection.execute(
            "SELECT s.extracted_text, s.segments_json, s.char_count,"
            " s.page_count, s.extraction_status, s.extraction_notes,"
            " s.display_name"
            " FROM sources s"
            " JOIN project_members m ON m.project_id = s.project_id"
            " WHERE s.content_hash = ? AND m.user_id = ?"
            "   AND s.extraction_status IN ('extracted', 'partially_extracted')"
            "   AND s.extracted_text IS NOT NULL"
            " LIMIT 1",
            (content_hash, user_id),
        ).fetchone()
        return _row_to_dict(row)

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

    def find_by_fingerprint(
        self, project_id: str, fingerprint: str
    ) -> dict[str, Any] | None:
        """The most recent successful run computed from identical input."""
        if not fingerprint:
            return None
        row = self._connection.execute(
            "SELECT * FROM reconstructions WHERE project_id = ?"
            " AND input_fingerprint = ? AND status = 'draft_ready'"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (project_id, fingerprint),
        ).fetchone()
        return _row_to_dict(row)

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
        input_fingerprint: str | None = None,
    ) -> str:
        reconstruction_id = new_id("rec")
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO reconstructions (id, project_id, created_at,"
                " model_provider, model_name, status, validated_json,"
                " processing_notes, source_count, is_demo_preview,"
                " input_fingerprint)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    input_fingerprint,
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
        source_url: str | None = None,
    ) -> str:
        evidence_id = new_id("ev")
        self._connection.execute(
            "INSERT INTO evidence (id, handover_item_id, source_id,"
            " location_label, excerpt, start_offset, end_offset, support_note,"
            " verified, source_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                source_url,
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
            " s.file_type AS source_file_type, s.origin AS source_origin,"
            " s.origin_url AS source_origin_url"
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


class UserRepository:
    """Accounts. Password hashes never leave this layer except to be verified."""

    #: Columns safe to hand to the rest of the application.
    _PUBLIC = "id, email, display_name, is_demo, created_at, last_login_at"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        email: str,
        password_hash: str,
        display_name: str | None = None,
        is_demo: bool = False,
    ) -> dict[str, Any]:
        user_id = new_id("usr")
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO users (id, email, display_name, password_hash,"
                " is_demo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    email,
                    display_name,
                    password_hash,
                    1 if is_demo else 0,
                    utc_now(),
                ),
            )
        stored = self.get(user_id)
        if stored is None:  # pragma: no cover - would indicate a failed insert
            raise RuntimeError("User insert succeeded but the row is missing")
        return stored

    def get(self, user_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            f"SELECT {self._PUBLIC} FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return _row_to_dict(row)

    def find_by_email(self, email: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            f"SELECT {self._PUBLIC} FROM users WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        return _row_to_dict(row)

    def password_hash_for(self, email: str) -> tuple[str, str] | None:
        """Return ``(user_id, password_hash)`` for a login attempt."""
        row = self._connection.execute(
            "SELECT id, password_hash FROM users WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        return (row["id"], row["password_hash"]) if row else None

    def record_login(self, user_id: str) -> None:
        with transaction(self._connection):
            self._connection.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (utc_now(), user_id),
            )

    def count(self) -> int:
        return int(
            self._connection.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        )


class MembershipRepository:
    """Who may act on which project, and in what role."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def grant(
        self, project_id: str, user_id: str, role: str, added_by: str | None = None
    ) -> None:
        """Add or change a member's role. Idempotent on (project, user)."""
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO project_members (id, project_id, user_id, role,"
                " added_at, added_by) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (project_id, user_id)"
                " DO UPDATE SET role = excluded.role",
                (
                    new_id("mem"),
                    project_id,
                    user_id,
                    str(role),
                    utc_now(),
                    added_by,
                ),
            )

    def revoke(self, project_id: str, user_id: str) -> None:
        with transaction(self._connection):
            self._connection.execute(
                "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            )

    def role_for(self, project_id: str, user_id: str) -> str | None:
        """The single source of truth for 'may this user touch this project'."""
        row = self._connection.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
        return str(row["role"]) if row else None

    def list_members(self, project_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT m.id, m.role, m.added_at, u.id AS user_id, u.email,"
            " u.display_name FROM project_members m"
            " JOIN users u ON u.id = m.user_id"
            " WHERE m.project_id = ?"
            " ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'manager' THEN 1"
            "   WHEN 'developer' THEN 2 ELSE 3 END, u.email",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def projects_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Projects this user is a member of, with their role on each.

        This is the only listing the dashboard uses, so a project the user has
        no membership on is not merely hidden -- it is never fetched.
        """
        rows = self._connection.execute(
            """
            SELECT
                p.*,
                m.role AS member_role,
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
            FROM project_members m
            JOIN projects p ON p.id = m.project_id
            WHERE m.user_id = ?
            ORDER BY p.created_at DESC, p.rowid DESC
            """,
            (user_id,),
        ).fetchall()
        projects: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["latest_reconstruction_status"] = (
                record.get("latest_reconstruction_status") or "not_started"
            )
            projects.append(record)
        return projects

    def count_for_project(self, project_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) AS c FROM project_members WHERE project_id = ?",
                (project_id,),
            ).fetchone()["c"]
        )



class RelationshipRepository:
    """Evidence-backed links between handover items.

    Only verified relationships are returned for display: an unsupported link
    is kept for the audit trail but never drawn, because a line between two
    boxes asserts a claim the sources did not make.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        reconstruction_id: str,
        from_item_id: str,
        to_item_id: str,
        relation: str,
        source_id: str | None = None,
        location_label: str | None = None,
        excerpt: str | None = None,
        source_url: str | None = None,
        verified: bool = False,
        reason: str | None = None,
    ) -> str:
        relationship_id = new_id("rel")
        self._connection.execute(
            "INSERT INTO item_relationships (id, reconstruction_id,"
            " from_item_id, to_item_id, relation, source_id, location_label,"
            " excerpt, source_url, verified, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                relationship_id,
                reconstruction_id,
                from_item_id,
                to_item_id,
                str(relation),
                source_id,
                location_label,
                excerpt,
                source_url,
                1 if verified else 0,
                reason,
                utc_now(),
            ),
        )
        return relationship_id

    def list_verified(self, reconstruction_id: str) -> list[dict[str, Any]]:
        """Drawable relationships, joined to both endpoints and the source."""
        rows = self._connection.execute(
            """
            SELECT r.*,
                   a.text     AS from_text,  a.category AS from_category,
                   a.review_status AS from_review_status,
                   b.text     AS to_text,    b.category AS to_category,
                   b.review_status AS to_review_status,
                   s.display_name AS source_display_name
            FROM item_relationships r
            JOIN handover_items a ON a.id = r.from_item_id
            JOIN handover_items b ON b.id = r.to_item_id
            LEFT JOIN sources s ON s.id = r.source_id
            WHERE r.reconstruction_id = ? AND r.verified = 1
            ORDER BY r.rowid ASC
            """,
            (reconstruction_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_for_reconstruction(self, reconstruction_id: str) -> tuple[int, int]:
        """``(verified, total)`` so the UI can say what was discarded."""
        row = self._connection.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END) AS ok"
            " FROM item_relationships WHERE reconstruction_id = ?",
            (reconstruction_id,),
        ).fetchone()
        return int(row["ok"] or 0), int(row["total"] or 0)
