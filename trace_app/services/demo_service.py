"""One-click loading of the synthetic CampusConnect demonstration project.

The demo is loaded on request, never silently on startup, so a judge always
knows where the data came from. It is seeded through the ordinary upload path
rather than by writing rows directly, so what they see is the real extraction
pipeline working on real files, not a staged result.

If the demo project already exists it is reused rather than duplicated, and
nothing a user created is ever removed to make room for it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config
from trace_app.security.authz import Principal
from trace_app.services.auth_service import AuthService
from trace_app.services.document_service import DocumentService
from trace_app.services.project_service import ProjectService

DEMO_PROJECT_NAME = "CampusConnect"
DEMO_DESCRIPTION = (
    "Synthetic demonstration project: a fictional campus event and "
    "student-services portal. This data describes no real institution."
)
DEMO_TEAM = "Synthetic demo data"

DEMO_DIR = config.PROJECT_ROOT / "sample_data" / "campusconnect"

#: Loaded in this order so the brief is the first source, as a reader expects.
DEMO_FILES = (
    "project_brief.txt",
    "meeting_notes.txt",
    "development_status.txt",
    "known_issues.txt",
)


@dataclass
class DemoResult:
    ok: bool
    message: str
    project: dict[str, Any] | None = None
    created: bool = False
    files_added: int = 0


#: Demo accounts created alongside the demo project, so the role-aware views
#: can be seen working rather than described. Their password is shown in the
#: interface on purpose: these exist only to demonstrate access control on
#: synthetic data, and they are flagged is_demo in the database.
DEMO_PASSWORD = "tracedemo2026"
DEMO_ACCOUNTS: tuple[tuple[str, str, str], ...] = (
    ("manager@campusconnect.demo", "Priya (Project manager)", "manager"),
    ("developer@campusconnect.demo", "Sam (Incoming developer)", "developer"),
    ("viewer@campusconnect.demo", "Alex (Viewer)", "viewer"),
)


class DemoService:
    """Loads the synthetic demo on behalf of a signed-in user."""

    def __init__(self, connection: sqlite3.Connection, user: dict[str, Any]) -> None:
        self._connection = connection
        self._user = user
        self._auth = AuthService(connection)
        self._projects = ProjectService(connection)
        # The loader acts as the project's owner: it is creating the project.
        self._owner = Principal(
            user_id=str(user["id"]),
            email=str(user["email"]),
            role="owner",
        )
        self._documents = DocumentService(connection, self._owner)

    def existing_demo(self) -> dict[str, Any] | None:
        """The caller's own demo project, if they already loaded one."""
        for project in self._auth.projects_for(self._user):
            if (
                str(project.get("name", "")).lower() == DEMO_PROJECT_NAME.lower()
                and project.get("is_demo")
            ):
                return project
        return None

    def available(self) -> bool:
        """Whether the demo source files are present in this deployment."""
        return DEMO_DIR.is_dir() and any(
            (DEMO_DIR / name).is_file() for name in DEMO_FILES
        )

    def _seed_demo_members(self, project_id: str) -> None:
        """Create the three demo accounts and grant them their roles.

        These let a visitor sign in as each role and see the access checks
        actually refuse things, instead of taking the claim on trust.
        """
        for email, name, role in DEMO_ACCOUNTS:
            account = self._auth.find_by_email(email)
            if account is None:
                try:
                    account = self._auth.register(
                        email, DEMO_PASSWORD, name, is_demo=True
                    )
                except Exception:  # pragma: no cover - lost a race; reuse it
                    account = self._auth.find_by_email(email)
            if account is not None:
                try:
                    self._auth.grant_role(self._owner, project_id, email, role)
                except Exception:  # pragma: no cover - already granted
                    pass

    def load(self) -> DemoResult:
        """Create the demo project and upload its sources. Idempotent."""
        if not self.available():
            return DemoResult(
                ok=False,
                message=(
                    "The demo source files are not present in this deployment, "
                    "so the demo project cannot be loaded."
                ),
            )

        project = self.existing_demo()
        created = False
        if project is None:
            project = self._projects.create_project(
                name=DEMO_PROJECT_NAME,
                description=DEMO_DESCRIPTION,
                team_label=DEMO_TEAM,
                is_demo=True,
            )
            self._auth.register_owner(project["id"], str(self._user["id"]))
            created = True
        # Rebuild the principal now that the project exists, so uploads below
        # are checked against a real membership rather than an assumed role.
        self._owner = self._auth.principal_for(self._user, project["id"])
        self._documents = DocumentService(self._connection, self._owner)
        self._seed_demo_members(project["id"])

        added = 0
        skipped = 0
        for name in DEMO_FILES:
            path = Path(DEMO_DIR / name)
            if not path.is_file():
                continue
            # Byte-identical files are refused by the upload path, which makes
            # re-loading the demo harmless rather than duplicating its sources.
            outcome = self._documents.upload(
                project["id"], name, path.read_bytes()
            )
            if outcome.accepted:
                added += 1
            else:
                skipped += 1

        if created:
            message = (
                f"Loaded the synthetic CampusConnect demo with {added} source "
                "file(s). Run a reconstruction to see the workflow."
            )
        elif added:
            message = f"Added {added} missing demo source file(s) to CampusConnect."
        else:
            message = (
                "The CampusConnect demo is already loaded, with all "
                f"{skipped} of its source files present."
            )

        return DemoResult(
            ok=True,
            message=message,
            project=project,
            created=created,
            files_added=added,
        )
