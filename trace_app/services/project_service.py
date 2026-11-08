"""Project workspace creation and retrieval."""

from __future__ import annotations

import sqlite3
from typing import Any

from database.repositories import ProjectRepository

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000
MAX_TEAM_LABEL_LENGTH = 120


class ValidationError(ValueError):
    """Raised when user input fails a business rule. Safe to show to a user."""


def _clean(value: str | None, limit: int) -> str | None:
    """Trim surrounding whitespace, collapse to None when empty, enforce length."""
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[:limit]


class ProjectService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._projects = ProjectRepository(connection)

    def create_project(
        self,
        name: str | None,
        description: str | None = None,
        team_label: str | None = None,
        is_demo: bool = False,
    ) -> dict[str, Any]:
        """Validate input and persist a new project workspace.

        Raises ``ValidationError`` for a blank or whitespace-only name.
        """
        clean_name = _clean(name, MAX_NAME_LENGTH)
        if not clean_name:
            raise ValidationError("Project name is required.")

        return self._projects.create(
            name=clean_name,
            description=_clean(description, MAX_DESCRIPTION_LENGTH),
            team_label=_clean(team_label, MAX_TEAM_LABEL_LENGTH),
            is_demo=is_demo,
        )

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        if not project_id:
            return None
        return self._projects.get(project_id)

    def require_project(self, project_id: str) -> dict[str, Any]:
        """Fetch a project or raise -- used by screens that need one to exist."""
        project = self.get_project(project_id)
        if project is None:
            raise ValidationError("That project could not be found.")
        return project

    def list_projects(self) -> list[dict[str, Any]]:
        return self._projects.list_all()

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        clean_name = _clean(name, MAX_NAME_LENGTH)
        if not clean_name:
            return None
        return self._projects.get_by_name(clean_name)
