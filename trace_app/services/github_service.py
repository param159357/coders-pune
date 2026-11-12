"""Ingesting a GitHub repository as TRACE sources.

An ingested commit, pull request or issue becomes an ordinary source row, so
everything downstream works on it unchanged: the same chunking, the same
Pydantic validation, the same evidence verification, the same review and
export. A claim about the codebase is checked exactly as strictly as a claim
about a PDF -- the quoted text must actually appear in the ingested record.

Each record keeps its permanent GitHub URL on the segment it produced, so
evidence can link straight back to the commit or pull request it quotes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from database.connection import transaction
from database.repositories import ProjectRepository, SourceRepository
from trace_app.integrations.github import (
    GitHubClient,
    GitHubError,
    RepoRef,
    RepositoryContent,
    parse_repo,
)
from trace_app.models import ExtractionStatus, new_id, utc_now
from trace_app.security.authz import Capability, Principal, require
from trace_app.services.extraction_service import build_segments

logger = logging.getLogger(__name__)

ORIGIN_GITHUB = "github"


@dataclass
class IngestOutcome:
    ok: bool
    message: str
    sources_added: int = 0
    records: int = 0
    notes: list[str] = field(default_factory=list)


def _commit_blocks(content: RepositoryContent) -> list[tuple[str, str, str | None]]:
    return [
        (
            f"commit {c['sha']}",
            f"Commit {c['sha']} by {c['author']} on {c['date']}:\n{c['message']}",
            c["url"] or None,
        )
        for c in content.commits
        if c.get("message")
    ]


def _pull_request_blocks(
    content: RepositoryContent,
) -> list[tuple[str, str, str | None]]:
    blocks: list[tuple[str, str, str | None]] = []
    for pr in content.pull_requests:
        body = f"\n{pr['body']}" if pr.get("body") else ""
        blocks.append(
            (
                f"pull request #{pr['number']}",
                f"Pull request #{pr['number']} — {pr['title']} "
                f"({pr['state']}, opened {pr['date']} by {pr['author']}).{body}",
                pr["url"] or None,
            )
        )
    return blocks


def _issue_blocks(content: RepositoryContent) -> list[tuple[str, str, str | None]]:
    blocks: list[tuple[str, str, str | None]] = []
    for issue in content.issues:
        labels = (
            f" Labels: {', '.join(issue['labels'])}." if issue.get("labels") else ""
        )
        body = f"\n{issue['body']}" if issue.get("body") else ""
        blocks.append(
            (
                f"issue #{issue['number']}",
                f"Issue #{issue['number']} — {issue['title']} "
                f"({issue['state']}, opened {issue['date']} by "
                f"{issue['author']}).{labels}{body}",
                issue["url"] or None,
            )
        )
    return blocks


class GitHubService:
    def __init__(self, connection: sqlite3.Connection, principal: Principal) -> None:
        self._connection = connection
        self._principal = principal
        self._sources = SourceRepository(connection)
        self._projects = ProjectRepository(connection)

    def can_connect(self) -> bool:
        return self._principal.can(Capability.CONNECT_INTEGRATIONS)

    # ------------------------------------------------------------- linked
    def linked_repositories(self, project_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM repository_links WHERE project_id = ?"
            " ORDER BY connected_at DESC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _record_link(
        self, project_id: str, ref: RepoRef, note: str
    ) -> None:
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO repository_links (id, project_id, owner, repo,"
                " html_url, connected_at, connected_by, last_synced_at,"
                " last_sync_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (project_id, owner, repo) DO UPDATE SET"
                "   last_synced_at = excluded.last_synced_at,"
                "   last_sync_note = excluded.last_sync_note",
                (
                    new_id("repo"),
                    project_id,
                    ref.owner,
                    ref.repo,
                    ref.html_url,
                    utc_now(),
                    self._principal.user_id,
                    utc_now(),
                    note,
                ),
            )

    # ------------------------------------------------------------- ingest
    def ingest(
        self,
        project_id: str,
        repository: str | None,
        token: str | None,
        client: GitHubClient | None = None,
    ) -> IngestOutcome:
        """Fetch a repository and store it as sources.

        A token is optional: a public repository is read anonymously. The
        token, when given, is used here and never persisted.

        ``client`` is injectable so the whole path can be tested without a
        network.
        """
        require(self._principal, Capability.CONNECT_INTEGRATIONS, project_id)

        try:
            ref = parse_repo(repository)
        except GitHubError as exc:
            return IngestOutcome(ok=False, message=str(exc))

        try:
            github = client or GitHubClient(token)
            content = github.fetch(ref)
        except GitHubError as exc:
            return IngestOutcome(ok=False, message=str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected GitHub failure: %s", exc)
            return IngestOutcome(
                ok=False, message="TRACE could not read that repository."
            )

        groups = [
            ("commits", _commit_blocks(content)),
            ("pull requests", _pull_request_blocks(content)),
            ("issues", _issue_blocks(content)),
        ]

        added = 0
        notes: list[str] = []
        for kind, blocks in groups:
            if not blocks:
                notes.append(f"No {kind} were available to ingest.")
                continue

            full_text, segments = build_segments(blocks)
            if not segments:
                continue

            display_name = f"{ref.full_name} — {kind}"
            # Re-ingesting replaces the previous snapshot for this repository
            # and kind, rather than silently accumulating duplicates.
            self._replace_existing(project_id, display_name)

            self._sources.create(
                project_id=project_id,
                display_name=display_name,
                stored_name=f"github:{ref.full_name}:{kind.replace(' ', '_')}",
                file_type="github",
                file_size=len(full_text.encode("utf-8")),
                extraction_status=ExtractionStatus.EXTRACTED,
                extracted_text=full_text,
                extraction_notes=(
                    f"Ingested from {ref.html_url} on "
                    f"{utc_now()}. {len(segments)} {kind}."
                ),
                segments_json=json.dumps([s.to_dict() for s in segments]),
                char_count=len(full_text),
                origin=ORIGIN_GITHUB,
                origin_url=ref.html_url,
            )
            added += 1

        if added == 0:
            return IngestOutcome(
                ok=False,
                message=(
                    f"Nothing could be ingested from {ref.full_name}. The "
                    "repository may be empty, or the token may not grant read "
                    "access to its contents, issues and pull requests."
                ),
                notes=notes,
            )

        note = (
            f"{content.total} record(s) across {added} source(s): "
            f"{len(content.commits)} commits, "
            f"{len(content.pull_requests)} pull requests, "
            f"{len(content.issues)} issues."
        )
        self._record_link(project_id, ref, note)
        self._projects.touch(project_id)

        return IngestOutcome(
            ok=True,
            message=(
                f"Ingested {content.total} record(s) from {ref.full_name}. "
                "Run a reconstruction to include them in the handover."
            ),
            sources_added=added,
            records=content.total,
            notes=notes,
        )

    def _replace_existing(self, project_id: str, display_name: str) -> None:
        """Drop a previous snapshot, unless evidence still cites it.

        A cited source is left in place: breaking an evidence trail to make
        room for a newer snapshot would silently invalidate approved items.
        """
        rows = self._connection.execute(
            "SELECT id FROM sources WHERE project_id = ? AND display_name = ?"
            " AND origin = ?",
            (project_id, display_name, ORIGIN_GITHUB),
        ).fetchall()
        for row in rows:
            cited = self._connection.execute(
                "SELECT COUNT(*) AS c FROM evidence WHERE source_id = ?",
                (row["id"],),
            ).fetchone()["c"]
            if cited:
                continue
            with transaction(self._connection):
                self._connection.execute(
                    "DELETE FROM sources WHERE id = ?", (row["id"],)
                )
