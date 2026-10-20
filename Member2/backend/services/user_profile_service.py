"""
User profile service — persistent per-user privacy preferences and feedback.

Storage
-------
Profiles are stored in the SQLite database at _DB_PATH.
The database is managed by backend.database; call init_db() at startup.

Profile schema (dict returned by load_profile)
----------------------------------------------
{
  "user_id": str,
  "privacy_preference": "Low" | "Medium" | "High",
  "historical_protection_level": "Low" | "Medium" | "High",
  "feedback_count": int,
  "feedback_log": [
    {
      "screenshot_id": str,
      "predicted_label": str,
      "user_label": str,
      "timestamp": ISO-8601 str
    }
  ],
  "calibrated_threshold_override": null | {"medium": float, "high": float},
  "created_at": ISO-8601 str,
  "updated_at": ISO-8601 str
}

Security notes
--------------
- user_id is sanitised before use as a DB key (alphanumeric + '-' only).
- Profile data never contains raw OCR text or screenshot content.
- The legacy profiles directory (JSON) should be excluded from version control.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from backend.database import get_connection
from backend.services.risk_prediction import DEFAULT_MEDIUM_THRESHOLDS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Monkeypatchable in tests — set to a temporary path to isolate test state.
_DB_PATH: Path = Path(__file__).parent.parent / "data" / "caprlm.db"

# Legacy JSON profiles directory; retained as a reference for init_db migration.
_PROFILES_DIR: Path = Path(__file__).parent.parent / "data" / "profiles"

_VALID_RISK_LABELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
_VALID_PREFERENCE_VALUES = frozenset({"Low", "Medium", "High"})
RECALIBRATION_THRESHOLD: int = 10

# Maximum number of most-recent feedback records used for recalibration.
# Older records are ignored so that the model responds to recent behaviour.
RECALIBRATION_WINDOW: int = 50

# Anti-poisoning: a user may submit at most this many feedback records for the
# same screenshot_id before further feedback for that screenshot is rejected.
MAX_FEEDBACK_PER_SCREENSHOT: int = 3

_DEFAULT_THRESHOLDS: dict[str, float] = DEFAULT_MEDIUM_THRESHOLDS.copy()
_THRESHOLD_STEP: float = 0.02


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitise_user_id(user_id: str) -> str:
    """Allow only alphanumeric characters and hyphens; raise on invalid input."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "", user_id)
    if not cleaned:
        raise ValueError("user_id must contain at least one alphanumeric character.")
    if len(cleaned) > 128:
        raise ValueError("user_id must not exceed 128 characters.")
    return cleaned


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def load_profile(user_id: str) -> dict[str, Any]:
    """Load an existing profile or return a default one (not persisted)."""
    cleaned = _sanitise_user_id(user_id)
    conn = get_connection(_DB_PATH)
    try:
        row = conn.execute(
            "SELECT * FROM user_profile WHERE user_id = ?", (cleaned,)
        ).fetchone()

        if row is None:
            now = _now_iso()
            return {
                "user_id": cleaned,
                "privacy_preference": "Medium",
                "historical_protection_level": "Medium",
                "feedback_count": 0,
                "feedback_log": [],
                "calibrated_threshold_override": None,
                "created_at": now,
                "updated_at": now,
            }

        override = None
        if row["calibrated_threshold_medium"] is not None:
            override = {
                "medium": row["calibrated_threshold_medium"],
                "high": row["calibrated_threshold_high"],
            }

        fb_rows = conn.execute(
            """
            SELECT screenshot_id, predicted_label, user_label, submitted_at
              FROM feedback_log
             WHERE user_id = ?
             ORDER BY id ASC
            """,
            (cleaned,),
        ).fetchall()

        feedback_log = [
            {
                "screenshot_id": r["screenshot_id"],
                "predicted_label": r["predicted_label"],
                "user_label": r["user_label"],
                "timestamp": r["submitted_at"],
            }
            for r in fb_rows
        ]

        return {
            "user_id": row["user_id"],
            "privacy_preference": row["privacy_preference"],
            "historical_protection_level": row["historical_protection_level"],
            "feedback_count": row["feedback_count"],
            "feedback_log": feedback_log,
            "calibrated_threshold_override": override,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def save_profile(profile: dict[str, Any]) -> None:
    """Upsert profile preferences and calibration state to the database."""
    user_id = _sanitise_user_id(str(profile.get("user_id", "")))
    override = profile.get("calibrated_threshold_override")
    cal_medium = override["medium"] if isinstance(override, dict) else None
    cal_high = override["high"] if isinstance(override, dict) else None
    updated_at = _now_iso()

    conn = get_connection(_DB_PATH)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO user_profile
                    (user_id, privacy_preference, historical_protection_level,
                     feedback_count, calibrated_threshold_medium,
                     calibrated_threshold_high, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    privacy_preference          = excluded.privacy_preference,
                    historical_protection_level = excluded.historical_protection_level,
                    feedback_count              = excluded.feedback_count,
                    calibrated_threshold_medium = excluded.calibrated_threshold_medium,
                    calibrated_threshold_high   = excluded.calibrated_threshold_high,
                    updated_at                  = excluded.updated_at
                """,
                (
                    user_id,
                    profile.get("privacy_preference", "Medium"),
                    profile.get("historical_protection_level", "Medium"),
                    int(profile.get("feedback_count", 0)),
                    cal_medium,
                    cal_high,
                    profile.get("created_at", _now_iso()),
                    updated_at,
                ),
            )
    finally:
        conn.close()

    profile["updated_at"] = updated_at


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

def update_preferences(
    user_id: str,
    privacy_preference: str | None = None,
    historical_protection_level: str | None = None,
) -> dict[str, Any]:
    """Update stored user preferences and return the updated profile."""
    profile = load_profile(user_id)

    if privacy_preference is not None:
        if privacy_preference not in _VALID_PREFERENCE_VALUES:
            raise ValueError(
                f"privacy_preference must be one of {sorted(_VALID_PREFERENCE_VALUES)}, "
                f"got '{privacy_preference}'."
            )
        profile["privacy_preference"] = privacy_preference

    if historical_protection_level is not None:
        if historical_protection_level not in _VALID_PREFERENCE_VALUES:
            raise ValueError(
                f"historical_protection_level must be one of "
                f"{sorted(_VALID_PREFERENCE_VALUES)}, got '{historical_protection_level}'."
            )
        profile["historical_protection_level"] = historical_protection_level

    save_profile(profile)
    return profile


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def record_feedback(
    user_id: str,
    screenshot_id: str,
    predicted_label: str,
    user_label: str,
) -> dict[str, Any]:
    """
    Append a feedback record and recalibrate thresholds when
    RECALIBRATION_THRESHOLD examples have been collected.
    """
    user_label_upper = user_label.strip().upper()
    predicted_upper = predicted_label.strip().upper()

    if user_label_upper not in _VALID_RISK_LABELS:
        raise ValueError(
            f"user_label must be one of {sorted(_VALID_RISK_LABELS)}, "
            f"got '{user_label}'."
        )
    if predicted_upper not in _VALID_RISK_LABELS:
        raise ValueError(
            f"predicted_label must be one of {sorted(_VALID_RISK_LABELS)}, "
            f"got '{predicted_label}'."
        )

    cleaned_id = _sanitise_user_id(user_id)
    # Strip whitespace so that "scr_001" and "scr_001 " are treated identically,
    # preventing a trivial bypass of the per-screenshot poisoning guard.
    cleaned_scr_id = str(screenshot_id).strip()
    now = _now_iso()

    conn = get_connection(_DB_PATH)
    try:
        with conn:
            # Ensure the user_profile row exists before inserting into feedback_log.
            existing = conn.execute(
                "SELECT created_at FROM user_profile WHERE user_id = ?",
                (cleaned_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO user_profile
                        (user_id, privacy_preference, historical_protection_level,
                         feedback_count, created_at, updated_at)
                    VALUES (?, 'Medium', 'Medium', 0, ?, ?)
                    """,
                    (cleaned_id, now, now),
                )

            # Poisoning guard: reject feedback for a screenshot_id that already
            # has MAX_FEEDBACK_PER_SCREENSHOT records from this user.
            dup_count = conn.execute(
                """
                SELECT COUNT(*) FROM feedback_log
                 WHERE user_id = ? AND screenshot_id = ?
                """,
                (cleaned_id, cleaned_scr_id),
            ).fetchone()[0]
            if dup_count >= MAX_FEEDBACK_PER_SCREENSHOT:
                raise ValueError(
                    f"Feedback limit reached for screenshot '{cleaned_scr_id}'. "
                    f"Maximum {MAX_FEEDBACK_PER_SCREENSHOT} records per screenshot per user."
                )

            conn.execute(
                """
                INSERT INTO feedback_log
                    (user_id, screenshot_id, predicted_label, user_label, submitted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cleaned_id, cleaned_scr_id, predicted_upper, user_label_upper, now),
            )

            # Count all feedback; use only the most recent RECALIBRATION_WINDOW
            # records for threshold calculation (windowed adaptation).
            all_fb_rows = conn.execute(
                """
                SELECT predicted_label, user_label
                  FROM feedback_log
                 WHERE user_id = ?
                 ORDER BY id ASC
                """,
                (cleaned_id,),
            ).fetchall()
            feedback_count = len(all_fb_rows)
            window_rows = all_fb_rows[-RECALIBRATION_WINDOW:]

            # Fetch previous thresholds before overwriting (for adaptation log).
            prev_row = conn.execute(
                """
                SELECT calibrated_threshold_medium, calibrated_threshold_high
                  FROM user_profile WHERE user_id = ?
                """,
                (cleaned_id,),
            ).fetchone()
            old_medium = prev_row["calibrated_threshold_medium"] if prev_row else None
            old_high   = prev_row["calibrated_threshold_high"]   if prev_row else None

            override: dict[str, float] | None = None
            if feedback_count >= RECALIBRATION_THRESHOLD:
                fb_list = [
                    {"predicted_label": r["predicted_label"], "user_label": r["user_label"]}
                    for r in window_rows
                ]
                override = _recalibrate(fb_list)

            cal_medium = override["medium"] if override else None
            cal_high   = override["high"]   if override else None

            conn.execute(
                """
                UPDATE user_profile SET
                    feedback_count              = ?,
                    calibrated_threshold_medium = ?,
                    calibrated_threshold_high   = ?,
                    updated_at                  = ?
                WHERE user_id = ?
                """,
                (feedback_count, cal_medium, cal_high, now, cleaned_id),
            )

            # Log the adaptation event when thresholds change.
            if override and (override["medium"] != old_medium or override["high"] != old_high):
                conn.execute(
                    """
                    INSERT INTO adaptation_log
                        (user_id, applied_at, trigger_count, feedback_window_size,
                         old_medium_threshold, old_high_threshold,
                         new_medium_threshold, new_high_threshold)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cleaned_id, now, feedback_count, len(window_rows),
                        old_medium, old_high,
                        override["medium"], override["high"],
                    ),
                )
    finally:
        conn.close()

    return load_profile(cleaned_id)


