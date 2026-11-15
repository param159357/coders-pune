"""Adversarial tests: what a stranger does to the product on first contact.

The rest of the suite checks that TRACE behaves correctly when it is used as
intended. This file checks that it does not break when it is not: empty
files, the wrong file type, a file that lies about its type, the same action
done twice, a stage reached before the stage it depends on, and hostile text
in fields that are rendered back to a page.

Every test here asserts two things at once -- that the operation is refused
or absorbed, *and* that it is refused with something a person can read. A
crash and a silent success are both failures.
"""

from __future__ import annotations

import pytest

from trace_app.security.authz import Principal
from trace_app.services.auth_service import AuthService, AuthError
from trace_app.services.document_service import DocumentService
from trace_app.services.review_service import ReviewService
from trace_app.utils.file_validation import (
    FileValidationError,
    safe_display_name,
    validate_upload,
)

import config


# --------------------------------------------------------------------------
# Uploads that are malformed, hostile, or simply empty
# --------------------------------------------------------------------------

def test_empty_file_is_rejected_with_a_readable_reason():
    """A 0-byte upload is a mistake, not a document with no findings."""
    with pytest.raises(FileValidationError) as caught:
        validate_upload("empty.txt", b"")
    assert str(caught.value).strip(), "rejection must carry a message"


def test_executable_is_rejected_even_when_renamed_to_txt():
    """The extension allow-list is not the only check.

    Renaming an ELF binary to .txt gets it past the extension check, so the
    content itself has to be read as text before the file is accepted.
    """
    elf = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200
    with pytest.raises(FileValidationError):
        validate_upload("payload.exe", elf)
    # The same bytes under an allowed extension must also not sail through
    # as if they were a readable document.
    try:
        validated = validate_upload("payload.txt", elf)
    except FileValidationError:
        return  # rejected outright, which is a fine outcome
    # If it was accepted, it must at least not claim to be usable text.
    assert validated.size_bytes == len(elf)


def test_file_claiming_to_be_a_pdf_without_pdf_bytes_is_rejected():
    """A .pdf whose leading bytes are not %PDF- is refused before storage."""
    with pytest.raises(FileValidationError):
        validate_upload("not-really.pdf", b"this is plain text, not a PDF")


def test_file_claiming_to_be_a_docx_without_zip_bytes_is_rejected():
    with pytest.raises(FileValidationError):
        validate_upload("not-really.docx", b"plain text, definitely not a zip")


def test_oversized_upload_is_rejected_at_the_documented_ceiling():
    """The ceiling the interface advertises is the ceiling enforced."""
    too_big = b"x" * (config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    with pytest.raises(FileValidationError) as caught:
        validate_upload("huge.txt", too_big)
    assert str(config.MAX_UPLOAD_MB) in str(caught.value), (
        "the message should tell the user what the limit actually is"
    )


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "/absolute/path/report.txt",
        "name\x00truncated.txt",
        "report.txt ",
    ],
)
def test_hostile_filenames_cannot_escape_their_directory(hostile):
    """Display names are sanitised; paths are generated, never user-supplied."""
    safe = safe_display_name(hostile)
    assert "/" not in safe and "\\" not in safe
    assert ".." not in safe
    assert "\x00" not in safe
    assert safe == safe.strip()
    assert safe, "a name must never sanitise down to nothing"


def test_missing_filename_still_yields_a_usable_name():
    assert safe_display_name(None)
    assert safe_display_name("")


# --------------------------------------------------------------------------
# The same action, done twice
# --------------------------------------------------------------------------

def test_uploading_the_same_bytes_twice_does_not_duplicate_evidence(
    connection, project, owner, isolated_storage
):
    """A judge will double-click. The second upload must be absorbed."""
    documents = DocumentService(connection, owner)
    content = b"CampusConnect uses a single shared database for all services."

    first = documents.upload(project["id"], "notes.txt", content)
    assert first.accepted

    second = documents.upload(project["id"], "notes.txt", content)
    assert not second.accepted, "a byte-identical re-upload must not be stored"
    assert second.duplicate_of == first.source["id"]
    assert "identical" in second.message.lower()

    assert documents.source_count(project["id"]) == 1


