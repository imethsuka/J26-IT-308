"""
User profile service — persistent per-user privacy preferences and feedback.

Storage
-------
Profiles are written as JSON files under backend/data/profiles/<user_id>.json.
These files contain user preferences, a feedback log, and a calibrated
decision-threshold override that is updated after ≥ RECALIBRATION_THRESHOLD
feedback examples are collected.

Profile schema
--------------
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
- user_id is sanitised before use as a filename (alphanumeric + '-' only).
- Profile JSON never contains raw OCR text or screenshot content.
- The profiles directory should be excluded from version control (.gitignore).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_PROFILES_DIR = Path(__file__).parent.parent / "data" / "profiles"
_VALID_RISK_LABELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
_VALID_PREFERENCE_VALUES = frozenset({"Low", "Medium", "High"})
RECALIBRATION_THRESHOLD: int = 10

# Default thresholds (must stay in sync with risk_prediction._DEFAULT_THRESHOLDS["medium"])
_DEFAULT_THRESHOLDS: dict[str, float] = {"medium": 0.35, "high": 0.22}

# How much each user-labelled upgrade shifts the threshold downward
_THRESHOLD_STEP: float = 0.02


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitise_user_id(user_id: str) -> str:
    """Allow only alphanumeric characters and hyphens; raise on invalid input."""
    cleaned = re.sub(r"[^A-Za-z0-9\-]", "", user_id)
    if not cleaned:
        raise ValueError("user_id must contain at least one alphanumeric character.")
    return cleaned


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _profile_path(user_id: str) -> Path:
    return _PROFILES_DIR / f"{_sanitise_user_id(user_id)}.json"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def load_profile(user_id: str) -> dict[str, Any]:
    """Load an existing profile or return a default one."""
    path = _profile_path(user_id)
    if path.exists():
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass  # fall through to default

    now = _now_iso()
    return {
        "user_id": user_id,
        "privacy_preference": "Medium",
        "historical_protection_level": "Medium",
        "feedback_count": 0,
        "feedback_log": [],
        "calibrated_threshold_override": None,
        "created_at": now,
        "updated_at": now,
    }


def save_profile(profile: dict[str, Any]) -> None:
    """Persist a profile dict to disk."""
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    user_id = profile.get("user_id", "")
    path = _profile_path(str(user_id))
    profile["updated_at"] = _now_iso()
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")


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
    Append a feedback record to the user profile and recalibrate thresholds
    when RECALIBRATION_THRESHOLD examples have been collected.

    Parameters
    ----------
    user_id:
        Identifier for the user session.
    screenshot_id:
        Identifier for the screenshot that was analysed.
    predicted_label:
        The label that the model predicted.
    user_label:
        The label the user believes is correct (validated externally).

    Returns
    -------
    Updated profile dict (does not include raw feedback text).
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

    profile = load_profile(user_id)

    entry: dict[str, str] = {
        "screenshot_id": str(screenshot_id),
        "predicted_label": predicted_upper,
        "user_label": user_label_upper,
        "timestamp": _now_iso(),
    }
    profile["feedback_log"].append(entry)
    profile["feedback_count"] = len(profile["feedback_log"])

    if profile["feedback_count"] >= RECALIBRATION_THRESHOLD:
        profile["calibrated_threshold_override"] = _recalibrate(profile["feedback_log"])

    save_profile(profile)
    return profile


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
        "high":   round(max(0.05, min(0.90, _DEFAULT_THRESHOLDS["high"]   - shift)), 4),
    }


# ---------------------------------------------------------------------------
# Profile summary (safe for API responses — no raw OCR data)
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