def get_adaptation_history(user_id: str, limit: int = 20) -> list[dict]:
    """
    Return the most-recent adaptation events for a user (newest first).

    Each event records the threshold values before and after recalibration
    and how many feedback records triggered it.
    """
    cleaned = _sanitise_user_id(user_id)
    conn = get_connection(_DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT applied_at, trigger_count, feedback_window_size,
                   old_medium_threshold, old_high_threshold,
                   new_medium_threshold, new_high_threshold,
                   rollback_reason
              FROM adaptation_log
             WHERE user_id = ?
             ORDER BY id DESC
             LIMIT ?
            """,
            (cleaned, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def rollback_calibration(user_id: str, reason: str = "manual_rollback") -> dict:
    """
    Revert the user's threshold calibration to the previous safe state.

    Looks at the second-most-recent adaptation_log entry (one step back)
    and restores those thresholds.  If no previous state exists the
    calibration is cleared (reverts to default preference-based thresholds).

    A new adaptation_log row is written to record the rollback event.

    Returns the updated profile.
    """
    cleaned = _sanitise_user_id(user_id)
    now = _now_iso()

    conn = get_connection(_DB_PATH)
    try:
        # Fetch the last two adaptation events.
        rows = conn.execute(
            """
            SELECT id, new_medium_threshold, new_high_threshold
              FROM adaptation_log
             WHERE user_id = ?
             ORDER BY id DESC
             LIMIT 2
            """,
            (cleaned,),
        ).fetchall()

        if len(rows) >= 2:
            # Restore to the state *before* the most recent adaptation.
            target_medium = rows[1]["new_medium_threshold"]
            target_high   = rows[1]["new_high_threshold"]
        elif len(rows) == 1:
            # Only one event; roll back to default (None = use preference defaults).
            target_medium = None
            target_high   = None
        else:
            # Nothing to roll back.
            return load_profile(cleaned)

        # Read current thresholds for the log.
        cur = conn.execute(
            "SELECT calibrated_threshold_medium, calibrated_threshold_high, feedback_count FROM user_profile WHERE user_id = ?",
            (cleaned,),
        ).fetchone()
        old_medium = cur["calibrated_threshold_medium"] if cur else None
        old_high   = cur["calibrated_threshold_high"]   if cur else None
        feedback_count = cur["feedback_count"] if cur else 0

        with conn:
            conn.execute(
                """
                UPDATE user_profile SET
                    calibrated_threshold_medium = ?,
                    calibrated_threshold_high   = ?,
                    updated_at                  = ?
                WHERE user_id = ?
                """,
                (target_medium, target_high, now, cleaned),
            )
            conn.execute(
                """
                INSERT INTO adaptation_log
                    (user_id, applied_at, trigger_count, feedback_window_size,
                     old_medium_threshold, old_high_threshold,
                     new_medium_threshold, new_high_threshold,
                     rollback_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned, now, feedback_count, 0,
                    old_medium, old_high,
                    target_medium if target_medium is not None else _DEFAULT_THRESHOLDS["medium"],
                    target_high   if target_high   is not None else _DEFAULT_THRESHOLDS["high"],
                    reason,
                ),
            )
    finally:
        conn.close()

    return load_profile(cleaned)