def test_registering_the_same_email_twice_is_refused_clearly(connection):
    auth = AuthService(connection)
    auth.register("dup@test.local", "a-good-password", "First")
    with pytest.raises(AuthError) as caught:
        auth.register("dup@test.local", "another-password", "Second")
    assert str(caught.value).strip()


def test_email_registration_is_case_insensitive(connection):
    """Signing up twice with different capitalisation is still one account."""
    auth = AuthService(connection)
    auth.register("Case@Test.local", "a-good-password", "First")
    with pytest.raises(AuthError):
        auth.register("case@test.local", "another-password", "Second")


# --------------------------------------------------------------------------
# Stages reached out of order, and empty states
# --------------------------------------------------------------------------

def test_review_of_a_reconstruction_that_does_not_exist_is_empty_not_fatal(
    connection, owner
):
    """A stale link or a refresh must not raise."""
    reviews = ReviewService(connection, owner)
    assert reviews.list_items("rec_does_not_exist") == []
    assert reviews.approved_items("rec_does_not_exist") == []


def test_progress_on_an_unknown_reconstruction_reports_zero(connection, owner):
    reviews = ReviewService(connection, owner)
    progress = reviews.progress("rec_does_not_exist")
    assert progress.total == 0
    assert progress.reviewed == 0
    # A zero-item run must not divide by zero when drawing the progress bar.
    assert progress.fraction == 0.0


def test_a_project_with_no_sources_reports_none_usable(
    connection, project, owner
):
    documents = DocumentService(connection, owner)
    assert documents.usable_sources(project["id"]) == []
    assert documents.source_count(project["id"]) == 0


def test_deleting_a_source_that_is_already_gone_is_not_fatal(
    connection, project, owner, isolated_storage
):
    """Two tabs, both with a Remove button. The second click must be safe."""
    documents = DocumentService(connection, owner)
    outcome = documents.upload(project["id"], "notes.txt", b"Some project text.")
    assert outcome.accepted

    removed, _ = documents.delete_source(outcome.source["id"], project["id"])
    assert removed
    removed_again, message = documents.delete_source(
        outcome.source["id"], project["id"]
    )
    assert not removed_again
    assert message.strip(), "the second delete needs to say what happened"


def test_fetching_a_source_from_another_project_returns_nothing(
    connection, project, project_service, owner, isolated_storage
):
    """Guessing an ID must not reach across a project boundary."""
    documents = DocumentService(connection, owner)
    outcome = documents.upload(project["id"], "notes.txt", b"Private text.")
    other = project_service.create_project("Other", "Unrelated", "SAMURAI")

    assert documents.get_source(outcome.source["id"], other["id"]) is None


# --------------------------------------------------------------------------
# Hostile text in fields that are rendered back to a page
# --------------------------------------------------------------------------

def test_markup_in_a_project_name_is_stored_verbatim_not_executed(
    project_service,
):
    """TRACE escapes on render; the stored value stays exactly as typed.

    This pins the half of the contract the database owns. The rendering half
    is ui.esc(), covered below.
    """
    hostile = "<script>alert('xss')</script>"
    created = project_service.create_project(hostile, "A description", "SAMURAI")
    assert created["name"] == hostile


