"""Unit tests for feature_extraction.extract_features."""

import pytest
from backend.services.sensitive_detection import detect_sensitive_entities
from backend.services.feature_extraction import extract_features


def _make_detection(text: str):
    return detect_sensitive_entities(text)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_rejects_non_string_ocr_text():
    det = _make_detection("")
    with pytest.raises(TypeError):
        extract_features(42, det, "Chrome")  # type: ignore[arg-type]


def test_rejects_non_mapping_detection_output():
    with pytest.raises(TypeError):
        extract_features("text", "not a mapping", "Chrome")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Feature values
# ---------------------------------------------------------------------------

def test_features_for_empty_text():
    det = _make_detection("")
    feats = extract_features("", det, "VS Code")
    assert feats["email_count"] == 0
    assert feats["phone_count"] == 0
    assert feats["url_count"] == 0
    assert feats["sensitive_entity_count"] == 0
    assert feats["ocr_text_length"] == 0


def test_email_count_reflects_detection():
    text = "hello@example.com and hi@test.org"
    det = _make_detection(text)
    feats = extract_features(text, det, "Word")
    assert feats["email_count"] == 2


def test_url_count_reflects_detection():
    text = "Visit https://example.com and http://other.com"
    det = _make_detection(text)
    feats = extract_features(text, det, "Google Chrome")
    assert feats["url_count"] == 2


def test_ocr_text_length_correct():
    text = "Hello world"
    det = _make_detection(text)
    feats = extract_features(text, det, "ChatGPT")
    assert feats["ocr_text_length"] == len(text)


def test_application_context_stored():
    det = _make_detection("")
    feats = extract_features("", det, "Slack")
    assert feats["application_context"] == "Slack"


def test_sensitive_entity_count_is_total():
    text = "admin@corp.com visited https://internal.corp.com"
    det = _make_detection(text)
    feats = extract_features(text, det, "Google Chrome")
    assert feats["sensitive_entity_count"] == det["total_sensitive_entity_count"]


def test_all_expected_keys_present():
    det = _make_detection("")
    feats = extract_features("", det, "Other")
    expected_keys = {
        "email_count", "phone_count", "url_count", "ip_count",
        "date_count", "windows_path_count", "sensitive_entity_count",
        "ocr_text_length", "application_context",
    }
    assert expected_keys.issubset(feats.keys())
