"""Read-only GitHub ingestion.

What this collects, and what it deliberately does not
-----------------------------------------------------
TRACE needs enough to reconstruct *why* the project is where it is, and no
more. For each record it keeps the identifier, the title or message, the body,
the author's login, the date, and the permanent URL.

It does not fetch diffs or file contents, and it does not keep author email
addresses, avatars, or any other profile data the API happens to return. A
handover does not need them, and collecting them would mean holding personal
data this product has no use for.

Authentication is optional. A public repository is read without any token,
because GitHub allows that and requiring one would be friction for no
security benefit. A private repository needs a fine-grained personal access
token, supplied for a single fetch.

A token is never written to the database or to a log -- re-syncing asks for it
again. A stored token would be a credential at rest in a file the operator may
not be treating as a secret.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "TRACE-Handover-System"
TIMEOUT_SECONDS = 30

#: Caps per record type. A handover is built from recent history, not an
#: archive, and an unbounded fetch would be slow and collect far more than is
#: needed.
MAX_COMMITS = 40
MAX_PULL_REQUESTS = 30
MAX_ISSUES = 30

#: Bodies are truncated before storage. Long issue threads add volume without
#: adding evidence, and the excerpt that supports a claim is always short.
MAX_BODY_CHARS = 2000

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class GitHubError(RuntimeError):
    """A failure worth showing to the user as-is."""


class GitHubAuthError(GitHubError):
    """The token was missing, rejected, or lacks access to the repository."""


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


def parse_repo(value: str | None) -> RepoRef:
    """Accept ``owner/repo`` or a github.com URL. Raises ``GitHubError``."""
    raw = str(value or "").strip()
    if not raw:
        raise GitHubError("Enter a repository as owner/name.")

    if "github.com" in raw:
        path = urllib.parse.urlparse(
            raw if "//" in raw else f"https://{raw}"
        ).path
        parts = [p for p in path.split("/") if p]
    else:
        parts = [p for p in raw.split("/") if p]

    if len(parts) < 2:
        raise GitHubError("Enter a repository as owner/name, for example acme/app.")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not _REPO_PATTERN.match(owner) or not _REPO_PATTERN.match(repo):
        raise GitHubError("That does not look like a valid repository name.")
    return RepoRef(owner=owner, repo=repo)


def _trim(text: str | None, limit: int = MAX_BODY_CHARS) -> str:
    cleaned = str(text or "").replace("\r\n", "\n").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n[truncated]"


def _login(payload: dict | None) -> str:
    """The author's login only -- never the email or any profile field."""
    if not isinstance(payload, dict):
        return "unknown"
    return str(payload.get("login") or "unknown")


@dataclass
class RepositoryContent:
    """Everything fetched for one repository, already minimised."""

    ref: RepoRef
    description: str = ""
    default_branch: str = ""
    commits: list[dict[str, Any]] = field(default_factory=list)
    pull_requests: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.commits) + len(self.pull_requests) + len(self.issues)


