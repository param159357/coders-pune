"""Roles, capabilities, and the permission check every service calls.

The rule this module exists to enforce: a user without permission cannot
retrieve the underlying content through *any* route. Hiding a button is not
access control, so nothing here is about the interface. Services take a
:class:`Principal` and call :func:`require` before they read or write, and the
tests assert that a forbidden call raises rather than returning data.

A principal is always scoped to one project. Roles are per-project
memberships, so the same person can be an owner of one project and a viewer of
another.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AccessDenied(PermissionError):
    """A refused operation. The message is written to be shown to a user."""


class Role(StrEnum):
    """A member's role on one project."""

    OWNER = "owner"
    MANAGER = "manager"
    DEVELOPER = "developer"
    VIEWER = "viewer"


class Capability(StrEnum):
    """One thing a principal may be allowed to do."""

    VIEW_APPROVED = "view_approved"
    VIEW_ALL_ITEMS = "view_all_items"
    VIEW_EVIDENCE = "view_evidence"
    VIEW_SOURCES = "view_sources"
    UPLOAD_SOURCES = "upload_sources"
    CONNECT_INTEGRATIONS = "connect_integrations"
    RUN_RECONSTRUCTION = "run_reconstruction"
    REVIEW_ITEMS = "review_items"
    EXPORT = "export"
    MANAGE_MEMBERS = "manage_members"
    DELETE_PROJECT = "delete_project"


ROLE_LABELS: dict[str, str] = {
    Role.OWNER: "Owner",
    Role.MANAGER: "Project manager",
    Role.DEVELOPER: "Incoming developer",
    Role.VIEWER: "Viewer",
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    Role.OWNER: (
        "Full control, including adding and removing members and deleting the "
        "project."
    ),
    Role.MANAGER: (
        "Adds sources, runs reconstruction, and approves or rejects items. "
        "Cannot change who has access."
    ),
    Role.DEVELOPER: (
        "Receives the handover. Reads every item and the source documents "
        "behind them, and can export, but cannot change anything."
    ),
    Role.VIEWER: (
        "Reads the approved handover and the evidence quoted in it. Cannot "
        "open the source documents, and cannot see items still under review."
    ),
}

#: The whole access-control policy, in one readable place.
#:
#: VIEW_EVIDENCE is deliberately separate from VIEW_SOURCES. A viewer can see
#: the short excerpt quoted in support of an approved claim -- that is what
#: makes the handover verifiable, and it is the point of the product -- but
#: cannot browse the full source documents those excerpts came from.
ROLE_CAPABILITIES: dict[str, frozenset[Capability]] = {
    Role.OWNER: frozenset(Capability),
    Role.MANAGER: frozenset(
        {
            Capability.VIEW_APPROVED,
            Capability.VIEW_ALL_ITEMS,
            Capability.VIEW_EVIDENCE,
            Capability.VIEW_SOURCES,
            Capability.UPLOAD_SOURCES,
            Capability.CONNECT_INTEGRATIONS,
            Capability.RUN_RECONSTRUCTION,
            Capability.REVIEW_ITEMS,
            Capability.EXPORT,
        }
    ),
    Role.DEVELOPER: frozenset(
        {
            Capability.VIEW_APPROVED,
            Capability.VIEW_ALL_ITEMS,
            Capability.VIEW_EVIDENCE,
            Capability.VIEW_SOURCES,
            Capability.EXPORT,
        }
    ),
    Role.VIEWER: frozenset(
        {
            Capability.VIEW_APPROVED,
            Capability.VIEW_EVIDENCE,
            Capability.EXPORT,
        }
    ),
}


@dataclass(frozen=True)
class Principal:
    """Who is acting, on which project, with what role."""

    user_id: str
    email: str
    role: str
    project_id: str | None = None
    #: True only for internal operations that run before any user exists:
    #: schema setup, demo seeding, and migrations. Never built from a request.
    is_system: bool = False

    @classmethod
    def system(cls, project_id: str | None = None) -> "Principal":
        """An internal actor. Used by seeding and migrations, never by the UI."""
        return cls(
            user_id="system",
            email="system@trace.local",
            role=str(Role.OWNER),
            project_id=project_id,
            is_system=True,
        )

    @property
    def capabilities(self) -> frozenset[Capability]:
        if self.is_system:
            return frozenset(Capability)
        return ROLE_CAPABILITIES.get(self.role, frozenset())

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, str(self.role).title())

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def for_project(self, project_id: str, role: str) -> "Principal":
        """This user's principal on a different project."""
        return Principal(
            user_id=self.user_id,
            email=self.email,
            role=role,
            project_id=project_id,
            is_system=self.is_system,
        )


#: Messages written for the person who hit the wall, not for a log.
_DENIAL_MESSAGES: dict[Capability, str] = {
    Capability.VIEW_ALL_ITEMS: (
        "Your role can only see items that have been approved."
    ),
    Capability.VIEW_SOURCES: (
        "Your role cannot open the source documents behind this handover."
    ),
    Capability.UPLOAD_SOURCES: "Your role cannot add sources to this project.",
    Capability.CONNECT_INTEGRATIONS: (
        "Your role cannot connect external repositories to this project."
    ),
    Capability.RUN_RECONSTRUCTION: (
        "Your role cannot run a reconstruction on this project."
    ),
    Capability.REVIEW_ITEMS: (
        "Your role is read-only: you cannot edit, approve or reject items."
    ),
    Capability.MANAGE_MEMBERS: "Only the project owner can manage access.",
    Capability.DELETE_PROJECT: "Only the project owner can delete this project.",
}


def require(
    principal: Principal | None,
    capability: Capability,
    project_id: str | None = None,
) -> None:
    """Raise ``AccessDenied`` unless ``principal`` holds ``capability``.

    Also checks the principal is acting on the project it was issued for, so a
    principal obtained for one project cannot be replayed against another.
    """
    if principal is None:
        raise AccessDenied("You must sign in to do that.")

    if (
        project_id is not None
        and not principal.is_system
        and principal.project_id is not None
        and principal.project_id != project_id
    ):
        raise AccessDenied("You do not have access to that project.")

    if not principal.can(capability):
        raise AccessDenied(
            _DENIAL_MESSAGES.get(capability, "Your role cannot do that.")
        )


def assignable_roles() -> list[str]:
    """Roles an owner may grant. Ownership transfer is handled separately."""
    return [str(Role.MANAGER), str(Role.DEVELOPER), str(Role.VIEWER)]
