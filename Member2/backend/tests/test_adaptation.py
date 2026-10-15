"""
Tests for continual learning / adaptation features.

ADAPT-01  Poisoning guard: >MAX_FEEDBACK_PER_SCREENSHOT for same screenshot raises ValueError.
ADAPT-02  Different screenshot IDs are accepted up to the per-screenshot limit independently.
ADAPT-03  Windowed adaptation: only last RECALIBRATION_WINDOW records used (not all history).
ADAPT-04  Adaptation log row written when thresholds change after recalibration.
ADAPT-05  No adaptation log row written when thresholds do not change.
ADAPT-06  get_adaptation_history returns rows newest-first.
ADAPT-07  Rollback restores previous threshold from adaptation_log.
ADAPT-08  Rollback with no prior history clears calibration (resets to None).
ADAPT-09  Rollback writes a new adaptation_log row with rollback_reason.
ADAPT-10  database.py adaptation_log table has expected columns.
ADAPT-11  Feedback count includes all records; recalibration uses windowed subset.
"""

from __future__ import annotations

import pytest

from backend.database import get_connection, init_db
import backend.services.user_profile_service as ups


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "adapt.db"
    init_db(path)
    original = ups._DB_PATH
    ups._DB_PATH = path
    yield path
    ups._DB_PATH = original


# ---------------------------------------------------------------------------
# ADAPT-01  Poisoning guard
# ---------------------------------------------------------------------------

def test_adapt01_poisoning_guard_rejects_duplicate_screenshot(db):
    """More than MAX_FEEDBACK_PER_SCREENSHOT records for the same screenshot raises ValueError."""
    limit = ups.MAX_FEEDBACK_PER_SCREENSHOT
    for i in range(limit):
        ups.record_feedback("user_a", "scr_001", "LOW", "MEDIUM")

    with pytest.raises(ValueError, match="Feedback limit reached"):
        ups.record_feedback("user_a", "scr_001", "LOW", "MEDIUM")


# ---------------------------------------------------------------------------
# ADAPT-02  Independent screenshot IDs are not blocked
# ---------------------------------------------------------------------------

def test_adapt02_different_screenshot_ids_accepted(db):
    limit = ups.MAX_FEEDBACK_PER_SCREENSHOT
    for i in range(limit * 3):
        ups.record_feedback("user_b", f"scr_{i:03d}", "LOW", "MEDIUM")
    profile = ups.load_profile("user_b")
    assert profile["feedback_count"] == limit * 3


# ---------------------------------------------------------------------------
# ADAPT-03  Only last RECALIBRATION_WINDOW records used
# ---------------------------------------------------------------------------

def test_adapt03_windowed_recalibration(db):
    """
    Push RECALIBRATION_WINDOW + 5 records. The first 5 are all downgrades
    (LOW→predicted MEDIUM); the rest are all upgrades (predicted LOW, user MEDIUM).
    If we used ALL history the net would be lower; using the window the net
    would be RECALIBRATION_WINDOW upgrades minus 0 (downgrades outside window ignored).
    """
    from backend.services.risk_prediction import DEFAULT_MEDIUM_THRESHOLDS
    # 5 downgrades first (predicted MEDIUM, user says LOW) — will fall outside window
    for i in range(5):
        ups.record_feedback("user_c", f"early_{i:03d}", "MEDIUM", "LOW")
    # Then RECALIBRATION_WINDOW upgrades — predicted LOW, user says MEDIUM
    window = ups.RECALIBRATION_WINDOW
    threshold = ups.RECALIBRATION_THRESHOLD
    total_upgrades = max(threshold, window)
    for i in range(total_upgrades):
        ups.record_feedback("user_c", f"later_{i:03d}", "LOW", "MEDIUM")

    profile = ups.load_profile("user_c")
    override = profile["calibrated_threshold_override"]
    assert override is not None
    # Windowed result should show decrease in threshold (upgrades dominate window)
    assert override["medium"] < DEFAULT_MEDIUM_THRESHOLDS["medium"], (
        f"Expected threshold decrease due to upgrades dominating window; got {override}"
    )


# ---------------------------------------------------------------------------
# ADAPT-04  Adaptation log written when thresholds change
# ---------------------------------------------------------------------------

def test_adapt04_adaptation_log_written_on_threshold_change(db):
    threshold = ups.RECALIBRATION_THRESHOLD
    for i in range(threshold):
        ups.record_feedback("user_d", f"scr_{i:03d}", "LOW", "MEDIUM")

    history = ups.get_adaptation_history("user_d")
    assert len(history) >= 1
    row = history[0]
    assert row["new_medium_threshold"] is not None
    assert row["trigger_count"] == threshold


# ---------------------------------------------------------------------------
# ADAPT-05  No adaptation log row when thresholds unchanged
# ---------------------------------------------------------------------------

def test_adapt05_no_adaptation_log_before_threshold_hit(db):
    """Fewer than RECALIBRATION_THRESHOLD records → no adaptation event."""
    n = ups.RECALIBRATION_THRESHOLD - 1
    for i in range(n):
        ups.record_feedback("user_e", f"scr_{i:03d}", "LOW", "MEDIUM")

    history = ups.get_adaptation_history("user_e")
    assert history == [], f"Expected no adaptation log before threshold; got {history}"


# ---------------------------------------------------------------------------
# ADAPT-06  get_adaptation_history newest-first
# ---------------------------------------------------------------------------

