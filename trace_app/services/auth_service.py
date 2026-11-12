"""Accounts, sign-in, and resolving a principal for a project.

Two rules shape this module.

**Login must not leak which accounts exist.** A wrong password and an unknown
email produce the same message, and the unknown-email path still performs a
hash verification against a dummy value so it takes comparable time.

**A principal is derived from a stored membership, never from the session.**
:meth:`AuthService.principal_for` looks the role up in the database on every
call, so revoking access takes effect immediately and a stale session cannot
carry a role it no longer has.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from database.repositories import MembershipRepository, ProjectRepository, UserRepository
from trace_app.security.authz import AccessDenied, Principal, Role
from trace_app.security.passwords import (
    PasswordError,
    hash_password,
    validate_password,
    verify_password,
)

MAX_EMAIL_LENGTH = 254
MAX_NAME_LENGTH = 80

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

#: Verified against when the email is unknown, so an unknown account costs
#: roughly the same time as a wrong password.
_DUMMY_HASH = (
    "scrypt$16384$8$1$00000000000000000000000000000000$"
    + "0" * 64
)

GENERIC_LOGIN_FAILURE = "That email and password combination was not recognised."


class AuthError(ValueError):
    """A sign-up or sign-in failure. The message is safe to display."""


def normalise_email(value: str | None) -> str:
    cleaned = " ".join(str(value or "").split()).strip().lower()
    if not cleaned:
        raise AuthError("An email address is required.")
    if len(cleaned) > MAX_EMAIL_LENGTH or not _EMAIL.match(cleaned):
        raise AuthError("That does not look like an email address.")
    return cleaned


class AuthService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._users = UserRepository(connection)
        self._members = MembershipRepository(connection)
        self._projects = ProjectRepository(connection)

    # ------------------------------------------------------------- accounts
    def register(
        self,
        email: str | None,
        password: str | None,
        display_name: str | None = None,
        is_demo: bool = False,
    ) -> dict[str, Any]:
        """Create an account. Raises ``AuthError`` if the email is taken."""
        clean_email = normalise_email(email)
        try:
            checked = validate_password(password)
        except PasswordError as exc:
            raise AuthError(str(exc)) from exc

        if self._users.find_by_email(clean_email) is not None:
            raise AuthError("An account with that email already exists.")

        name = " ".join(str(display_name or "").split())[:MAX_NAME_LENGTH] or None
        try:
            return self._users.create(
                email=clean_email,
                password_hash=hash_password(checked),
                display_name=name,
                is_demo=is_demo,
            )
        except sqlite3.IntegrityError as exc:  # lost a race on the unique index
            raise AuthError("An account with that email already exists.") from exc

    def authenticate(self, email: str | None, password: str | None) -> dict[str, Any]:
        """Verify credentials and return the user, or raise ``AuthError``."""
        try:
            clean_email = normalise_email(email)
        except AuthError:
            # Still do the work, so a malformed email is not distinguishable.
            verify_password(password or "", _DUMMY_HASH)
            raise AuthError(GENERIC_LOGIN_FAILURE) from None

        found = self._users.password_hash_for(clean_email)
        if found is None:
            verify_password(password or "", _DUMMY_HASH)
            raise AuthError(GENERIC_LOGIN_FAILURE)

        user_id, stored_hash = found
        if not verify_password(password, stored_hash):
            raise AuthError(GENERIC_LOGIN_FAILURE)

        self._users.record_login(user_id)
        user = self._users.get(user_id)
        if user is None:  # pragma: no cover - row vanished mid-login
            raise AuthError(GENERIC_LOGIN_FAILURE)
        return user

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self._users.get(user_id) if user_id else None

    def find_by_email(self, email: str | None) -> dict[str, Any] | None:
        try:
            return self._users.find_by_email(normalise_email(email))
        except AuthError:
            return None

    def user_count(self) -> int:
        return self._users.count()

    # ----------------------------------------------------------- principals
    def principal_for(self, user: dict[str, Any], project_id: str) -> Principal:
        """Build the principal for this user on this project.

        The role is read from the database on every call, so a revoked
        membership stops working at once rather than when a session expires.
        Raises ``AccessDenied`` when there is no membership at all -- an
        unauthorised project is not merely hidden, it is unreachable.
        """
        role = self._members.role_for(project_id, str(user["id"]))
        if role is None:
            raise AccessDenied("You do not have access to that project.")
        return Principal(
            user_id=str(user["id"]),
            email=str(user["email"]),
            role=role,
            project_id=project_id,
        )

    def try_principal_for(
        self, user: dict[str, Any] | None, project_id: str | None
    ) -> Principal | None:
        if not user or not project_id:
            return None
        try:
            return self.principal_for(user, project_id)
        except AccessDenied:
            return None

    # --------------------------------------------------------- memberships
    def register_owner(self, project_id: str, user_id: str) -> None:
        """Record the creator of a project as its owner."""
        self._members.grant(project_id, user_id, Role.OWNER, added_by=user_id)
        with self._connection:
            self._connection.execute(
                "UPDATE projects SET owner_id = ? WHERE id = ?",
                (user_id, project_id),
            )

    def list_members(self, project_id: str) -> list[dict[str, Any]]:
        return self._members.list_members(project_id)

    def projects_for(self, user: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not user:
            return []
        return self._members.projects_for_user(str(user["id"]))

    def grant_role(
        self, principal: Principal, project_id: str, email: str | None, role: str
    ) -> dict[str, Any]:
        """Give an existing account a role on this project.

        The caller must already hold MANAGE_MEMBERS; that check belongs to the
        caller so this method stays usable from seeding as well.
        """
        if role not in {str(r) for r in Role} or role == str(Role.OWNER):
            raise AuthError("That is not a role you can assign.")

        target = self.find_by_email(email)
        if target is None:
            raise AuthError(
                "No account with that email exists yet. Ask them to sign up "
                "first, then add them."
            )
        if str(target["id"]) == principal.user_id:
            raise AuthError("You cannot change your own role.")

        self._members.grant(
            project_id, str(target["id"]), role, added_by=principal.user_id
        )
        return target

    def revoke_access(
        self, principal: Principal, project_id: str, user_id: str
    ) -> None:
        if user_id == principal.user_id:
            raise AuthError("You cannot remove your own access.")
        if self._members.role_for(project_id, user_id) == str(Role.OWNER):
            raise AuthError("The project owner cannot be removed.")
        self._members.revoke(project_id, user_id)