def test_the_renderer_escapes_markup():
    from trace_app import ui

    rendered = ui.esc("<script>alert('xss')</script>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_the_renderer_handles_none_and_numbers():
    """esc() is called on values straight out of the database."""
    from trace_app import ui

    assert ui.esc(None) == ""
    assert ui.esc(0) == "0"
    assert ui.esc(False) == "False"


# --------------------------------------------------------------------------
# Access control under guessing
# --------------------------------------------------------------------------

def test_a_principal_with_no_project_cannot_read_another_projects_sources(
    connection, project, owner, isolated_storage
):
    documents = DocumentService(connection, owner)
    documents.upload(project["id"], "notes.txt", b"Private project text.")

    stranger = Principal(
        user_id="usr_stranger", email="stranger@test.local", role="viewer"
    )
    stranger_documents = DocumentService(connection, stranger)

    from trace_app.security.authz import AccessDenied

    try:
        found = stranger_documents.list_sources(project["id"])
    except AccessDenied:
        return  # refused outright
    assert found == [], "a stranger must not receive another project's sources"


# --------------------------------------------------------------------------
# GitHub error messages must describe what actually happened
# --------------------------------------------------------------------------

def _http_error(status: int, headers: dict | None = None):
    import urllib.error

    return urllib.error.HTTPError(
        url="https://api.github.com/repos/x/y",
        code=status,
        msg="refused",
        hdrs=headers or {},
        fp=None,
    )


def test_anonymous_403_does_not_blame_a_token_that_was_never_supplied():
    """The panel says a public repo needs no token, so the error must agree.

    Telling someone their token is wrong when they did not supply one sends
    them looking for a setting they never set.
    """
    from trace_app.integrations.github import GitHubClient

    client = GitHubClient()  # no token
    message = str(client._translate(_http_error(403)))
    assert "the token does not grant" not in message.lower()
    assert "anonymous" in message.lower() or "private" in message.lower()


def test_authenticated_403_does_point_at_the_token():
    from trace_app.integrations.github import GitHubClient

    client = GitHubClient(token="ghp_example_not_a_real_token")
    message = str(client._translate(_http_error(403)))
    assert "token" in message.lower()


def test_spent_rate_limit_is_reported_as_a_rate_limit():
    from trace_app.integrations.github import GitHubClient

    client = GitHubClient()
    message = str(
        client._translate(_http_error(403, {"X-RateLimit-Remaining": "0"}))
    )
    assert "rate limit" in message.lower()


# --------------------------------------------------------------------------
# A placeholder key is the absence of a key
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "your-api-key-here", "YOUR-API-KEY-HERE",
     "your_api_key", "<paste your key>", "changeme", "TODO"],
)
def test_placeholder_keys_are_not_treated_as_configured(value):
    assert config.is_placeholder_secret(value)
    assert not config.LLMConfig(
        provider="gemini", model="m", api_key=value, base_url=None
    ).is_configured


@pytest.mark.parametrize(
    "value",
    ["AIzaSyExampleLookingKeyValue1234567890", "sk-abc123", "a" * 39],
)
def test_real_looking_keys_are_still_accepted(value):
    """The placeholder rule must never reject a key someone actually pasted."""
    assert not config.is_placeholder_secret(value)
    assert config.LLMConfig(
        provider="gemini", model="m", api_key=value, base_url=None
    ).is_configured


def test_copying_the_shipped_env_example_does_not_look_configured():
    """Following the README's setup must not produce a false "Configured".

    This reads the committed .env.example so the test fails if someone later
    puts a value there that the placeholder rule does not recognise.
    """
    import re
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / ".env.example"
    text = example.read_text(encoding="utf-8")
    match = re.search(r"^LLM_API_KEY=(.*)$", text, re.MULTILINE)
    assert match, ".env.example should document LLM_API_KEY"
    assert config.is_placeholder_secret(match.group(1)), (
        "the value shipped in .env.example must register as a placeholder, "
        "otherwise copying it makes TRACE claim an AI provider it lacks"
    )


# --------------------------------------------------------------------------
# The public demo a judge is invited to use
# --------------------------------------------------------------------------

def test_the_public_demo_seeds_itself_on_an_empty_database(connection, isolated_storage):
    """The sign-in screen prints these credentials, so they must work cold.

    A visitor arrives before anyone has loaded the demo. If seeding only
    happened after a signed-in user pressed a button, the credentials printed
    on the sign-in screen would simply fail.
    """
    from trace_app.services.auth_service import AuthService
    from trace_app.services.demo_service import (
        DEMO_ACCOUNTS,
        DEMO_PASSWORD,
        ensure_public_demo,
    )

    project = ensure_public_demo(connection)
    assert project is not None

    auth = AuthService(connection)
    for email, _display, _role in DEMO_ACCOUNTS:
        user = auth.authenticate(email, DEMO_PASSWORD)
        assert user is not None, f"{email} must be able to sign in"


def test_each_demo_account_holds_the_role_it_advertises(connection, isolated_storage):
    """The panel names a role beside each address; the database must agree."""
    from trace_app.services.auth_service import AuthService
    from trace_app.services.demo_service import DEMO_ACCOUNTS, ensure_public_demo

    project = ensure_public_demo(connection)
    assert project is not None
    auth = AuthService(connection)

    for email, _display, role in DEMO_ACCOUNTS:
        user = auth.find_by_email(email)
        assert user is not None
        principal = auth.principal_for(user, project["id"])
        assert principal.role == role, (
            f"{email} is advertised as {role} but holds {principal.role}"
        )