def test_adapt06_adaptation_history_newest_first(db):
    threshold = ups.RECALIBRATION_THRESHOLD
    # First batch: 10 upgrades
    for i in range(threshold):
        ups.record_feedback("user_f", f"batch1_{i:03d}", "LOW", "MEDIUM")
    # Second batch: 10 more upgrades (different screenshot IDs)
    for i in range(threshold):
        ups.record_feedback("user_f", f"batch2_{i:03d}", "LOW", "MEDIUM")

    history = ups.get_adaptation_history("user_f", limit=10)
    assert len(history) >= 2
    # Newest first: trigger_count of first row >= second row
    assert history[0]["trigger_count"] >= history[1]["trigger_count"]


# ---------------------------------------------------------------------------
# ADAPT-07  Rollback restores previous threshold
# ---------------------------------------------------------------------------

def test_adapt07_rollback_restores_previous_threshold(db):
    threshold = ups.RECALIBRATION_THRESHOLD

    # First recalibration event (upgrades → lower threshold)
    for i in range(threshold):
        ups.record_feedback("user_g", f"r1_{i:03d}", "LOW", "MEDIUM")

    # Second batch: more upgrades to trigger additional events
    for i in range(threshold):
        ups.record_feedback("user_g", f"r2_{i:03d}", "LOW", "MEDIUM")

    # Read the current adaptation history to know what ONE STEP BACK should be
    history = ups.get_adaptation_history("user_g")
    assert len(history) >= 2, "Need at least 2 adaptation events to test rollback"

    # rollback restores second-most-recent event's thresholds
    expected_medium = history[1]["new_medium_threshold"]

    profile_rolled = ups.rollback_calibration("user_g")
    rolled_override = profile_rolled["calibrated_threshold_override"]

    assert rolled_override is not None
    assert rolled_override["medium"] == pytest.approx(expected_medium, abs=1e-6)


# ---------------------------------------------------------------------------
# ADAPT-08  Rollback with single event clears calibration
# ---------------------------------------------------------------------------

def test_adapt08_rollback_single_event_clears_calibration(db):
    threshold = ups.RECALIBRATION_THRESHOLD
    for i in range(threshold):
        ups.record_feedback("user_h", f"scr_{i:03d}", "LOW", "MEDIUM")

    # Only one adaptation event; rolling back should clear override
    profile = ups.rollback_calibration("user_h")
    assert profile["calibrated_threshold_override"] is None


# ---------------------------------------------------------------------------
# ADAPT-09  Rollback writes adaptation_log row
# ---------------------------------------------------------------------------

def test_adapt09_rollback_writes_log_row(db):
    threshold = ups.RECALIBRATION_THRESHOLD
    for i in range(threshold):
        ups.record_feedback("user_i", f"scr_{i:03d}", "LOW", "MEDIUM")

    before_count = len(ups.get_adaptation_history("user_i"))
    ups.rollback_calibration("user_i", reason="test_rollback")
    after_count  = len(ups.get_adaptation_history("user_i"))

    assert after_count == before_count + 1
    latest = ups.get_adaptation_history("user_i")[0]
    assert latest["rollback_reason"] == "test_rollback"


# ---------------------------------------------------------------------------
# ADAPT-10  adaptation_log table has expected columns
# ---------------------------------------------------------------------------

def test_adapt10_adaptation_log_schema(db):
    conn = get_connection(db)
    try:
        cursor = conn.execute("PRAGMA table_info(adaptation_log)")
        columns = {row["name"] for row in cursor.fetchall()}
    finally:
        conn.close()

    required = {
        "id", "user_id", "applied_at", "trigger_count",
        "feedback_window_size",
        "old_medium_threshold", "old_high_threshold",
        "new_medium_threshold", "new_high_threshold",
        "rollback_reason",
    }
    assert required.issubset(columns), f"Missing columns: {required - columns}"


# ---------------------------------------------------------------------------
# ADAPT-11  feedback_count is total; window may be smaller
# ---------------------------------------------------------------------------

def test_adapt11_feedback_count_is_total_not_window(db):
    window = ups.RECALIBRATION_WINDOW
    threshold = ups.RECALIBRATION_THRESHOLD
    total = window + threshold
    for i in range(total):
        ups.record_feedback("user_j", f"scr_{i:04d}", "LOW", "MEDIUM")

    profile = ups.load_profile("user_j")
    assert profile["feedback_count"] == total, (
        f"feedback_count must be total records, not windowed: expected {total}, got {profile['feedback_count']}"
    )


# ---------------------------------------------------------------------------
# ADAPT-12  screenshot_id whitespace stripped — poisoning guard bypass prevented
# ---------------------------------------------------------------------------

def test_adapt12_screenshot_id_whitespace_stripped(db):
    """
    ADAPT-12 — record_feedback must strip leading/trailing whitespace from
    screenshot_id before comparing against the poisoning guard limit.

    Without stripping, a caller could submit 'scr_001', 'scr_001 ', ' scr_001'
    etc. as distinct IDs and accumulate more than MAX_FEEDBACK_PER_SCREENSHOT
    records for the same screenshot.

    After the fix, all three spellings are normalised to 'scr_001' and the
    limit is correctly enforced.
    """
    limit = ups.MAX_FEEDBACK_PER_SCREENSHOT

    # Fill to the limit using the canonical ID
    for i in range(limit):
        ups.record_feedback("user_ws", "scr_ws_001", "LOW", "MEDIUM")

    # Now attempt with trailing whitespace — must be rejected just like the plain ID
    with pytest.raises(ValueError, match="Feedback limit reached"):
        ups.record_feedback("user_ws", "scr_ws_001 ", "LOW", "MEDIUM")

    # Leading whitespace also rejected
    with pytest.raises(ValueError, match="Feedback limit reached"):
        ups.record_feedback("user_ws", "  scr_ws_001", "LOW", "MEDIUM")
