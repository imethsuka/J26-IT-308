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


def test_obfuscated_emails_are_detected():
    result = detect_sensitive_entities(
        "email: alice [at] example [dot] com and bob(at)test(dot)org"
    )
    assert result["EMAIL_OBFUSCATED"]["count"] == 2


def test_ocr_style_email_substitution_with_obfuscation_is_detected():
    result = detect_sensitive_entities("alice [at] examp1e [dot] com")
    assert result["EMAIL_OBFUSCATED"]["count"] == 1


def test_spaced_email_is_detected():
    result = detect_sensitive_entities("contact alice @ example . com")
    assert result["EMAIL_OBFUSCATED"]["matches"] == ["alice @ example . com"]


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


def test_date_detected_slash_format():
    result = detect_sensitive_entities("Saved on 12/25/2024 at 3pm")
    assert result["DATE"]["count"] == 1


def test_date_ui_timestamp_triggers_detection():
    """
    DATE-FP-01 — UI timestamps (e.g. message times, file dates shown in
    desktop screenshots) trigger the DATE regex.

    This is a KNOWN LIMITATION: screenshots routinely show timestamps like
    '10/14/2026' as part of UI chrome (file browser, chat apps, etc.).
    These match the date pattern but are not annotator-defined sensitive entities.

    The detection is technically correct; the false-positive rate arises from
    a gap between the regex definition and the annotation policy.
    This test documents the behaviour so it cannot regress silently.
    """
    ui_text = "Last modified: 10/14/2026  Size: 45 KB"
    result = detect_sensitive_entities(ui_text)
    # Confirm the detection fires (documenting known behaviour)
    assert result["DATE"]["count"] >= 1, (
        "DATE regex should match UI-style date timestamps — known false-positive source"
    )


def test_date_version_number_not_detected():
    """
    Version numbers like '2.14.1' (only 3 parts, no separator) or
    '14.1' should NOT match the date regex (requires exactly d/d/d or d-d-d).
    """
    result = detect_sensitive_entities("Python 3.14.1 released")
    assert result["DATE"]["count"] == 0, (
        "Short version numbers like 3.14.1 must not be detected as dates"
    )


# ---------------------------------------------------------------------------
# WINDOWS_PATH
# ---------------------------------------------------------------------------

def test_windows_path_detected():
    result = detect_sensitive_entities(r"File is at C:\Users\alice\Documents\report.pdf")
    assert result["WINDOWS_PATH"]["count"] == 1


def test_credential_like_patterns_are_detected_as_synthetic_values():
    text = (
        "password: Synth-Pass_42! "
        "api_key=sk_test_1234567890abcdef "
        "Authorization: Bearer syntheticBearerToken_1234567890 "
        "JWT eyJhbGciOiJIUzI1NiJ9.payload.signature "
        "AWS AKIAABCDEFGHIJKLMNOP "
        "Google AIza12345678901234567890123456789012345 "
        "GitHub ghp_123456789012345678901234"
    )
    result = detect_sensitive_entities(text)
    for category in (
        "PASSWORD", "API_KEY", "BEARER_TOKEN", "JWT",
        "AWS_ACCESS_KEY", "GOOGLE_API_KEY", "GITHUB_TOKEN",
    ):
        assert result[category]["count"] == 1, category


def test_credential_labels_without_values_are_not_detected():
    result = detect_sensitive_entities(
        "The password policy describes API keys and bearer authentication."
    )
    assert result["PASSWORD"]["count"] == 0
    assert result["API_KEY"]["count"] == 0
    assert result["BEARER_TOKEN"]["count"] == 0


def test_multiple_categories_and_overlapping_values_are_counted():
    result = detect_sensitive_entities(
        "api_key=synthetickey_1234567890abcd AWS AKIAABCDEFGHIJKLMNOP"
    )
    assert result["API_KEY"]["count"] == 1
    assert result["AWS_ACCESS_KEY"]["count"] == 1
    assert result["total_sensitive_entity_count"] >= 2


def test_duplicate_sensitive_values_are_retained_for_audit_counts():
    result = detect_sensitive_entities("alice@example.com alice@example.com")
    assert result["EMAIL"]["count"] == 2
    assert result["EMAIL"]["matches"] == ["alice@example.com", "alice@example.com"]


def test_ocr_style_substitution_is_not_overmatched():
    result = detect_sensitive_entities("ordinary alice0example.com")
    assert result["EMAIL"]["count"] == 0
    assert result["EMAIL_OBFUSCATED"]["count"] == 0


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
