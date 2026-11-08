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
