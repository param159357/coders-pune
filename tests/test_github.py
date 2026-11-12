"""GitHub ingestion tests (Round 3, improvement 1).

The point of ingesting a repository is that a claim about the code is held to
the same standard as a claim about a document: the quoted text must actually
appear in the ingested record. These tests check that, plus the data
minimisation and the access control around it.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error

import pytest

from tests.conftest import OWNER, principal
from trace_app.integrations.github import (
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    RepoRef,
    RepositoryContent,
    parse_repo,
)
from trace_app.security.authz import AccessDenied
from trace_app.services.extraction_service import segments_from_json
from trace_app.services.github_service import GitHubService
from trace_app.services.review_service import ReviewService


class FakeGitHub:
    """Returns canned content so the whole path runs without a network."""

    def __init__(self, content: RepositoryContent | None = None, error=None):
        self.content = content
        self.error = error
        self.calls = 0

    def fetch(self, ref: RepoRef) -> RepositoryContent:
        self.calls += 1
        if self.error:
            raise self.error
        return self.content


def _content(ref: RepoRef | None = None) -> RepositoryContent:
    ref = ref or RepoRef("acme", "campus")
    return RepositoryContent(
        ref=ref,
        description="A campus portal",
        default_branch="main",
        commits=[
            {
                "sha": "a1b2c3d",
                "message": "Postpone payment integration until release two",
                "author": "priya",
                "date": "2026-10-02",
                "url": "https://github.com/acme/campus/commit/a1b2c3d",
            },
            {
                "sha": "e4f5g6h",
                "message": "Add event listing filters",
                "author": "sam",
                "date": "2026-10-04",
                "url": "https://github.com/acme/campus/commit/e4f5g6h",
            },
        ],
        pull_requests=[
            {
                "number": 42,
                "title": "Registration validation",
                "body": "Rejects duplicate registrations. Capacity check still missing.",
                "state": "open",
                "author": "sam",
                "date": "2026-10-06",
                "url": "https://github.com/acme/campus/pull/42",
            }
        ],
        issues=[
            {
                "number": 17,
                "title": "Email notifications cannot be tested",
                "body": "There is no configured test email service.",
                "state": "open",
                "labels": ["blocker"],
                "author": "priya",
                "date": "2026-10-07",
                "url": "https://github.com/acme/campus/issues/17",
            }
        ],
    )


@pytest.fixture()
def service(connection: sqlite3.Connection, project: dict) -> GitHubService:
    del project
    return GitHubService(connection, OWNER)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    [
        "acme/campus",
        "https://github.com/acme/campus",
        "github.com/acme/campus.git",
        "https://github.com/acme/campus/pull/42",
    ],
)
def test_repository_references_are_parsed(value) -> None:
    assert parse_repo(value).full_name == "acme/campus"


@pytest.mark.parametrize("bad", ["", None, "acme", "bad name/x", "/"])
def test_malformed_repository_references_are_refused(bad) -> None:
    with pytest.raises(GitHubError):
        parse_repo(bad)


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------

def test_ingestion_creates_one_source_per_record_type(service, project) -> None:
    outcome = service.ingest(
        project["id"], "acme/campus", token=None, client=FakeGitHub(_content())
    )
    assert outcome.ok
    assert outcome.sources_added == 3
    assert outcome.records == 4

    from trace_app.services.document_service import DocumentService

    names = [
        s["display_name"]
        for s in DocumentService(service._connection, OWNER).list_sources(project["id"])
    ]
    assert "acme/campus — commits" in names
    assert "acme/campus — pull requests" in names
    assert "acme/campus — issues" in names


def test_each_record_becomes_a_located_segment_with_its_url(service, project) -> None:
    """Evidence must be able to link back to the exact commit or issue."""
    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))

    row = service._connection.execute(
        "SELECT segments_json FROM sources WHERE display_name LIKE '%commits'"
    ).fetchone()
    segments = segments_from_json(row["segments_json"])

    assert [s.location_label for s in segments] == ["commit a1b2c3d", "commit e4f5g6h"]
    assert segments[0].url == "https://github.com/acme/campus/commit/a1b2c3d"
    assert "Postpone payment integration" in segments[0].text


def test_offsets_index_back_into_the_stored_text(service, project) -> None:
    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    row = service._connection.execute(
        "SELECT extracted_text, segments_json FROM sources"
        " WHERE display_name LIKE '%issues'"
    ).fetchone()
    text = row["extracted_text"]
    for segment in segments_from_json(row["segments_json"]):
        assert text[segment.start_offset : segment.end_offset] == segment.text


def test_ingested_sources_are_marked_as_coming_from_github(service, project) -> None:
    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    rows = service._connection.execute(
        "SELECT origin, origin_url, file_type FROM sources"
    ).fetchall()
    assert {row["origin"] for row in rows} == {"github"}
    assert {row["file_type"] for row in rows} == {"github"}
    assert {row["origin_url"] for row in rows} == {"https://github.com/acme/campus"}


def test_the_repository_link_is_recorded(service, project) -> None:
    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    links = service.linked_repositories(project["id"])
    assert len(links) == 1
    assert links[0]["owner"] == "acme" and links[0]["repo"] == "campus"
    assert "4 record(s)" in links[0]["last_sync_note"]


# --------------------------------------------------------------------------
# The token is never stored
# --------------------------------------------------------------------------

def test_the_access_token_is_never_persisted(service, project) -> None:
    secret = "github_pat_THISMUSTNOTBESTORED"
    service.ingest(project["id"], "acme/campus", secret, FakeGitHub(_content()))

    tables = [
        r["name"]
        for r in service._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    ]
    for table in tables:
        rows = service._connection.execute(f"SELECT * FROM {table}").fetchall()
        for row in rows:
            assert secret not in " ".join(str(v) for v in dict(row).values())


# --------------------------------------------------------------------------
# Data minimisation
# --------------------------------------------------------------------------

def test_only_the_author_login_is_kept(service, project) -> None:
    """A handover does not need contributors' email addresses."""
    content = _content()
    service.ingest(project["id"], "acme/campus", None, FakeGitHub(content))

    stored = " ".join(
        str(r["extracted_text"])
        for r in service._connection.execute("SELECT extracted_text FROM sources")
    )
    assert "priya" in stored  # the login is kept
    assert "@" not in stored.replace("—", "")  # no email addresses
    assert "avatar" not in stored.lower()