class GitHubClient:
    """A small read-only client. Only the four endpoints TRACE needs.

    With no token it reads public repositories anonymously, which GitHub
    permits at a lower rate limit. That is enough for one ingest and means a
    public repository needs no setup at all.
    """

    def __init__(self, token: str | None = None, api_root: str = API_ROOT) -> None:
        self._token = str(token or "").strip() or None
        self._api_root = api_root.rstrip("/")

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    # ------------------------------------------------------------- request
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._api_root}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise self._translate(exc) from exc
        except urllib.error.URLError as exc:
            logger.warning("GitHub request failed: %s", exc)
            raise GitHubError(
                "TRACE could not reach GitHub. Check the network connection."
            ) from exc
        except (ValueError, TimeoutError) as exc:
            logger.warning("GitHub reply could not be read: %s", exc)
            raise GitHubError("GitHub returned a reply TRACE could not read.") from exc

    def _translate(self, exc: urllib.error.HTTPError) -> GitHubError:
        """Map an HTTP status to a message the user can act on."""
        status = exc.code
        anonymous = not self.authenticated

        if status == 401:
            return GitHubAuthError(
                "GitHub rejected the token. Check it is correct and has not "
                "expired."
            )
        if status == 403:
            # 403 is also how GitHub reports a spent rate limit.
            try:
                remaining = exc.headers.get("X-RateLimit-Remaining")
            except AttributeError:  # pragma: no cover - headers absent
                remaining = None
            if remaining == "0":
                return GitHubError(
                    "The GitHub API rate limit is used up"
                    + (
                        ". Anonymous requests have a low limit — adding a "
                        "token raises it considerably."
                        if anonymous
                        else " for this token. Wait and try again."
                    )
                )
            return GitHubAuthError(
                "The token does not grant access to that repository. A "
                "fine-grained token must list the repository and allow "
                "read access to its contents, issues and pull requests."
            )
        if status == 404:
            if anonymous:
                return GitHubAuthError(
                    "That repository was not found. If it is private, add a "
                    "fine-grained access token that can read it."
                )
            return GitHubAuthError(
                "That repository was not found. For a private repository this "
                "also means the token cannot see it."
            )
        if status >= 500:
            return GitHubError("GitHub reported a server error. Try again shortly.")
        return GitHubError(f"GitHub refused the request (HTTP {status}).")

    # --------------------------------------------------------------- fetch
    def fetch(self, ref: RepoRef) -> RepositoryContent:
        """Fetch the repository's recent history, already minimised."""
        meta = self._get(f"/repos/{ref.owner}/{ref.repo}")
        content = RepositoryContent(
            ref=ref,
            description=_trim(meta.get("description"), 400),
            default_branch=str(meta.get("default_branch") or ""),
        )

        for entry in self._get(
            f"/repos/{ref.owner}/{ref.repo}/commits",
            {"per_page": MAX_COMMITS},
        ):
            commit = entry.get("commit") or {}
            author = commit.get("author") or {}
            content.commits.append(
                {
                    "sha": str(entry.get("sha") or "")[:7],
                    "message": _trim(commit.get("message")),
                    "author": _login(entry.get("author")),
                    "date": str(author.get("date") or "")[:10],
                    "url": str(entry.get("html_url") or ""),
                }
            )

        for entry in self._get(
            f"/repos/{ref.owner}/{ref.repo}/pulls",
            {"state": "all", "per_page": MAX_PULL_REQUESTS, "sort": "updated",
             "direction": "desc"},
        ):
            content.pull_requests.append(
                {
                    "number": entry.get("number"),
                    "title": _trim(entry.get("title"), 300),
                    "body": _trim(entry.get("body")),
                    "state": "merged" if entry.get("merged_at") else str(entry.get("state") or ""),
                    "author": _login(entry.get("user")),
                    "date": str(entry.get("created_at") or "")[:10],
                    "url": str(entry.get("html_url") or ""),
                }
            )

        for entry in self._get(
            f"/repos/{ref.owner}/{ref.repo}/issues",
            {"state": "all", "per_page": MAX_ISSUES, "sort": "updated",
             "direction": "desc"},
        ):
            # The issues endpoint also returns pull requests; they are already
            # covered above, so skip them rather than storing each one twice.
            if entry.get("pull_request"):
                continue
            content.issues.append(
                {
                    "number": entry.get("number"),
                    "title": _trim(entry.get("title"), 300),
                    "body": _trim(entry.get("body")),
                    "state": str(entry.get("state") or ""),
                    "labels": [
                        str(label.get("name"))
                        for label in (entry.get("labels") or [])
                        if isinstance(label, dict) and label.get("name")
                    ][:8],
                    "author": _login(entry.get("user")),
                    "date": str(entry.get("created_at") or "")[:10],
                    "url": str(entry.get("html_url") or ""),
                }
            )

        return content