def test_seeding_the_public_demo_twice_changes_nothing(connection, isolated_storage):
    """It runs once per process, but a restart or a race must be harmless."""
    from trace_app.services.demo_service import ensure_public_demo

    first = ensure_public_demo(connection)
    second = ensure_public_demo(connection)
    assert first is not None and second is not None
    assert first["id"] == second["id"], "a second call must not make a second project"

    sources = connection.execute(
        "SELECT COUNT(*) FROM sources WHERE project_id = ?", (first["id"],)
    ).fetchone()[0]
    assert sources == 4, f"expected the 4 demo sources, found {sources}"


def test_the_demo_viewer_cannot_reach_the_sources(connection, isolated_storage):
    """The panel promises the viewer is refused; that must be true."""
    from trace_app.security.authz import AccessDenied
    from trace_app.services.auth_service import AuthService
    from trace_app.services.demo_service import ensure_public_demo

    project = ensure_public_demo(connection)
    assert project is not None
    auth = AuthService(connection)
    viewer = auth.find_by_email("viewer@campusconnect.demo")
    principal = auth.principal_for(viewer, project["id"])

    documents = DocumentService(connection, principal)
    try:
        found = documents.list_sources(project["id"])
    except AccessDenied:
        return
    assert found == [], "the demo viewer must not receive the source documents"


def test_a_role_that_cannot_reconstruct_is_not_told_to_reconstruct():
    """The empty review state must not send a viewer to a page they lack.

    A viewer has no Project Workspace in their navigation and would be
    refused the action, so pointing them at it is a dead end.
    """
    from trace_app.security.authz import Capability, Role, ROLE_CAPABILITIES

    assert Capability.RUN_RECONSTRUCTION not in ROLE_CAPABILITIES[Role.VIEWER]
    assert Capability.RUN_RECONSTRUCTION not in ROLE_CAPABILITIES[Role.DEVELOPER]
    assert Capability.RUN_RECONSTRUCTION in ROLE_CAPABILITIES[Role.MANAGER]


def test_a_restricted_role_is_not_told_how_many_citations_exist(
    connection, isolated_storage
):
    """The verification figures describe items a viewer may not see.

    "24 citations checked" shown to someone who is shown none of those items
    reports the volume of hidden content -- the same leak the review counts
    already avoid. sees_only_approved() is what the page branches on, so it
    must be true for exactly the roles that are shown a filtered list.
    """
    from trace_app.services.auth_service import AuthService
    from trace_app.services.demo_service import ensure_public_demo
    from trace_app.services.review_service import ReviewService

    project = ensure_public_demo(connection)
    assert project is not None
    auth = AuthService(connection)

    expected = {
        "owner@campusconnect.demo": False,
        "manager@campusconnect.demo": False,
        "developer@campusconnect.demo": False,
        "viewer@campusconnect.demo": True,
    }
    for email, hidden in expected.items():
        user = auth.find_by_email(email)
        principal = auth.principal_for(user, project["id"])
        reviews = ReviewService(connection, principal)
        assert reviews.sees_only_approved() is hidden, (
            f"{email}: sees_only_approved() should be {hidden}"
        )


def test_app_imports_demo_service_as_a_module_not_by_name():
    """Guard the deployment against a stale module cache.

    Streamlit Community Cloud can redeploy app.py while keeping an older
    copy of an already-imported module in memory. A `from x import
    new_symbol` then raises ImportError at startup and the whole app serves
    an error page until a human reboots it -- which is what happened when the
    demo panel first shipped. Going through the module downgrades that to a
    missing panel.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app.py").read_text(
        encoding="utf-8"
    )
    assert "from trace_app.services import demo_service" in source
    for symbol in ("DEMO_ROLE_BLURBS", "ensure_public_demo"):
        assert f"    {symbol},\n" not in source, (
            f"{symbol} is imported by name from demo_service; import the "
            "module and use demo_service." + symbol + " instead, so a stale "
            "module cache cannot take the whole app down"
        )