def test_long_bodies_are_truncated_before_storage() -> None:
    from trace_app.integrations.github import MAX_BODY_CHARS, _trim

    trimmed = _trim("x" * (MAX_BODY_CHARS + 500))
    assert len(trimmed) <= MAX_BODY_CHARS + len("\n[truncated]")
    assert trimmed.endswith("[truncated]")


# --------------------------------------------------------------------------
# Ingested records go through the same evidence verification
# --------------------------------------------------------------------------

def test_a_claim_quoting_a_commit_verifies(service, project) -> None:
    from trace_app.services.evidence_service import SourceIndex, VerificationReport, verify_item
    from trace_app.ai.schemas import EvidenceRef, HandoverItemDraft
    from trace_app.services.document_service import DocumentService

    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    sources = DocumentService(service._connection, OWNER).usable_sources(project["id"])
    index = SourceIndex(sources)
    commits = next(s for s in sources if s["display_name"].endswith("commits"))

    report = VerificationReport()
    good = verify_item(
        HandoverItemDraft(
            category="decision",
            text="Payment integration was postponed to release two.",
            evidence_status="supported",
            evidence=[
                EvidenceRef(
                    source_id=commits["id"],
                    location="commit a1b2c3d",
                    excerpt="Postpone payment integration until release two",
                )
            ],
        ),
        index,
        report,
    )
    assert good.evidence[0].verified
    assert good.evidence[0].location_label == "commit a1b2c3d"
    assert good.evidence_status == "supported"


def test_a_fabricated_commit_quotation_is_rejected(service, project) -> None:
    """A model inventing a commit message must not get away with it."""
    from trace_app.services.evidence_service import SourceIndex, VerificationReport, verify_item
    from trace_app.ai.schemas import EvidenceRef, HandoverItemDraft
    from trace_app.services.document_service import DocumentService

    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    sources = DocumentService(service._connection, OWNER).usable_sources(project["id"])
    commits = next(s for s in sources if s["display_name"].endswith("commits"))

    report = VerificationReport()
    bad = verify_item(
        HandoverItemDraft(
            category="decision",
            text="The team decided to ship payments in release one.",
            evidence_status="supported",
            evidence=[
                EvidenceRef(
                    source_id=commits["id"],
                    location="commit a1b2c3d",
                    excerpt="Ship payment integration in release one",
                )
            ],
        ),
        SourceIndex(sources),
        report,
    )
    assert not bad.evidence[0].verified
    assert bad.evidence_status == "unsupported"
    assert report.excerpt_not_found == 1


# --------------------------------------------------------------------------
# Re-ingestion
# --------------------------------------------------------------------------

def test_reingesting_replaces_the_previous_snapshot(service, project) -> None:
    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))

    count = service._connection.execute(
        "SELECT COUNT(*) AS c FROM sources WHERE display_name LIKE '%commits'"
    ).fetchone()["c"]
    assert count == 1


