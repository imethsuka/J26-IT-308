"""Unit tests for sensitive_detection.detect_sensitive_entities."""

import pytest
from backend.services.sensitive_detection import detect_sensitive_entities


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_rejects_non_string():
    with pytest.raises(TypeError):
        detect_sensitive_entities(12345)  # type: ignore[arg-type]


def test_empty_string_returns_all_zeros():
    result = detect_sensitive_entities("")
    for category in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"):
        assert result[category]["count"] == 0
    assert result["total_sensitive_entity_count"] == 0


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def test_email_detected():
    result = detect_sensitive_entities("Contact alice@example.com for details.")
    assert result["EMAIL"]["count"] == 1
    assert "alice@example.com" in result["EMAIL"]["matches"]


def test_multiple_emails_detected():
    result = detect_sensitive_entities("a@b.com and c@d.org are both here")
    assert result["EMAIL"]["count"] == 2


def test_no_email_in_plain_text():
    result = detect_sensitive_entities("Hello world, nothing sensitive here.")
    assert result["EMAIL"]["count"] == 0


# ---------------------------------------------------------------------------
# PHONE
# ---------------------------------------------------------------------------

def test_phone_detected():
    result = detect_sensitive_entities("Call me at 555-867-5309")
    assert result["PHONE"]["count"] >= 1


def test_no_phone_in_plain_text():
    result = detect_sensitive_entities("No phone number here.")
    assert result["PHONE"]["count"] == 0


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------

def test_url_detected():
    result = detect_sensitive_entities("Visit https://example.com/page for more.")
    assert result["URL"]["count"] == 1
    assert "https://example.com/page" in result["URL"]["matches"]


def test_url_trailing_punctuation_stripped():
    result = detect_sensitive_entities("See https://example.com/page.")
    assert all(not m.endswith(".") for m in result["URL"]["matches"])


# ---------------------------------------------------------------------------
# IP_ADDRESS
# ---------------------------------------------------------------------------

def test_ipv4_detected():
    result = detect_sensitive_entities("Server is at 192.168.1.100")
    assert result["IP_ADDRESS"]["count"] == 1


def test_invalid_ipv4_octet_not_detected():
    result = detect_sensitive_entities("Not an IP: 999.999.999.999")
    assert result["IP_ADDRESS"]["count"] == 0


# ---------------------------------------------------------------------------
# DATE
# ---------------------------------------------------------------------------

def test_date_detected():
    result = detect_sensitive_entities("Meeting on 2024-03-15")
    assert result["DATE"]["count"] == 1


# ---------------------------------------------------------------------------
# WINDOWS_PATH
# ---------------------------------------------------------------------------

def test_windows_path_detected():
    result = detect_sensitive_entities(r"File is at C:\Users\alice\Documents\report.pdf")
    assert result["WINDOWS_PATH"]["count"] == 1


# ---------------------------------------------------------------------------
# Total count
# ---------------------------------------------------------------------------

def test_total_count_is_sum_of_categories():
    text = "email@example.com and https://site.com"
    result = detect_sensitive_entities(text)
    manual_total = sum(
        result[cat]["count"]
        for cat in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH")
    )
    assert result["total_sensitive_entity_count"] == manual_total


def test_total_count_with_multiple_types():
    text = "user@test.com called 555-123-4567 and visited https://example.com"
    result = detect_sensitive_entities(text)
    assert result["total_sensitive_entity_count"] >= 3
