"""
CAPRLM ML-based privacy risk prediction with user-specific personalisation.

Architecture
------------
1. A shared Random Forest classifier is trained offline by train_model.py and
   serialised to backend/data/caprlm_model.joblib.
2. At inference, raw class probabilities are produced by the shared model.
3. User privacy_preference and historical_protection_level shift the decision
   thresholds applied to those probabilities (threshold adjustment, not
   per-user retraining — documented as a limitation).
4. User feedback is logged by user_profile_service.py; after ≥ 10 feedback
   examples the per-user threshold is recalibrated from that user's history.

Feature mapping (train CSV → runtime)
--------------------------------------
CSV column               runtime feature_extraction key    transform
EMAIL                    email_count                       int(count > 0)
PHONE                    phone_count                       int(count > 0)
URL                      url_count                         int(count > 0)
IP_ADDRESS               ip_count                          int(count > 0)
DATE                     date_count                        int(count > 0)
WINDOWS_PATH             windows_path_count                int(count > 0)
sensitive_entity_present sensitive_entity_count            int(count > 0)
application_context_enc  application_context               LabelEncoder.transform
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import joblib

# ---------------------------------------------------------------------------
# Model bundle (lazy-loaded once per process)
# ---------------------------------------------------------------------------
_MODEL_PATH = Path(__file__).parent.parent / "data" / "caprlm_model.joblib"
_bundle: dict | None = None


def _load_bundle() -> dict:
    global _bundle
    if _bundle is None:
        if not _MODEL_PATH.exists():
            raise RuntimeError(
                "ML model not found. Run `python -m backend.services.train_model` "
                "from the Member2 directory to train and serialise the model first."
            )
        _bundle = joblib.load(_MODEL_PATH)
    return _bundle


def reload_bundle() -> None:
    """Force a reload of the model bundle from disk (called after /train)."""
    global _bundle
    _bundle = None
    _load_bundle()


# ---------------------------------------------------------------------------
# Risk level ordering
# ---------------------------------------------------------------------------
_RISK_LEVELS: list[str] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _risk_index(level: str) -> int:
    try:
        return _RISK_LEVELS.index(level.upper())
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Feature preparation
# ---------------------------------------------------------------------------

def _prepare_inference_features(
    extracted_features: Mapping[str, Any],
    bundle: dict,
) -> list[int | float]:
    """
    Convert runtime feature_extraction output into the vector expected by
    the trained model.  Integer counts are binarised (>0 → 1) so the
    inference feature space matches the binary training data.
    """
    context_encoder = bundle["context_encoder"]
    feature_names: list[str] = bundle["feature_names"]

    def _binarise(key: str) -> int:
        val = extracted_features.get(key, 0)
        return 1 if (isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0) else 0

    def _encode_context(val: Any) -> int:
        s = str(val).strip() if val is not None else "Unknown"
        try:
            return int(context_encoder.transform([s])[0])
        except ValueError:
            return -1

    # Build a mapping from feature name to value
    value_map: dict[str, int | float] = {
        "EMAIL": _binarise("email_count"),
        "PHONE": _binarise("phone_count"),
        "URL": _binarise("url_count"),
        "IP_ADDRESS": _binarise("ip_count"),
        "DATE": _binarise("date_count"),
        "WINDOWS_PATH": _binarise("windows_path_count"),
        "sensitive_entity_present": _binarise("sensitive_entity_count"),
        "application_context_encoded": _encode_context(
            extracted_features.get("application_context")
        ),
    }

    return [value_map.get(name, 0) for name in feature_names]


# ---------------------------------------------------------------------------
# Threshold tables
# ---------------------------------------------------------------------------

# Per-preference thresholds: if P(HIGH or CRITICAL) exceeds high_thresh,
# predict HIGH; if P(MEDIUM or above) exceeds medium_thresh, predict MEDIUM.
_DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "high":   {"medium": 0.20, "high": 0.10},
    "medium": {"medium": 0.35, "high": 0.22},
    "low":    {"medium": 0.50, "high": 0.40},
}

# Historical-protection-level modifies the threshold by ±0.08
_HISTORICAL_DELTA: dict[str, float] = {
    "high": -0.08,    # more conservative → lower threshold
    "medium": 0.0,
    "low": +0.08,     # more permissive → higher threshold
}


def _apply_thresholds(
    class_proba: dict[str, float],
    privacy_preference: str,
    historical_protection_level: str,
    calibrated_threshold_override: dict[str, float] | None,
) -> tuple[str, float]:
    """
    Apply preference-based thresholds to the raw probability distribution.

    Returns (adjusted_risk_level, confidence).
    """
    pref_key = str(privacy_preference).lower() if privacy_preference else "medium"
    hist_key = str(historical_protection_level).lower() if historical_protection_level else "medium"

    if calibrated_threshold_override:
        thresholds = dict(calibrated_threshold_override)
    else:
        base = _DEFAULT_THRESHOLDS.get(pref_key, _DEFAULT_THRESHOLDS["medium"]).copy()
        delta = _HISTORICAL_DELTA.get(hist_key, 0.0)
        thresholds = {k: max(0.05, min(0.95, v + delta)) for k, v in base.items()}

    p_high_plus = class_proba.get("HIGH", 0.0) + class_proba.get("CRITICAL", 0.0)
    p_medium_plus = class_proba.get("MEDIUM", 0.0) + p_high_plus

    if p_high_plus >= thresholds["high"]:
        adjusted = "HIGH"
        confidence = p_high_plus
    elif p_medium_plus >= thresholds["medium"]:
        adjusted = "MEDIUM"
        confidence = p_medium_plus
    else:
        adjusted = "LOW"
        confidence = class_proba.get("LOW", 1.0)

    return adjusted, round(float(confidence), 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_privacy_risk(
    extracted_features: Mapping[str, Any],
    application_context: Any,
    privacy_preference: Any,
    historical_protection_level: Any,
    calibrated_threshold_override: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Predict privacy risk using the trained shared RF model with per-user
    threshold adjustment.

    Parameters
    ----------
    extracted_features:
        Output of feature_extraction.extract_features().
    application_context:
        String passed from the frontend (e.g. 'Google Chrome').
    privacy_preference:
        User setting: 'Low' | 'Medium' | 'High'.
    historical_protection_level:
        User setting: 'Low' | 'Medium' | 'High'.
    calibrated_threshold_override:
        Optional per-user recalibrated thresholds from user_profile_service.
        When provided, these replace the default preference-based thresholds.

    Returns
    -------
    dict with keys:
        risk_level, confidence, model_type, model_version,
        raw_probabilities, applied_thresholds,
        privacy_preference_used, historical_protection_level_used,
        personalisation_note
    """
    try:
        bundle = _load_bundle()
    except RuntimeError as exc:
        return {
            "risk_level": "UNAVAILABLE",
            "confidence": None,
            "model_type": "ml_random_forest",
            "model_version": None,
            "raw_probabilities": None,
            "applied_thresholds": None,
            "privacy_preference_used": str(privacy_preference),
            "historical_protection_level_used": str(historical_protection_level),
            "personalisation_note": None,
            "model_status": f"MODEL_NOT_LOADED: {exc}",
        }

    model = bundle["model"]
    classes: list[str] = bundle["classes"]

    feature_vector = _prepare_inference_features(extracted_features, bundle)

    proba_array = model.predict_proba([feature_vector])[0]
    raw_proba: dict[str, float] = {
        cls: round(float(p), 4) for cls, p in zip(classes, proba_array)
    }

    # Add CRITICAL=0 if absent (class not seen in training data)
    if "CRITICAL" not in raw_proba:
        raw_proba["CRITICAL"] = 0.0

    risk_level, confidence = _apply_thresholds(
        raw_proba,
        str(privacy_preference) if privacy_preference else "Medium",
        str(historical_protection_level) if historical_protection_level else "Medium",
        calibrated_threshold_override,
    )

    pref_key = str(privacy_preference).lower() if privacy_preference else "medium"
    hist_key = str(historical_protection_level).lower() if historical_protection_level else "medium"
    base = _DEFAULT_THRESHOLDS.get(pref_key, _DEFAULT_THRESHOLDS["medium"]).copy()
    delta = _HISTORICAL_DELTA.get(hist_key, 0.0)
    applied_thresholds = (
        calibrated_threshold_override
        if calibrated_threshold_override
        else {k: round(max(0.05, min(0.95, v + delta)), 4) for k, v in base.items()}
    )

    personalisation_note = (
        "User-calibrated thresholds applied (≥10 feedback examples)."
        if calibrated_threshold_override
        else "Default preference-based threshold adjustment applied."
    )

    return {
        "risk_level": risk_level,
        "confidence": confidence,
        "model_type": "ml_random_forest",
        "model_version": bundle.get("model_version"),
        "raw_probabilities": raw_proba,
        "applied_thresholds": applied_thresholds,
        "privacy_preference_used": str(privacy_preference),
        "historical_protection_level_used": str(historical_protection_level),
        "personalisation_note": personalisation_note,
        "model_status": "ML_MODEL_ACTIVE",
    }
