"""Unit tests for rule_baseline.predict_rule_based."""

import pytest
from backend.services.rule_baseline import predict_rule_based


def _features(**kwargs):
    base = {
        "email_count": 0, "phone_count": 0, "url_count": 0,
        "ip_count": 0, "date_count": 0, "windows_path_count": 0,
        "sensitive_entity_count": 0, "application_context": "Other",
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Rule correctness
# ---------------------------------------------------------------------------

def test_no_entities_returns_low():
    result = predict_rule_based(_features())
    assert result["risk_level"] == "LOW"


def test_email_and_phone_returns_critical():
    result = predict_rule_based(_features(email_count=1, phone_count=1))
    assert result["risk_level"] == "CRITICAL"


def test_email_alone_returns_high():
    result = predict_rule_based(_features(email_count=1))
    assert result["risk_level"] == "HIGH"


def test_phone_alone_returns_high():
    result = predict_rule_based(_features(phone_count=1))
    assert result["risk_level"] == "HIGH"


def test_ip_alone_returns_high():
    result = predict_rule_based(_features(ip_count=1))
    assert result["risk_level"] == "HIGH"


def test_url_alone_returns_medium():
    result = predict_rule_based(_features(url_count=1))
    assert result["risk_level"] == "MEDIUM"


def test_windows_path_alone_returns_medium():
    result = predict_rule_based(_features(windows_path_count=1))
    assert result["risk_level"] == "MEDIUM"


def test_browser_context_with_entity_returns_medium():
    result = predict_rule_based(_features(
        sensitive_entity_count=1,
        application_context="Google Chrome",
    ))
    assert result["risk_level"] == "MEDIUM"


def test_browser_context_no_entity_returns_low():
    result = predict_rule_based(_features(application_context="Google Chrome"))
    assert result["risk_level"] == "LOW"


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------

def test_return_has_required_keys():
    result = predict_rule_based(_features())
    assert "risk_level" in result
    assert "confidence" in result
    assert "model_type" in result
    assert result["model_type"] == "rule_based_baseline"


def test_confidence_is_float_in_range():
    result = predict_rule_based(_features(email_count=1))
    assert isinstance(result["confidence"], float)
    assert 0.0 <= result["confidence"] <= 1.0
