"""SQLite schema and ordered migrations for the context store.

Design notes:
- ``context_records`` keeps the feature dict as JSON (flexible while the feature set is evolving)
  and the 512-d embedding as a raw float32 BLOB (2048 bytes), with a few promoted columns
  (``ts``, ``app``, ``domain``, ``decision``, ``label``) that queries and indexes need.
- ``decision`` is the model's action at capture time (save/discard). ``label`` is the ground-truth
  importance assigned LATER by the label-construction phase; it is NULL until then.
- ``label_version`` records which labelling rule and horizon produced ``label``, so stale labels can
  be found and rebuilt when the rule changes (both inputs change the label, so both are encoded).
- The "contexts older than N hours, unlabelled" query is ``label IS NULL AND ts < cutoff``. The
  partial index ``ix_context_unlabelled`` covers exactly that, staying small because it indexes
  only the unlabelled rows.
- All timestamps are INTEGER microseconds since the Unix epoch, UTC (see database.to_epoch_us).
"""

from __future__ import annotations

# Each migration is (version:int, description:str, sql:str). Append new ones; never edit an applied
# migration in place. database.migrate() applies pending versions in order, in a transaction.
MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial schema: context_records, model_versions, indexes",
        """
        CREATE TABLE model_versions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            version     TEXT    NOT NULL UNIQUE,
            algorithm   TEXT,
            created_at  INTEGER NOT NULL,          -- epoch microseconds, UTC
            is_active   INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
            metrics_json TEXT,
            notes       TEXT
        );

        CREATE TABLE context_records (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            INTEGER NOT NULL,          -- capture time, epoch microseconds, UTC
            app           TEXT,
            domain        TEXT,
            title         TEXT,
            decision      TEXT    NOT NULL CHECK (decision IN ('save', 'discard')),
            features_json TEXT    NOT NULL,
            embedding     BLOB,                      -- float32[embedding_dim], little-endian; NULL if no frame
            embedding_dim INTEGER,
            label         INTEGER,                   -- ground truth; NULL = unlabelled
            labeled_at    INTEGER,                   -- epoch microseconds when labelled; NULL otherwise
            model_version_id INTEGER REFERENCES model_versions(id),
            created_at    INTEGER NOT NULL           -- row insert time, epoch microseconds, UTC
        );

        -- Label-construction scan: unlabelled rows older than a cutoff, oldest first.
        CREATE INDEX ix_context_unlabelled ON context_records(ts) WHERE label IS NULL;

        -- General time-range scans and joins.
        CREATE INDEX ix_context_ts ON context_records(ts);
        CREATE INDEX ix_context_model_version ON context_records(model_version_id);

        -- At most one active model version.
        CREATE UNIQUE INDEX ix_model_active ON model_versions(is_active) WHERE is_active = 1;
        """,
    ),
    (
        2,
        "context_records.label_version: which labelling rule + horizon produced the label",
        """
        ALTER TABLE context_records ADD COLUMN label_version TEXT;
        """,
    ),
    (
        3,
        "context_records.drc_would_capture / drc_trigger: the frozen DRC rules' decision (baseline, not enforced)",
        """
        ALTER TABLE context_records ADD COLUMN drc_would_capture INTEGER CHECK (drc_would_capture IN (0, 1));
        ALTER TABLE context_records ADD COLUMN drc_trigger TEXT;
        """,
    ),
]

# Bookkeeping table for applied migrations. Created before any migration runs.
SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    description TEXT,
    applied_at  INTEGER NOT NULL                    -- epoch microseconds, UTC
);
"""

SCHEMA_VERSION = MIGRATIONS[-1][0]
