"""Explicit schema creation with a simple forward-only version counter.

The schema is created idempotently. ``schema_version`` records which
migration step the file is at, so future changes can be applied in order
without a migration framework.
"""

from __future__ import annotations

import sqlite3

#: Bump this and add a matching block in ``_MIGRATIONS`` for every change.
SCHEMA_VERSION = 2

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

#: Ordered migrations. Index 0 takes the database from version 0 to 1.
_MIGRATIONS = [_MIGRATION_1, _MIGRATION_2]


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
