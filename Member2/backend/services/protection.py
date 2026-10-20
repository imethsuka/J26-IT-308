"""
Adaptive privacy protection selection.

Maps the ML-predicted risk level and detection output to a concrete
protection action.  Actions are ordered by severity:

  LOW      → MONITOR      : log the event; no user-visible action
  MEDIUM   → BLUR_SENSITIVE: blur detected sensitive entity regions
  HIGH     → BLUR_AND_ALERT: blur all visible text and raise an alert
  CRITICAL → BLOCK        : prevent the capture and alert (irreversible)

The confidence value from the risk prediction is exposed so the caller
can treat low-confidence decisions differently if needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_RISK_TO_ACTION: dict[str, dict[str, Any]] = {
    "LOW": {
        "protection_action": "MONITOR",
        "description": "Log the event. No user-visible protection applied.",
        "reversible": True,
        "severity": 0,
    },
    "MEDIUM": {
        "protection_action": "BLUR_SENSITIVE",
        "description": "Blur detected sensitive entity regions (email, phone, URL, path).",
        "reversible": True,
        "severity": 1,
    },
    "HIGH": {
        "protection_action": "BLUR_AND_ALERT",
        "description": "Blur all visible text and notify the user.",
        "reversible": True,
        "severity": 2,
    },
    "CRITICAL": {
        "protection_action": "BLOCK",
        "description": "Block the screenshot capture and alert the user immediately.",
        "reversible": False,
        "severity": 3,
    },
}

_FALLBACK_ACTION: dict[str, Any] = {
    "protection_action": "BLOCK",
    "description": "Risk state unavailable or unrecognised; blocking until it can be assessed safely.",
    "reversible": False,
    "severity": 3,
}

_VALID_RISK_LEVELS = frozenset(_RISK_TO_ACTION)


def select_protection(
    risk_level: Any,
    confidence: Any,
    detection_output: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Select a protection action based on risk level.

    Parameters
    ----------
    risk_level:
        String from predict_privacy_risk(), e.g. 'HIGH'.
    confidence:
        Float [0, 1] from predict_privacy_risk(); None if model unavailable.
    detection_output:
        Dict from detect_sensitive_entities(), used to count affected categories.

    Returns
    -------
    dict with keys:
        protection_action, description, reversible, severity,
        risk_level_received, confidence_received, affected_categories
    """
    level = str(risk_level).upper().strip() if risk_level else "UNAVAILABLE"
    action_info = _RISK_TO_ACTION.get(level, _FALLBACK_ACTION)

    # Enumerate which entity categories are present (for display / audit)
    affected: list[str] = []
    if isinstance(detection_output, Mapping):
        for category, value in detection_output.items():
            if category == "total_sensitive_entity_count":
                continue
            if isinstance(value, Mapping) and isinstance(value.get("count"), int):
                if value["count"] > 0:
                    affected.append(category)

    return {
        "protection_action": action_info["protection_action"],
        "description": action_info["description"],
        "reversible": action_info["reversible"],
        "severity": action_info["severity"],
        "risk_level_received": level,
        "confidence_received": round(float(confidence), 4) if isinstance(confidence, float) else confidence,
        "affected_categories": affected,
    }