def _recalibrate(feedback_log: list[dict[str, str]]) -> dict[str, float]:
    """
    Derive adjusted thresholds from the user's feedback history.

    For each case where the user labelled the risk as higher than the model
    predicted, the decision threshold is lowered (becoming more sensitive).
    For each case where the user labelled it lower, it is raised.

    The shift is bounded to [0.05, 0.90].
    """
    _LEVEL_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    upgrades = 0
    downgrades = 0
    for record in feedback_log:
        pred_idx = _LEVEL_ORDER.get(record.get("predicted_label", "LOW"), 0)
        user_idx = _LEVEL_ORDER.get(record.get("user_label", "LOW"), 0)
        if user_idx > pred_idx:
            upgrades += 1
        elif user_idx < pred_idx:
            downgrades += 1

    net = upgrades - downgrades
    shift = net * _THRESHOLD_STEP

    return {
        "medium": round(max(0.05, min(0.90, _DEFAULT_THRESHOLDS["medium"] - shift)), 4),
        "high": round(max(0.05, min(0.90, _DEFAULT_THRESHOLDS["high"] - shift)), 4),
    }


# ---------------------------------------------------------------------------
# Profile summary (safe for API responses — no raw feedback text)
# ---------------------------------------------------------------------------

def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a summary of a profile safe to include in API responses."""
    return {
        "user_id": profile.get("user_id"),
        "privacy_preference": profile.get("privacy_preference"),
        "historical_protection_level": profile.get("historical_protection_level"),
        "feedback_count": profile.get("feedback_count", 0),
        "calibrated_threshold_override": profile.get("calibrated_threshold_override"),
        "recalibration_threshold": RECALIBRATION_THRESHOLD,
        "recalibration_active": profile.get("calibrated_threshold_override") is not None,
        "updated_at": profile.get("updated_at"),
    }
