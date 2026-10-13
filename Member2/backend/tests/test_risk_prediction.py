"""Unit tests for risk_prediction.predict_privacy_risk (using a mock bundle)."""

import pytest
from unittest.mock import MagicMock, patch
import numpy as np


# ---------------------------------------------------------------------------
# Helpers — build a minimal mock bundle
# ---------------------------------------------------------------------------

def _mock_bundle(classes=None):
    if classes is None:
        classes = ["HIGH", "LOW", "MEDIUM"]

    model = MagicMock()
    proba = np.array([[0.15, 0.70, 0.15]])  # HIGH=0.15, LOW=0.70, MEDIUM=0.15
    model.predict_proba.return_value = proba
    model.classes_ = classes

    from sklearn.preprocessing import LabelEncoder
    enc = LabelEncoder()
    enc.fit(["ChatGPT", "Google Chrome", "Unknown", "VS Code"])

    return {
        "model": model,
        "context_encoder": enc,
        "feature_names": [
            "EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE",
            "WINDOWS_PATH", "sensitive_entity_present",
            "application_context_encoded",
        ],
        "classes": classes,
        "model_version": "test-1.0",
    }


def _base_features():
    return {
        "email_count": 0,
        "phone_count": 0,
        "url_count": 0,
        "ip_count": 0,
        "date_count": 0,
        "windows_path_count": 0,
        "sensitive_entity_count": 0,
        "ocr_text_length": 100,
        "application_context": "Google Chrome",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_returns_low_when_proba_dominated_by_low():
    """P(HIGH)=0.15 < default HIGH threshold → should return LOW with Medium preference."""
    import backend.services.risk_prediction as rp
    with patch.object(rp, "_bundle", _mock_bundle()):
        result = rp.predict_privacy_risk(
            _base_features(), "Google Chrome", "Medium", "Medium"
        )
    assert result["risk_level"] == "LOW"
    assert result["model_type"] == "ml_random_forest"
    assert result["model_status"] == "ML_MODEL_ACTIVE"


def test_high_preference_lowers_threshold():
    """P(HIGH)=0.15 ≥ HIGH-preference threshold (0.10) → should return HIGH."""
    import backend.services.risk_prediction as rp
    with patch.object(rp, "_bundle", _mock_bundle()):
        result = rp.predict_privacy_risk(
            _base_features(), "Google Chrome", "High", "Medium"
        )
    assert result["risk_level"] == "HIGH"


def test_calibrated_override_applied():
    """Passing an override that sets high=0.10 should make P(HIGH)=0.15 trigger HIGH."""
    import backend.services.risk_prediction as rp
    with patch.object(rp, "_bundle", _mock_bundle()):
        result = rp.predict_privacy_risk(
            _base_features(), "Google Chrome", "Medium", "Medium",
            calibrated_threshold_override={"medium": 0.20, "high": 0.10},
        )
    assert result["risk_level"] == "HIGH"
    assert "User-calibrated" in result["personalisation_note"]


def test_returns_unavailable_when_model_not_loaded():
    import backend.services.risk_prediction as rp
    with patch.object(rp, "_bundle", None):
        with patch.object(rp, "_MODEL_PATH", __import__("pathlib").Path("/nonexistent/path")):
            result = rp.predict_privacy_risk(
                _base_features(), "Chrome", "Medium", "Medium"
            )
    assert result["risk_level"] == "UNAVAILABLE"
    assert "MODEL_NOT_LOADED" in result["model_status"]


def test_raw_probabilities_present_and_sum_to_one():
    import backend.services.risk_prediction as rp
    with patch.object(rp, "_bundle", _mock_bundle()):
        result = rp.predict_privacy_risk(
            _base_features(), "Google Chrome", "Medium", "Medium"
        )
    proba = result["raw_probabilities"]
    assert proba is not None
    # CRITICAL is added with 0.0 when not in training classes
    total = sum(v for k, v in proba.items() if k != "CRITICAL")
    assert abs(total - 1.0) < 1e-4


def test_unknown_application_context_does_not_crash():
    import backend.services.risk_prediction as rp
    feats = _base_features()
    feats["application_context"] = "UnknownApp_XYZ"
    with patch.object(rp, "_bundle", _mock_bundle()):
        result = rp.predict_privacy_risk(feats, "UnknownApp_XYZ", "Medium", "Medium")
    assert "risk_level" in result


def test_none_preference_uses_medium_default():
    import backend.services.risk_prediction as rp
    with patch.object(rp, "_bundle", _mock_bundle()):
        result = rp.predict_privacy_risk(
            _base_features(), "Chrome", None, None
        )
    assert result["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
