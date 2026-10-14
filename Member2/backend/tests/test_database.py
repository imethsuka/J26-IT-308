"""Tests for backend.database: schema creation, migration, and CRUD via the service."""

import json
import sqlite3
import pytest
from pathlib import Path

from backend.database import get_connection, init_db
import backend.services.user_profile_service as ups


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tables(db_path: Path) -> set[str]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

def test_init_db_creates_all_tables(tmp_path):
    db = tmp_path / "caprlm.db"
    init_db(db)
    assert _tables(db) >= {"user_profile", "feedback_log", "analysis_log", "training_run"}


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "caprlm.db"
    init_db(db)
    init_db(db)  # second call must not raise or drop data
    assert _tables(db) >= {"user_profile", "feedback_log", "analysis_log", "training_run"}


def test_init_db_creates_parent_dirs(tmp_path):
    db = tmp_path / "nested" / "dir" / "caprlm.db"
    init_db(db)
    assert db.exists()


# ---------------------------------------------------------------------------
# Profile CRUD via user_profile_service
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    init_db(db)
    monkeypatch.setattr(ups, "_DB_PATH", db)


def test_profile_default_for_new_user():
    p = ups.load_profile("new_user")
    assert p["privacy_preference"] == "Medium"
    assert p["feedback_count"] == 0
    assert p["calibrated_threshold_override"] is None


def test_profile_create_and_load():
    p = ups.load_profile("u1")
    p["privacy_preference"] = "High"
    ups.save_profile(p)
    reloaded = ups.load_profile("u1")
    assert reloaded["privacy_preference"] == "High"


def test_profile_preferences_update():
    ups.update_preferences("u2", privacy_preference="Low", historical_protection_level="High")
    p = ups.load_profile("u2")
    assert p["privacy_preference"] == "Low"
    assert p["historical_protection_level"] == "High"


def test_save_profile_does_not_change_created_at():
    p = ups.load_profile("u3")
    ups.save_profile(p)
    created_first = ups.load_profile("u3")["created_at"]
    ups.save_profile(ups.load_profile("u3"))
    assert ups.load_profile("u3")["created_at"] == created_first


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def test_feedback_insertion():
    ups.record_feedback("fb1", "scr_001", "LOW", "MEDIUM")
    p = ups.load_profile("fb1")
    assert len(p["feedback_log"]) == 1
    entry = p["feedback_log"][0]
    assert entry["screenshot_id"] == "scr_001"
    assert entry["predicted_label"] == "LOW"
    assert entry["user_label"] == "MEDIUM"
    assert "timestamp" in entry


def test_feedback_count_increments():
    for i in range(3):
        ups.record_feedback("fb2", f"scr_{i:03d}", "LOW", "HIGH")
    assert ups.load_profile("fb2")["feedback_count"] == 3


def test_recalibration_threshold_activates():
    for i in range(ups.RECALIBRATION_THRESHOLD):
        ups.record_feedback("fb3", f"scr_{i:03d}", "LOW", "MEDIUM")
    override = ups.load_profile("fb3")["calibrated_threshold_override"]
    assert override is not None
    assert "medium" in override and "high" in override


def test_recalibration_below_threshold_is_null():
    for i in range(ups.RECALIBRATION_THRESHOLD - 1):
        ups.record_feedback("fb4", f"scr_{i:03d}", "LOW", "HIGH")
    assert ups.load_profile("fb4")["calibrated_threshold_override"] is None


# ---------------------------------------------------------------------------
# analysis_log direct insert
# ---------------------------------------------------------------------------

def test_analysis_log_insert(tmp_path, monkeypatch):
    db = tmp_path / "alog.db"
    init_db(db)
    monkeypatch.setattr(ups, "_DB_PATH", db)
    conn = get_connection(db)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO analysis_log
                    (screenshot_id, analyzed_at, risk_level, confidence,
                     model_type, protection_action, rule_risk_level, rule_confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("test.jpg", "2026-10-14T12:00:00Z",
                 "LOW", 0.85, "random_forest", "MONITOR", "LOW", 0.70),
            )
        rows = conn.execute("SELECT * FROM analysis_log").fetchall()
        assert len(rows) == 1
        assert rows[0]["screenshot_id"] == "test.jpg"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# training_run direct insert
