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
