"""
SQLite persistence layer for CAPRLM.

Database file: backend/data/caprlm.db

Tables
------
user_profile  – per-user privacy preferences and adaptive threshold calibration
feedback_log  – user corrections to model risk predictions
analysis_log  – record of every /analyze call (no raw OCR text stored)
training_run  – record of every /train call with evaluation metrics
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_profile (
    user_id                     TEXT    PRIMARY KEY,
    privacy_preference          TEXT    NOT NULL DEFAULT 'Medium',
    historical_protection_level TEXT    NOT NULL DEFAULT 'Medium',
    feedback_count              INTEGER NOT NULL DEFAULT 0,
    calibrated_threshold_medium REAL,
    calibrated_threshold_high   REAL,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT    NOT NULL REFERENCES user_profile(user_id),
    screenshot_id    TEXT    NOT NULL,
    predicted_label  TEXT    NOT NULL,
    user_label       TEXT    NOT NULL,
    submitted_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_log (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    screenshot_id               TEXT    NOT NULL,
    analyzed_at                 TEXT    NOT NULL,
    user_id                     TEXT,
    application_context         TEXT,
    privacy_preference          TEXT,
    historical_protection_level TEXT,
    ocr_text_length             INTEGER,
    email_count                 INTEGER NOT NULL DEFAULT 0,
    phone_count                 INTEGER NOT NULL DEFAULT 0,
    url_count                   INTEGER NOT NULL DEFAULT 0,
    ip_count                    INTEGER NOT NULL DEFAULT 0,
    date_count                  INTEGER NOT NULL DEFAULT 0,
    windows_path_count          INTEGER NOT NULL DEFAULT 0,
    sensitive_entity_count      INTEGER NOT NULL DEFAULT 0,
    risk_level                  TEXT,
    confidence                  REAL,
    model_type                  TEXT,
    protection_action           TEXT,
    rule_risk_level             TEXT,
    rule_confidence             REAL
);

CREATE TABLE IF NOT EXISTS training_run (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version       TEXT    NOT NULL,
    trained_at          TEXT    NOT NULL,
    dataset_rows        INTEGER,
    label_low           INTEGER,
    label_medium        INTEGER,
    label_high          INTEGER,
    label_critical      INTEGER,
    train_size          INTEGER,
    test_size           INTEGER,
    cv_folds            INTEGER,
    cv_macro_f1_mean    REAL,
    cv_macro_f1_std     REAL,
    test_macro_f1       REAL,
    test_accuracy       REAL
);
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection(db_path: Path) -> sqlite3.Connection:
    """
    Open and return a SQLite connection.

    Uses WAL journal mode for better read concurrency and enables foreign-key
    enforcement.  The caller is responsible for closing the connection.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db(db_path: Path, profiles_dir: Path | None = None) -> None:
    """
    Create all four tables if they do not already exist, then migrate any
    JSON profile files found in profiles_dir.  Safe to call multiple times.
    """
    conn = get_connection(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        if profiles_dir is not None:
            _migrate_json_profiles(conn, profiles_dir)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# JSON migration (one-time, non-destructive)
# ---------------------------------------------------------------------------

def _migrate_json_profiles(conn: sqlite3.Connection, profiles_dir: Path) -> None:
    """
    Import existing per-user JSON profile files into the database.
    Only inserts profiles that are not already present (idempotent).
    Original JSON files are preserved and never modified.
    """
    if not profiles_dir.exists():
        return

    for json_path in sorted(profiles_dir.glob("*.json")):
        try:
            with json_path.open(encoding="utf-8") as fh:
                profile = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        user_id = str(profile.get("user_id", "")).strip()
        if not user_id:
            continue

        existing = conn.execute(
            "SELECT 1 FROM user_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if existing:
            continue

        override = profile.get("calibrated_threshold_override")
        cal_medium = override.get("medium") if isinstance(override, dict) else None
        cal_high = override.get("high") if isinstance(override, dict) else None

        with conn:
            conn.execute(
                """
                INSERT INTO user_profile
                    (user_id, privacy_preference, historical_protection_level,
                     feedback_count, calibrated_threshold_medium,
                     calibrated_threshold_high, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    profile.get("privacy_preference", "Medium"),
                    profile.get("historical_protection_level", "Medium"),
                    int(profile.get("feedback_count", 0)),
                    cal_medium,
                    cal_high,
                    profile.get("created_at", ""),
                    profile.get("updated_at", ""),
                ),
            )
            for entry in profile.get("feedback_log", []):
                conn.execute(
                    """
                    INSERT INTO feedback_log
                        (user_id, screenshot_id, predicted_label,
                         user_label, submitted_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        str(entry.get("screenshot_id", "")),
                        str(entry.get("predicted_label", "")),
                        str(entry.get("user_label", "")),
                        str(entry.get("timestamp", "")),
                    ),
                )
