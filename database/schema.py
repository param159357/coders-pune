"""Explicit schema creation with a simple forward-only version counter.

The schema is created idempotently. ``schema_version`` records which
migration step the file is at, so future changes can be applied in order
without a migration framework.
"""

from __future__ import annotations

import sqlite3

#: Bump this and add a matching block in ``_MIGRATIONS`` for every change.
SCHEMA_VERSION = 7

_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS projects (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT,
    team_label   TEXT,
    is_demo      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    stored_name       TEXT NOT NULL,
    file_type         TEXT NOT NULL,
    file_size         INTEGER NOT NULL,
    uploaded_at       TEXT NOT NULL,
    extraction_status TEXT NOT NULL,
    extraction_error  TEXT,
    extracted_text    TEXT,
    char_count        INTEGER NOT NULL DEFAULT 0,
    page_count        INTEGER,
    content_hash      TEXT,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sources_project ON sources (project_id);

CREATE TABLE IF NOT EXISTS reconstructions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    model_provider  TEXT,
    model_name      TEXT,
    status          TEXT NOT NULL,
    raw_output      TEXT,
    validated_json  TEXT,
    processing_notes TEXT,
    source_count    INTEGER NOT NULL DEFAULT 0,
    is_demo_preview INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reconstructions_project
    ON reconstructions (project_id);

CREATE TABLE IF NOT EXISTS handover_items (
    id                TEXT PRIMARY KEY,
    reconstruction_id TEXT NOT NULL,
    category          TEXT NOT NULL,
    text              TEXT NOT NULL,
    review_status     TEXT NOT NULL,
    evidence_status   TEXT NOT NULL,
    owner             TEXT,
    due_date          TEXT,
    reviewer_note     TEXT,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (reconstruction_id) REFERENCES reconstructions (id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_items_reconstruction
    ON handover_items (reconstruction_id);

CREATE TABLE IF NOT EXISTS evidence (
    id               TEXT PRIMARY KEY,
    handover_item_id TEXT NOT NULL,
    source_id        TEXT NOT NULL,
    location_label   TEXT,
    excerpt          TEXT NOT NULL,
    start_offset     INTEGER,
    end_offset       INTEGER,
    support_note     TEXT,
    verified         INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (handover_item_id) REFERENCES handover_items (id)
        ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_evidence_item ON evidence (handover_item_id);

CREATE TABLE IF NOT EXISTS review_events (
    id               TEXT PRIMARY KEY,
    handover_item_id TEXT NOT NULL,
    action           TEXT NOT NULL,
    previous_value   TEXT,
    new_value        TEXT,
    note             TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (handover_item_id) REFERENCES handover_items (id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_review_events_item
    ON review_events (handover_item_id);
"""

# Version 2 stores the located segments produced during extraction (page or
# paragraph level) so evidence can be checked and displayed against the exact
# region of the source it came from.
_MIGRATION_2 = """
ALTER TABLE sources ADD COLUMN segments_json TEXT;
ALTER TABLE sources ADD COLUMN extraction_notes TEXT;
"""

# Version 3 introduces accounts and per-project roles. Access control is
# enforced in the service layer against these rows (see trace_app.security),
# not in the interface.
#
# projects.owner_id is nullable because projects created before this migration
# have no owner. Such a project has no members, so it is listed to nobody -- it
# is not silently adopted by whoever signs in first, which would be a hole.
_MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL,
    display_name  TEXT,
    password_hash TEXT NOT NULL,
    is_demo       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
    ON users (email COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS project_members (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL,
    added_at   TEXT NOT NULL,
    added_by   TEXT,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_members_project_user
    ON project_members (project_id, user_id);
CREATE INDEX IF NOT EXISTS idx_members_user ON project_members (user_id);

ALTER TABLE projects ADD COLUMN owner_id TEXT REFERENCES users (id);
"""

# Version 4 lets a source come from somewhere other than an upload. An
# ingested commit, pull request or issue is stored as an ordinary source row,
# so evidence verification, review and export all work on it unchanged.
#
# No access token is stored. The token is used for one fetch and discarded;
# re-syncing asks for it again. A stored token would be a credential at rest
# in a file the operator may not be treating as a secret.
_MIGRATION_4 = """
ALTER TABLE sources ADD COLUMN origin TEXT NOT NULL DEFAULT 'upload';
ALTER TABLE sources ADD COLUMN origin_url TEXT;

CREATE TABLE IF NOT EXISTS repository_links (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    owner          TEXT NOT NULL,
    repo           TEXT NOT NULL,
    html_url       TEXT,
    connected_at   TEXT NOT NULL,
    connected_by   TEXT,
    last_synced_at TEXT,
    last_sync_note TEXT,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_repo_links_project_repo
    ON repository_links (project_id, owner, repo);
"""

# Version 5 lets a piece of evidence carry the public address of the thing it
# quotes, so a citation of a commit or issue is clickable in the interface and
# in the exported report. Null for uploaded documents, which have no address.
_MIGRATION_5 = """
ALTER TABLE evidence ADD COLUMN source_url TEXT;
"""

# Version 6 records evidence-backed relationships between handover items --
# which blocker holds up which task, which decision caused which work, what a
# dependency is waiting on.
#
# A relationship carries its own supporting quotation and a verified flag,
# checked against the real source exactly as an item's evidence is. Only
# verified relationships are drawn: a line between two boxes asserts a causal
# claim, and an unsupported one would be a decorative guess.
_MIGRATION_6 = """
CREATE TABLE IF NOT EXISTS item_relationships (
    id                TEXT PRIMARY KEY,
    reconstruction_id TEXT NOT NULL,
    from_item_id      TEXT NOT NULL,
    to_item_id        TEXT NOT NULL,
    relation          TEXT NOT NULL,
    source_id         TEXT,
    location_label    TEXT,
    excerpt           TEXT,
    source_url        TEXT,
    verified          INTEGER NOT NULL DEFAULT 0,
    reason            TEXT,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (reconstruction_id) REFERENCES reconstructions (id)
        ON DELETE CASCADE,
    FOREIGN KEY (from_item_id) REFERENCES handover_items (id) ON DELETE CASCADE,
    FOREIGN KEY (to_item_id) REFERENCES handover_items (id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_relationships_reconstruction
    ON item_relationships (reconstruction_id);
"""

# Version 7 records what a reconstruction was computed from, so the same work
# is not paid for twice. The fingerprint covers the exact set of source
# contents, the model, and the prompt -- change any of them and the previous
# answer no longer applies.
_MIGRATION_7 = """
ALTER TABLE reconstructions ADD COLUMN input_fingerprint TEXT;

CREATE INDEX IF NOT EXISTS idx_reconstructions_fingerprint
    ON reconstructions (project_id, input_fingerprint);
"""

#: Ordered migrations. Index 0 takes the database from version 0 to 1.
_MIGRATIONS = [
    _MIGRATION_1, _MIGRATION_2, _MIGRATION_3, _MIGRATION_4, _MIGRATION_5,
    _MIGRATION_6, _MIGRATION_7,
]


def _current_version(connection: sqlite3.Connection) -> int:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    row = connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


def initialise_database(connection: sqlite3.Connection) -> int:
    """Apply any outstanding migrations. Returns the resulting version."""
    version = _current_version(connection)
    for index in range(version, len(_MIGRATIONS)):
        connection.executescript(_MIGRATIONS[index])
        connection.execute(
            "INSERT INTO schema_version (version, applied_at)"
            " VALUES (?, datetime('now'))",
            (index + 1,),
        )
    connection.commit()
    return _current_version(connection)
