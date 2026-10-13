"""Unit tests for user_profile_service."""

import json
import pytest
from pathlib import Path

import backend.services.user_profile_service as ups


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_profiles(tmp_path, monkeypatch):
    """Redirect profile storage to a temporary directory for each test."""
    monkeypatch.setattr(ups, "_PROFILES_DIR", tmp_path / "profiles")


# ---------------------------------------------------------------------------
# load_profile
# ---------------------------------------------------------------------------

def test_load_profile_returns_default_for_new_user():
    profile = ups.load_profile("user_abc")
    assert profile["user_id"] == "user_abc"
    assert profile["privacy_preference"] == "Medium"
    assert profile["feedback_count"] == 0
    assert profile["calibrated_threshold_override"] is None


def test_load_profile_persists_across_calls():
    ups.save_profile(ups.load_profile("user_persist"))
    p1 = ups.load_profile("user_persist")
    p2 = ups.load_profile("user_persist")
    assert p1["created_at"] == p2["created_at"]


# ---------------------------------------------------------------------------
# save_profile
# ---------------------------------------------------------------------------

def test_save_and_reload_roundtrip():
    # Save a modified profile and verify it round-trips correctly
    profile = ups.load_profile("user_roundtrip")
    profile["privacy_preference"] = "High"
    ups.save_profile(profile)
    reloaded = ups.load_profile("user_roundtrip")
    assert reloaded["privacy_preference"] == "High"
    assert reloaded["user_id"] == "user_roundtrip"


def test_saved_profile_fields_are_preserved():
    profile = ups.load_profile("user_fields")
    profile["historical_protection_level"] = "Low"
    ups.save_profile(profile)
    reloaded = ups.load_profile("user_fields")
    assert reloaded["historical_protection_level"] == "Low"


# ---------------------------------------------------------------------------
# update_preferences
# ---------------------------------------------------------------------------

def test_update_preference_valid_value():
    ups.update_preferences("user_pref", privacy_preference="High")
    p = ups.load_profile("user_pref")
    assert p["privacy_preference"] == "High"


def test_update_preference_invalid_value_raises():
    with pytest.raises(ValueError, match="privacy_preference"):
        ups.update_preferences("user_bad", privacy_preference="Extreme")


def test_update_historical_protection_level():
    ups.update_preferences("user_hist", historical_protection_level="Low")
    p = ups.load_profile("user_hist")
    assert p["historical_protection_level"] == "Low"


# ---------------------------------------------------------------------------
# record_feedback
# ---------------------------------------------------------------------------

def test_record_feedback_appends_entry():
    ups.record_feedback("fb_user", "scr_001", "LOW", "MEDIUM")
    p = ups.load_profile("fb_user")
    assert p["feedback_count"] == 1
    assert p["feedback_log"][0]["user_label"] == "MEDIUM"


def test_record_feedback_invalid_user_label_raises():
    with pytest.raises(ValueError, match="user_label"):
        ups.record_feedback("fb_user2", "scr_002", "LOW", "EXTREME")


def test_record_feedback_invalid_predicted_label_raises():
    with pytest.raises(ValueError, match="predicted_label"):
        ups.record_feedback("fb_user3", "scr_003", "NONE", "LOW")


def test_recalibration_not_triggered_below_threshold():
    for i in range(ups.RECALIBRATION_THRESHOLD - 1):
        ups.record_feedback("cal_user", f"scr_{i:03d}", "LOW", "MEDIUM")
    p = ups.load_profile("cal_user")
    assert p["calibrated_threshold_override"] is None


def test_recalibration_triggered_at_threshold():
    for i in range(ups.RECALIBRATION_THRESHOLD):
        ups.record_feedback("cal_user2", f"scr_{i:03d}", "LOW", "MEDIUM")
    p = ups.load_profile("cal_user2")
    assert p["calibrated_threshold_override"] is not None
    assert "medium" in p["calibrated_threshold_override"]
    assert "high" in p["calibrated_threshold_override"]


def test_recalibration_lowers_threshold_when_user_consistently_upgrades():
    from backend.services.user_profile_service import _DEFAULT_THRESHOLDS
    for i in range(ups.RECALIBRATION_THRESHOLD):
        ups.record_feedback("cal_user3", f"scr_{i:03d}", "LOW", "HIGH")
    p = ups.load_profile("cal_user3")
    override = p["calibrated_threshold_override"]
    assert override["high"] < _DEFAULT_THRESHOLDS["high"]


# ---------------------------------------------------------------------------
# profile_summary
# ---------------------------------------------------------------------------

def test_profile_summary_has_required_keys():
    profile = ups.load_profile("sum_user")
    summary = ups.profile_summary(profile)
    required = {
        "user_id", "privacy_preference", "historical_protection_level",
        "feedback_count", "calibrated_threshold_override",
        "recalibration_threshold", "recalibration_active", "updated_at",
    }
    assert required.issubset(summary.keys())


def test_profile_summary_no_raw_feedback_log():
    profile = ups.load_profile("sum_user2")
    summary = ups.profile_summary(profile)
    assert "feedback_log" not in summary


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------

def test_user_id_path_traversal_is_stripped():
    # Path traversal chars are stripped; the result contains no slashes or dots
    cleaned = ups._sanitise_user_id("../../etc/passwd")
    assert ".." not in cleaned
    assert "/" not in cleaned
    assert "\\" not in cleaned


def test_user_id_entirely_special_chars_raises():
    # All-special-char input produces empty string → ValueError
    with pytest.raises(ValueError):
        ups._sanitise_user_id("...")


def test_valid_user_id_with_hyphens_accepted():
    cleaned = ups._sanitise_user_id("user-123-abc")
    assert cleaned == "user-123-abc"
