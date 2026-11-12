"""Access-control tests.

The Round 3 commitment was access control enforced by real server-side checks,
"not by hiding UI elements". These tests exist to hold that claim to account:
each one asks a role to do something it should not be able to do, and asserts
the attempt *fails* rather than merely being absent from a screen.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import OWNER, principal
from trace_app.security.authz import (
    AccessDenied,
    Capability,
    Principal,
    ROLE_CAPABILITIES,
    Role,
    assignable_roles,
    require,
)
from trace_app.security.passwords import hash_password, verify_password
from trace_app.services.auth_service import (
    GENERIC_LOGIN_FAILURE,
    AuthError,
    AuthService,
)
from trace_app.services.document_service import DocumentService
from trace_app.services.reconstruction_service import ReconstructionService
from trace_app.services.review_service import ReviewService

BRIEF = (
    "The initial release focuses on event discovery.\n\n"
    "Payment integration was postponed until after the first release."
)


@pytest.fixture()
def workspace(connection: sqlite3.Connection, isolated_storage, project: dict):
    """A project with one source, one approved item and one unapproved item."""
    del isolated_storage
    from database.repositories import (
        EvidenceRepository,
        HandoverItemRepository,
        ReconstructionRepository,
    )
    from trace_app.models import EvidenceStatus, ReviewStatus

    documents = DocumentService(connection, OWNER)
    source = documents.upload(project["id"], "brief.txt", BRIEF.encode()).source

    reconstruction_id = ReconstructionRepository(connection).create(
        project_id=project["id"], status="draft_ready",
        model_provider="fake", model_name="fake", source_count=1,
    )
    items = HandoverItemRepository(connection)
    evidence = EvidenceRepository(connection)
    approved = items.create(
        reconstruction_id, "decision", "Payment integration was postponed.",
        EvidenceStatus.SUPPORTED, ReviewStatus.APPROVED, sort_order=0,
    )
    evidence.create(
        approved, source["id"],
        "Payment integration was postponed until after the first release.",
        location_label="paragraph 2", verified=True,
    )
    unapproved = items.create(
        reconstruction_id, "risk", "An unreviewed risk nobody has accepted.",
        EvidenceStatus.NEEDS_REVIEW, ReviewStatus.PROPOSED, sort_order=1,
    )
    connection.commit()
    return {
        "connection": connection,
        "project": project,
        "source": source,
        "reconstruction_id": reconstruction_id,
        "approved": approved,
        "unapproved": unapproved,
    }


def _as(role: str, workspace) -> Principal:
    return principal(role, workspace["project"]["id"])


# --------------------------------------------------------------------------
# A viewer cannot reach source documents
# --------------------------------------------------------------------------

def test_a_viewer_cannot_read_source_documents(workspace) -> None:
    """The spec is explicit: check access before exposing source documents."""
    documents = DocumentService(workspace["connection"], _as("viewer", workspace))

    with pytest.raises(AccessDenied):
        documents.list_sources(workspace["project"]["id"])
    with pytest.raises(AccessDenied):
        documents.get_source(workspace["source"]["id"], workspace["project"]["id"])
    with pytest.raises(AccessDenied):
        documents.usable_sources(workspace["project"]["id"])


def test_a_viewer_cannot_upload_or_delete_sources(workspace) -> None:
    documents = DocumentService(workspace["connection"], _as("viewer", workspace))
    with pytest.raises(AccessDenied):
        documents.upload(workspace["project"]["id"], "x.txt", b"some content here")
    with pytest.raises(AccessDenied):
        documents.delete_source(workspace["source"]["id"], workspace["project"]["id"])


def test_a_developer_can_read_sources_but_not_change_them(workspace) -> None:
    """The incoming developer is the person the handover is for."""
    documents = DocumentService(workspace["connection"], _as("developer", workspace))
    assert len(documents.list_sources(workspace["project"]["id"])) == 1
    with pytest.raises(AccessDenied):
        documents.upload(workspace["project"]["id"], "x.txt", b"some content here")


# --------------------------------------------------------------------------
# A viewer is never handed an unapproved item
# --------------------------------------------------------------------------

def test_a_viewer_receives_only_approved_items(workspace) -> None:
    """Filtering happens in the service, so there is no route to the rest."""
    reviews = ReviewService(workspace["connection"], _as("viewer", workspace))
    items = reviews.list_items(workspace["reconstruction_id"])

    assert [item["id"] for item in items] == [workspace["approved"]]
    assert all(item["review_status"] == "approved" for item in items)
    texts = " ".join(item["text"] for item in items)
    assert "nobody has accepted" not in texts


def test_a_developer_sees_every_item(workspace) -> None:
    reviews = ReviewService(workspace["connection"], _as("developer", workspace))
    ids = {item["id"] for item in reviews.list_items(workspace["reconstruction_id"])}
    assert ids == {workspace["approved"], workspace["unapproved"]}


def test_a_viewer_still_sees_the_evidence_for_approved_claims(workspace) -> None:
    """Traceability is the product; a viewer must be able to check a claim."""
    reviews = ReviewService(workspace["connection"], _as("viewer", workspace))
    item = reviews.list_items(workspace["reconstruction_id"])[0]
    assert item["evidence"]
    assert item["evidence"][0]["excerpt"]
    assert item["evidence"][0]["source_display_name"] == "brief.txt"


# --------------------------------------------------------------------------
# Read-only roles cannot review
# --------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["developer", "viewer"])
def test_read_only_roles_cannot_change_a_decision(workspace, role) -> None:
    reviews = ReviewService(workspace["connection"], _as(role, workspace))
    item_id, reconstruction_id = workspace["approved"], workspace["reconstruction_id"]

    with pytest.raises(AccessDenied):
        reviews.approve(item_id, reconstruction_id)
    with pytest.raises(AccessDenied):
        reviews.reject(item_id, reconstruction_id)
    with pytest.raises(AccessDenied):
        reviews.reopen(item_id, reconstruction_id)
    with pytest.raises(AccessDenied):
        reviews.edit_text(item_id, reconstruction_id, "Rewritten by someone read-only.")
    with pytest.raises(AccessDenied):
        reviews.set_note(item_id, reconstruction_id, "A note.")


def test_a_refused_edit_leaves_the_item_untouched(workspace) -> None:
    """A denial must not be a partial write."""
    original = "Payment integration was postponed."
    reviews = ReviewService(workspace["connection"], _as("viewer", workspace))
    with pytest.raises(AccessDenied):
        reviews.edit_text(workspace["approved"], workspace["reconstruction_id"], "Hijacked")

    as_owner = ReviewService(workspace["connection"], OWNER)
    item = next(
        i
        for i in as_owner.list_items(workspace["reconstruction_id"])
        if i["id"] == workspace["approved"]
    )
    assert item["text"] == original


def test_a_manager_can_review(workspace) -> None:
    reviews = ReviewService(workspace["connection"], _as("manager", workspace))
    reviews.reject(workspace["unapproved"], workspace["reconstruction_id"])
    statuses = {
        i["id"]: i["review_status"]
        for i in reviews.list_items(workspace["reconstruction_id"])
    }
    assert statuses[workspace["unapproved"]] == "rejected"


# --------------------------------------------------------------------------
# Reconstruction
# --------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["developer", "viewer"])
def test_read_only_roles_cannot_run_a_reconstruction(workspace, role) -> None:
    service = ReconstructionService(workspace["connection"], _as(role, workspace))
    with pytest.raises(AccessDenied):
        service.run(workspace["project"])


# --------------------------------------------------------------------------
# Project scoping
# --------------------------------------------------------------------------

def test_a_principal_cannot_be_replayed_against_another_project(
    connection, isolated_storage, project_service
) -> None:
    """A principal issued for one project must not work on another."""
    del isolated_storage
    alpha = project_service.create_project("Alpha")
    beta = project_service.create_project("Beta")

    alpha_manager = principal("manager", alpha["id"])
    documents = DocumentService(connection, alpha_manager)

    documents.list_sources(alpha["id"])  # allowed
    with pytest.raises(AccessDenied):
        documents.list_sources(beta["id"])


def test_a_non_member_cannot_obtain_a_principal(connection, project_service) -> None:
    auth = AuthService(connection)
    outsider = auth.register("outsider@test.local", "outsider-password")
    secret = project_service.create_project("Confidential")

    with pytest.raises(AccessDenied):
        auth.principal_for(outsider, secret["id"])
    assert auth.try_principal_for(outsider, secret["id"]) is None
    assert auth.projects_for(outsider) == []


def test_revoking_access_takes_effect_immediately(connection, project_service) -> None:
    """The role is read from the database on every call, not from a session."""
    auth = AuthService(connection)
    owner = auth.register("owner2@test.local", "owner-password")
    member = auth.register("member@test.local", "member-password")
    proj = project_service.create_project("Alpha")
    auth.register_owner(proj["id"], owner["id"])
    owner_principal = auth.principal_for(owner, proj["id"])

    auth.grant_role(owner_principal, proj["id"], "member@test.local", "developer")
    assert auth.principal_for(member, proj["id"]).role == "developer"

    auth.revoke_access(owner_principal, proj["id"], member["id"])
    with pytest.raises(AccessDenied):
        auth.principal_for(member, proj["id"])


def test_the_owner_cannot_be_removed_or_self_demoted(connection, project_service) -> None:
    auth = AuthService(connection)
    owner = auth.register("owner3@test.local", "owner-password")
    proj = project_service.create_project("Alpha")
    auth.register_owner(proj["id"], owner["id"])
    actor = auth.principal_for(owner, proj["id"])

    with pytest.raises(AuthError):
        auth.revoke_access(actor, proj["id"], owner["id"])
    with pytest.raises(AuthError):
        auth.grant_role(actor, proj["id"], "owner3@test.local", "viewer")


def test_owner_is_not_an_assignable_role() -> None:
    assert str(Role.OWNER) not in assignable_roles()


def test_granting_a_role_to_an_unknown_email_is_refused(
    connection, project_service
) -> None:
    auth = AuthService(connection)
    owner = auth.register("owner4@test.local", "owner-password")
    proj = project_service.create_project("Alpha")
    auth.register_owner(proj["id"], owner["id"])
    actor = auth.principal_for(owner, proj["id"])

    with pytest.raises(AuthError, match="No account"):
        auth.grant_role(actor, proj["id"], "ghost@test.local", "viewer")


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

def test_login_does_not_reveal_whether_an_account_exists(connection) -> None:
    auth = AuthService(connection)
    auth.register("real@test.local", "real-password")

    messages = set()
    for email, password in [
        ("real@test.local", "wrong-password"),
        ("ghost@test.local", "any-password"),
        ("not-an-email", "any-password"),
    ]:
        with pytest.raises(AuthError) as caught:
            auth.authenticate(email, password)
        messages.add(str(caught.value))

    assert messages == {GENERIC_LOGIN_FAILURE}


def test_passwords_are_salted_and_never_stored_in_clear(connection) -> None:
    auth = AuthService(connection)
    auth.register("a@test.local", "shared-password")
    auth.register("b@test.local", "shared-password")

    rows = connection.execute("SELECT email, password_hash FROM users").fetchall()
    hashes = [row["password_hash"] for row in rows]
    assert all("shared-password" not in h for h in hashes)
    assert all(h.startswith("scrypt$") for h in hashes)
    assert len(set(hashes)) == len(hashes)  # same password, different hashes


def test_a_tampered_hash_fails_closed() -> None:
    stored = hash_password("correct-password")
    assert verify_password("correct-password", stored)
    assert not verify_password("correct-password", stored[:-1] + "0")
    assert not verify_password("correct-password", "not-a-hash")
    assert not verify_password("correct-password", None)


# --------------------------------------------------------------------------
# The policy itself
# --------------------------------------------------------------------------

def test_every_role_has_an_explicit_policy() -> None:
    assert set(ROLE_CAPABILITIES) == {str(r) for r in Role}


def test_only_the_owner_manages_access_or_deletes() -> None:
    for role in [Role.MANAGER, Role.DEVELOPER, Role.VIEWER]:
        assert Capability.MANAGE_MEMBERS not in ROLE_CAPABILITIES[role]
        assert Capability.DELETE_PROJECT not in ROLE_CAPABILITIES[role]


def test_an_unknown_role_gets_no_capabilities() -> None:
    rogue = Principal("u", "u@x", "superadmin", "proj_1")
    assert rogue.capabilities == frozenset()
    with pytest.raises(AccessDenied):
        require(rogue, Capability.VIEW_APPROVED, "proj_1")


def test_an_anonymous_caller_is_refused() -> None:
    with pytest.raises(AccessDenied, match="sign in"):
        require(None, Capability.VIEW_APPROVED)


def test_a_viewer_is_not_told_how_many_items_they_cannot_see(workspace) -> None:
    """Counting hidden items would leak their existence."""
    viewer = ReviewService(workspace["connection"], _as("viewer", workspace))
    progress = viewer.progress(workspace["reconstruction_id"])
    assert progress.approved == 1
    assert progress.proposed == 0
    assert progress.rejected == 0
    assert progress.total == 1

    manager = ReviewService(workspace["connection"], _as("manager", workspace))
    full = manager.progress(workspace["reconstruction_id"])
    assert full.total == 2 and full.proposed == 1