# ---------------------------------------------------------------------------

def test_training_run_insert(tmp_path):
    db = tmp_path / "trun.db"
    init_db(db)
    conn = get_connection(db)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO training_run
                    (model_version, trained_at, dataset_rows,
                     label_low, label_medium, label_high, label_critical,
                     train_size, test_size, cv_folds,
                     cv_macro_f1_mean, cv_macro_f1_std,
                     test_macro_f1, test_accuracy)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                ("v1.0.0", "2026-10-14T12:00:00Z",
                 50, 38, 10, 2, 0,
                 40, 10, 2,
                 0.353, 0.075, 0.444, 0.80),
            )
        rows = conn.execute("SELECT * FROM training_run").fetchall()
        assert len(rows) == 1
        assert rows[0]["model_version"] == "v1.0.0"
        assert rows[0]["test_accuracy"] == pytest.approx(0.80)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Transaction rollback
# ---------------------------------------------------------------------------

def test_transaction_rollback_on_fk_violation(tmp_path):
    db = tmp_path / "fk.db"
    init_db(db)
    conn = get_connection(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    """
                    INSERT INTO feedback_log
                        (user_id, screenshot_id, predicted_label, user_label, submitted_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("nonexistent_user", "scr_999", "LOW", "HIGH", "2026-10-14T00:00:00Z"),
                )
        # After the failed transaction, the table must still be empty.
        rows = conn.execute("SELECT * FROM feedback_log").fetchall()
        assert rows == []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# JSON migration
# ---------------------------------------------------------------------------

def test_migration_from_json(tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile_data = {
        "user_id": "migrated_user",
        "privacy_preference": "High",
        "historical_protection_level": "Low",
        "feedback_count": 2,
        "feedback_log": [
            {
                "screenshot_id": "scr_m1",
                "predicted_label": "LOW",
                "user_label": "MEDIUM",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "screenshot_id": "scr_m2",
                "predicted_label": "MEDIUM",
                "user_label": "HIGH",
                "timestamp": "2026-01-02T00:00:00Z",
            },
        ],
        "calibrated_threshold_override": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    (profiles_dir / "migrated_user.json").write_text(
        json.dumps(profile_data), encoding="utf-8"
    )

    db = tmp_path / "mig.db"
    init_db(db, profiles_dir)

    conn = get_connection(db)
    try:
        row = conn.execute(
            "SELECT * FROM user_profile WHERE user_id = ?", ("migrated_user",)
        ).fetchone()
        assert row is not None
        assert row["privacy_preference"] == "High"

        fb = conn.execute(
            "SELECT * FROM feedback_log WHERE user_id = ?", ("migrated_user",)
        ).fetchall()
        assert len(fb) == 2
        assert fb[0]["screenshot_id"] == "scr_m1"
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "idem_user.json").write_text(
        json.dumps({
            "user_id": "idem_user",
            "privacy_preference": "Medium",
            "historical_protection_level": "Medium",
            "feedback_count": 0,
            "feedback_log": [],
            "calibrated_threshold_override": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    db = tmp_path / "idem.db"
    init_db(db, profiles_dir)
    init_db(db, profiles_dir)  # second migration must not duplicate

    conn = get_connection(db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM user_profile WHERE user_id = ?", ("idem_user",)
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_original_json_files_preserved_after_migration(tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    json_path = profiles_dir / "keep_user.json"
    json_path.write_text(
        json.dumps({
            "user_id": "keep_user",
            "privacy_preference": "Medium",
            "historical_protection_level": "Medium",
            "feedback_count": 0,
            "feedback_log": [],
            "calibrated_threshold_override": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    db = tmp_path / "keep.db"
    init_db(db, profiles_dir)
    assert json_path.exists(), "Original JSON file must not be deleted during migration"