def test_a_cited_snapshot_is_not_replaced(service, project, connection) -> None:
    """Replacing a cited source would silently break an approved item."""
    from database.repositories import (
        EvidenceRepository,
        HandoverItemRepository,
        ReconstructionRepository,
    )
    from trace_app.models import utc_now

    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    commits = connection.execute(
        "SELECT id FROM sources WHERE display_name LIKE '%commits'"
    ).fetchone()["id"]

    rec = ReconstructionRepository(connection).create(
        project_id=project["id"], status="draft_ready",
        model_provider="fake", model_name="fake", source_count=1,
    )
    item = HandoverItemRepository(connection).create(
        rec, "decision", "A decision.", "supported", "approved",
    )
    EvidenceRepository(connection).create(
        item, commits, "Postpone payment integration", location_label="commit a1b2c3d",
        verified=True,
    )
    connection.commit()

    service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM sources WHERE id = ?", (commits,)
    ).fetchone()["c"] == 1


# --------------------------------------------------------------------------
# Failures
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "rejected the token"),
        (403, "does not grant access"),
        (404, "was not found"),
        (500, "server error"),
    ],
)
def test_http_failures_become_messages_a_user_can_act_on(status, expected) -> None:
    error = urllib.error.HTTPError(
        "https://api.github.com/x", status, "err", {}, None
    )
    translated = GitHubClient("tok")._translate(error)
    assert expected in str(translated)


def test_a_spent_rate_limit_is_reported_as_such() -> None:
    import email.message

    headers = email.message.Message()
    headers["X-RateLimit-Remaining"] = "0"
    error = urllib.error.HTTPError(
        "https://api.github.com/x", 403, "err", headers, None
    )
    assert "rate limit" in str(GitHubClient("tok")._translate(error))


def test_an_ingestion_failure_is_reported_not_raised(service, project) -> None:
    outcome = service.ingest(
        project["id"], "acme/campus", None,
        FakeGitHub(error=GitHubAuthError("GitHub rejected the token.")),
    )
    assert not outcome.ok
    assert "rejected the token" in outcome.message
    assert service.linked_repositories(project["id"]) == []


def test_an_empty_repository_is_reported(service, project) -> None:
    empty = RepositoryContent(ref=RepoRef("acme", "campus"))
    outcome = service.ingest(project["id"], "acme/campus", None, FakeGitHub(empty))
    assert not outcome.ok
    assert "Nothing could be ingested" in outcome.message


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["developer", "viewer"])
def test_read_only_roles_cannot_connect_a_repository(connection, project, role) -> None:
    service = GitHubService(connection, principal(role, project["id"]))
    assert not service.can_connect()
    with pytest.raises(AccessDenied):
        service.ingest(project["id"], "acme/campus", "tok", FakeGitHub(_content()))


def test_a_manager_can_connect_a_repository(connection, project) -> None:
    service = GitHubService(connection, principal("manager", project["id"]))
    assert service.can_connect()
    assert service.ingest(
        project["id"], "acme/campus", None, FakeGitHub(_content())
    ).ok


def test_a_refused_connection_fetches_nothing(connection, project) -> None:
    """The access check runs before the network call, not after."""
    fake = FakeGitHub(_content())
    service = GitHubService(connection, principal("viewer", project["id"]))
    with pytest.raises(AccessDenied):
        service.ingest(project["id"], "acme/campus", "tok", fake)
    assert fake.calls == 0


# --------------------------------------------------------------------------
# A public repository needs no token
# --------------------------------------------------------------------------

def test_a_client_without_a_token_reads_anonymously() -> None:
    anonymous = GitHubClient()
    assert not anonymous.authenticated
    authenticated = GitHubClient("tok")
    assert authenticated.authenticated


def test_an_anonymous_404_suggests_a_token_for_private_repositories() -> None:
    error = urllib.error.HTTPError("https://api.github.com/x", 404, "e", {}, None)
    message = str(GitHubClient()._translate(error))
    assert "private" in message and "token" in message


def test_an_anonymous_rate_limit_suggests_adding_a_token() -> None:
    import email.message

    headers = email.message.Message()
    headers["X-RateLimit-Remaining"] = "0"
    error = urllib.error.HTTPError("https://api.github.com/x", 403, "e", headers, None)
    assert "adding a token raises it" in str(GitHubClient()._translate(error))


def test_ingesting_a_public_repository_without_a_token_is_allowed(
    service, project
) -> None:
    outcome = service.ingest(project["id"], "acme/campus", None, FakeGitHub(_content()))
    assert outcome.ok
