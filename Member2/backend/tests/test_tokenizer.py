"""
Unit tests for the CAPRLM reversible tokenization service (T5).

Coverage:
- Round-trip restoration for all six entity categories
- Duplicate values within a category (one token, multiple replacements)
- Multiple distinct values within a category
- Empty detection output
- Partial detection output (some categories absent or zero-count)
- Risk-adaptive gating (HIGH/CRITICAL/MEDIUM always tokenize;
  LOW behaviour depends on confidence vs. threshold)
- Token format validation
- Absence of original sensitive values in tokenized output
- Restoration with a mismatched or empty map (graceful pass-through)
- token_map not returned in API response (via API-level test)
"""

import pytest
from backend.services.tokenizer import (
    tokenize,
    restore,
    DEFAULT_MIN_CONFIDENCE_TO_SKIP,
    _should_tokenize,
    _build_token_map,
    _strip_matches,
)


# ---------------------------------------------------------------------------
# Fixtures — minimal detection_output structures
# ---------------------------------------------------------------------------

def _det(*, email=None, phone=None, url=None, ip=None, date=None, path=None):
    """Build a detection_output dict with the given match lists."""
    cats = {
        "EMAIL":        email or [],
        "PHONE":        phone or [],
        "URL":          url or [],
        "IP_ADDRESS":   ip or [],
        "DATE":         date or [],
        "WINDOWS_PATH": path or [],
    }
    total = sum(len(v) for v in cats.values())
    result = {k: {"matches": v, "count": len(v)} for k, v in cats.items()}
    result["total_sensitive_entity_count"] = total
    return result


# ---------------------------------------------------------------------------
# 1. Round-trip: tokenize then restore → original text
# ---------------------------------------------------------------------------

def test_round_trip_single_email():
    text = "Contact alice@example.com for details."
    det = _det(email=["alice@example.com"])
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.9)
    assert "alice@example.com" not in tok_text
    assert restore(tok_text, tmap) == text


def test_round_trip_single_phone():
    text = "Call 555-867-5309 now."
    det = _det(phone=["555-867-5309"])
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.9)
    assert restore(tok_text, tmap) == text


def test_round_trip_single_url():
    text = "Visit https://example.com/page for more."
    det = _det(url=["https://example.com/page"])
    tok_text, tmap, _ = tokenize(text, det, "MEDIUM", 0.5)
    assert restore(tok_text, tmap) == text


def test_round_trip_single_ip():
    text = "Server at 192.168.1.100."
    det = _det(ip=["192.168.1.100"])
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.8)
    assert restore(tok_text, tmap) == text


def test_round_trip_single_date():
    text = "Meeting on 2024-03-15."
    det = _det(date=["2024-03-15"])
    tok_text, tmap, _ = tokenize(text, det, "MEDIUM", 0.6)
    assert restore(tok_text, tmap) == text


def test_round_trip_single_windows_path():
    text = r"Open C:\Users\alice\Documents\report.pdf here."
    det = _det(path=[r"C:\Users\alice\Documents\report.pdf"])
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.95)
    assert restore(tok_text, tmap) == text


# ---------------------------------------------------------------------------
# 2. All six categories in one call
# ---------------------------------------------------------------------------

def test_round_trip_all_six_categories():
    text = (
        "Email: alice@corp.com  Phone: 555-123-4567  "
        "URL: https://corp.com  IP: 10.0.0.1  "
        r"Date: 2024-01-01  Path: C:\Users\alice\file.txt"
    )
    det = _det(
        email=["alice@corp.com"],
        phone=["555-123-4567"],
        url=["https://corp.com"],
        ip=["10.0.0.1"],
        date=["2024-01-01"],
        path=[r"C:\Users\alice\file.txt"],
    )
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.99)
    assert len(tmap) == 6
    assert restore(tok_text, tmap) == text


# ---------------------------------------------------------------------------
# 3. Duplicate value same category → one token, all occurrences replaced
# ---------------------------------------------------------------------------

def test_duplicate_value_one_token():
    text = "alice@example.com and alice@example.com are the same."
    # detection returns the value once even if it appears twice in text
    det = _det(email=["alice@example.com"])
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.9)
    # Both occurrences replaced
    assert tok_text.count("[EMAIL_1]") == 2
    # One map entry
    assert len(tmap) == 1
    # Round-trip
    assert restore(tok_text, tmap) == text


def test_duplicate_value_in_matches_list_deduped():
    """If the matches list itself contains duplicates, only one token is created."""
    text = "alice@example.com appears."
    det = _det(email=["alice@example.com", "alice@example.com"])
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.9)
    assert len(tmap) == 1
    assert "[EMAIL_1]" in tok_text


# ---------------------------------------------------------------------------
# 4. Multiple distinct values same category
# ---------------------------------------------------------------------------

def test_multiple_distinct_emails():
    text = "From: alice@example.com To: bob@corp.com"
    det = _det(email=["alice@example.com", "bob@corp.com"])
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.9)
    assert "alice@example.com" not in tok_text
    assert "bob@corp.com" not in tok_text
    assert "[EMAIL_1]" in tok_text
    assert "[EMAIL_2]" in tok_text
    assert len(tmap) == 2
    assert restore(tok_text, tmap) == text


# ---------------------------------------------------------------------------
# 5. Empty detection output
# ---------------------------------------------------------------------------

def test_empty_detection_no_change():
    text = "Nothing sensitive here."
    det = _det()
    tok_text, tmap, tok_det = tokenize(text, det, "HIGH", 0.9)
    assert tok_text == text
    assert tmap == {}
    assert tok_det["total_sensitive_entity_count"] == 0


def test_zero_count_categories_no_tokens():
    text = "Plain text."
    det = _det(email=[], phone=[])
    tok_text, tmap, _ = tokenize(text, det, "MEDIUM", 0.5)
    assert tmap == {}
    assert tok_text == text


# ---------------------------------------------------------------------------
# 6. Partial detection (only some categories populated)
# ---------------------------------------------------------------------------

def test_partial_only_email_category():
    text = "Contact test@example.com for help."
    det = _det(email=["test@example.com"])
    tok_text, tmap, tok_det = tokenize(text, det, "HIGH", 0.9)
    # Only EMAIL token present
    assert any(k.startswith("[EMAIL_") for k in tmap)
    assert not any(k.startswith("[PHONE_") for k in tmap)
    assert not any(k.startswith("[URL_") for k in tmap)
    # detection output structure preserved
    assert "EMAIL" in tok_det
    assert "PHONE" in tok_det


# ---------------------------------------------------------------------------
# 7. Token format
# ---------------------------------------------------------------------------

def test_token_format_email():
    det = _det(email=["a@b.com", "c@d.com"])
    tmap = _build_token_map(det)
    tokens = list(tmap.keys())
    assert "[EMAIL_1]" in tokens
    assert "[EMAIL_2]" in tokens


def test_token_format_all_categories():
    import re
    pattern = re.compile(r"^\[([A-Z_]+)_(\d+)\]$")
    det = _det(
        email=["e@x.com"],
        phone=["555-000-0001"],
        url=["https://x.com"],
        ip=["1.2.3.4"],
        date=["2024-01-01"],
        path=[r"C:\f.txt"],
    )
    tmap = _build_token_map(det)
    for token in tmap:
        assert pattern.match(token), f"Token '{token}' does not match expected format"


# ---------------------------------------------------------------------------
# 8. No original sensitive values in tokenized output
# ---------------------------------------------------------------------------

def test_no_original_values_in_tokenized_text():
    text = "Email alice@corp.com, phone 555-999-1234, IP 10.0.0.5."
    det = _det(
        email=["alice@corp.com"],
        phone=["555-999-1234"],
        ip=["10.0.0.5"],
    )
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.9)
    for original in tmap.values():
        assert original not in tok_text, f"Original value '{original}' still in tokenized text"


def test_no_original_values_in_tokenized_detection():
    text = "Email bob@example.com here."
    det = _det(email=["bob@example.com"])
    _, tmap, tok_det = tokenize(text, det, "HIGH", 0.9)
    for matches_list in [
        tok_det["EMAIL"]["matches"],
    ]:
        for item in matches_list:
            assert item not in tmap.values(), (
                f"Original value '{item}' found in tokenized detection matches"
            )


# ---------------------------------------------------------------------------
# 9. Risk-adaptive gating
# ---------------------------------------------------------------------------

def test_high_risk_always_tokenizes():
    det = _det(email=["a@b.com"])
    _, tmap, _ = tokenize("a@b.com", det, "HIGH", 0.0)
    assert len(tmap) == 1


def test_critical_risk_always_tokenizes():
    det = _det(email=["a@b.com"])
    _, tmap, _ = tokenize("a@b.com", det, "CRITICAL", 0.0)
    assert len(tmap) == 1


def test_medium_risk_always_tokenizes():
    det = _det(phone=["555-000-0000"])
    _, tmap, _ = tokenize("555-000-0000", det, "MEDIUM", 0.4)
    assert len(tmap) == 1


def test_low_confident_skips_tokenization():
    """LOW with confidence >= threshold → tokenization skipped."""
    det = _det(email=["a@b.com"])
    text = "a@b.com"
    tok_text, tmap, _ = tokenize(
        text, det, "LOW", DEFAULT_MIN_CONFIDENCE_TO_SKIP, DEFAULT_MIN_CONFIDENCE_TO_SKIP
    )
    assert tmap == {}
    assert tok_text == text


def test_low_uncertain_tokenizes():
    """LOW with confidence below threshold → tokenization applied."""
    det = _det(email=["a@b.com"])
    tok_text, tmap, _ = tokenize(
        "a@b.com", det, "LOW", DEFAULT_MIN_CONFIDENCE_TO_SKIP - 0.01
    )
    assert len(tmap) == 1


def test_low_none_confidence_tokenizes():
    """LOW with confidence=None → fail-safe, always tokenize."""
    det = _det(email=["a@b.com"])
    _, tmap, _ = tokenize("a@b.com", det, "LOW", None)
    assert len(tmap) == 1


def test_unavailable_risk_tokenizes():
    """UNAVAILABLE risk level (model not loaded) → fail-safe, always tokenize."""
    det = _det(email=["a@b.com"])
    _, tmap, _ = tokenize("a@b.com", det, "UNAVAILABLE", None)
    assert len(tmap) == 1


def test_custom_threshold_honoured():
    det = _det(email=["a@b.com"])
    text = "a@b.com"
    # confidence 0.5 < threshold 0.6 → tokenize
    _, tmap1, _ = tokenize(text, det, "LOW", 0.5, min_confidence_to_skip=0.6)
    assert len(tmap1) == 1
    # confidence 0.7 >= threshold 0.6 → skip
    _, tmap2, _ = tokenize(text, det, "LOW", 0.7, min_confidence_to_skip=0.6)
    assert tmap2 == {}


# ---------------------------------------------------------------------------
# 10. _should_tokenize unit tests
# ---------------------------------------------------------------------------

def test_should_tokenize_high():
    assert _should_tokenize("HIGH", 0.9, 0.75) is True


def test_should_tokenize_critical():
    assert _should_tokenize("CRITICAL", 0.9, 0.75) is True


def test_should_tokenize_medium():
    assert _should_tokenize("MEDIUM", 0.5, 0.75) is True


def test_should_tokenize_low_high_confidence():
    assert _should_tokenize("LOW", 0.9, 0.75) is False


def test_should_tokenize_low_low_confidence():
    assert _should_tokenize("LOW", 0.5, 0.75) is True


def test_should_tokenize_low_exact_threshold():
    # confidence == threshold → skip (not strictly less than)
    assert _should_tokenize("LOW", 0.75, 0.75) is False


def test_should_tokenize_low_none_confidence():
    assert _should_tokenize("LOW", None, 0.75) is True


def test_should_tokenize_unavailable():
    assert _should_tokenize("UNAVAILABLE", None, 0.75) is True


# ---------------------------------------------------------------------------
# 11. Restoration with wrong or empty map (graceful pass-through)
# ---------------------------------------------------------------------------

def test_restore_with_empty_map_returns_text_unchanged():
    tok_text = "Some [EMAIL_1] and [PHONE_1] here."
    result = restore(tok_text, {})
    assert result == tok_text


def test_restore_with_unrelated_map_leaves_unknown_tokens():
    tok_text = "Contact [EMAIL_1] please."
    wrong_map = {"[PHONE_1]": "555-000-0000"}  # wrong token
    result = restore(tok_text, wrong_map)
    assert "[EMAIL_1]" in result  # unknown token left unchanged
    assert result == tok_text


def test_restore_partial_map():
    tok_text = "[EMAIL_1] and [PHONE_1]"
    partial_map = {"[EMAIL_1]": "alice@example.com"}
    result = restore(tok_text, partial_map)
    assert "alice@example.com" in result
    assert "[PHONE_1]" in result  # not in map → left unchanged


# ---------------------------------------------------------------------------
# 12. Tokenized detection output structure
# ---------------------------------------------------------------------------

def test_tokenized_detection_preserves_counts():
    det = _det(email=["a@b.com", "c@d.com"])
    _, _, tok_det = tokenize("a@b.com c@d.com", det, "HIGH", 0.9)
    assert tok_det["EMAIL"]["count"] == 2
    assert tok_det["total_sensitive_entity_count"] == 2


def test_tokenized_detection_matches_are_tokens_not_originals():
    det = _det(email=["secret@corp.com"])
    _, _, tok_det = tokenize("secret@corp.com", det, "HIGH", 0.9)
    assert "secret@corp.com" not in tok_det["EMAIL"]["matches"]
    assert tok_det["EMAIL"]["matches"][0].startswith("[EMAIL_")


def test_tokenized_detection_empty_categories_unchanged():
    det = _det(email=["a@b.com"])
    _, _, tok_det = tokenize("a@b.com", det, "HIGH", 0.9)
    assert tok_det["PHONE"]["matches"] == []
    assert tok_det["PHONE"]["count"] == 0


# ---------------------------------------------------------------------------
# 13. Type validation
# ---------------------------------------------------------------------------

def test_tokenize_rejects_non_string_ocr_text():
    with pytest.raises(TypeError):
        tokenize(12345, _det(), "HIGH", 0.9)  # type: ignore[arg-type]


def test_tokenize_rejects_non_mapping_detection():
    with pytest.raises(TypeError):
        tokenize("text", ["not", "a", "mapping"], "HIGH", 0.9)  # type: ignore[arg-type]


def test_restore_rejects_non_string():
    with pytest.raises(TypeError):
        restore(42, {})  # type: ignore[arg-type]


def test_restore_rejects_non_dict_map():
    with pytest.raises(TypeError):
        restore("text", "not_a_dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 14. Longer-match-first substitution (substring safety)
# ---------------------------------------------------------------------------

def test_longer_value_replaced_before_shorter_substring():
    """
    If 'example.com' were in URL matches AND 'alice@example.com' were in
    EMAIL matches, the shorter string must not corrupt the longer one.
    """
    text = "Mail alice@example.com, site https://example.com"
    det = _det(
        email=["alice@example.com"],
        url=["https://example.com"],
    )
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.9)
    # Both originals absent from tokenized text
    assert "alice@example.com" not in tok_text
    assert "https://example.com" not in tok_text
    # Round-trip
    assert restore(tok_text, tmap) == text


# ---------------------------------------------------------------------------
# 15. Issue 1 fix — skipped tokenization must not expose raw matches
# ---------------------------------------------------------------------------

def test_low_confident_skip_strips_matches_from_detection():
    """When tokenization is skipped (LOW + confident), matches must be empty."""
    det = _det(
        email=["alice@corp.com"],
        phone=["555-123-4567"],
        ip=["192.168.1.1"],
    )
    tok_text, tmap, tok_det = tokenize(
        "alice@corp.com 555-123-4567 192.168.1.1",
        det,
        "LOW",
        DEFAULT_MIN_CONFIDENCE_TO_SKIP,  # confidence >= threshold → skip
        DEFAULT_MIN_CONFIDENCE_TO_SKIP,
    )
    assert tmap == {}
    # Raw sensitive values must not appear in the returned detection output
    assert tok_det["EMAIL"]["matches"] == []
    assert tok_det["PHONE"]["matches"] == []
    assert tok_det["IP_ADDRESS"]["matches"] == []


def test_low_confident_skip_preserves_counts():
    """Counts in detection output are preserved even when matches are stripped."""
    det = _det(email=["alice@corp.com", "bob@corp.com"], phone=["555-000-0000"])
    _, tmap, tok_det = tokenize(
        "alice@corp.com bob@corp.com 555-000-0000",
        det,
        "LOW",
        DEFAULT_MIN_CONFIDENCE_TO_SKIP,
        DEFAULT_MIN_CONFIDENCE_TO_SKIP,
    )
    assert tmap == {}
    assert tok_det["EMAIL"]["count"] == 2
    assert tok_det["PHONE"]["count"] == 1
    assert tok_det["total_sensitive_entity_count"] == 3


def test_low_confident_skip_ocr_text_unchanged():
    """The returned OCR text is unchanged when tokenization is skipped."""
    text = "Contact alice@corp.com for help."
    det = _det(email=["alice@corp.com"])
    tok_text, tmap, _ = tokenize(
        text,
        det,
        "LOW",
        DEFAULT_MIN_CONFIDENCE_TO_SKIP,
        DEFAULT_MIN_CONFIDENCE_TO_SKIP,
    )
    assert tok_text == text
    assert tmap == {}


def test_strip_matches_helper_directly():
    """_strip_matches preserves structure, counts, and total while clearing values."""
    det = _det(email=["secret@corp.com"], phone=["999-888-7777"])
    stripped = _strip_matches(det)
    assert stripped["EMAIL"]["matches"] == []
    assert stripped["EMAIL"]["count"] == 1
    assert stripped["PHONE"]["matches"] == []
    assert stripped["PHONE"]["count"] == 1
    assert stripped["total_sensitive_entity_count"] == 2
    assert "secret@corp.com" not in str(stripped)
    assert "999-888-7777" not in str(stripped)


# ---------------------------------------------------------------------------
# 16. Issue 2 fix — restore() ordering safe with 10+ unique values
# ---------------------------------------------------------------------------

def _make_12_email_det():
    """Return detection output and source text with 12 distinct email addresses."""
    emails = [
        "user01@alpha.com",
        "user02@beta.com",
        "user03@gamma.com",
        "user04@delta.com",
        "user05@epsilon.com",
        "user06@zeta.com",
        "user07@eta.com",
        "user08@theta.com",
        "user09@iota.com",
        "user10@kappa.com",
        "user11@lambda.com",
        "user12@mu.com",
    ]
    text = "  ".join(emails)
    det = _det(email=emails)
    return text, det, emails


def test_restore_12_unique_emails_round_trip():
    """12 distinct emails must survive tokenize → restore without corruption."""
    text, det, emails = _make_12_email_det()
    tok_text, tmap, _ = tokenize(text, det, "HIGH", 0.99)

    assert len(tmap) == 12

    # All originals absent from tokenized text
    for addr in emails:
        assert addr not in tok_text, f"Original address {addr!r} still in tokenized text"

    # Tokens EMAIL_1 through EMAIL_12 must all be present
    for n in range(1, 13):
        assert f"[EMAIL_{n}]" in tok_text, f"[EMAIL_{n}] missing from tokenized text"

    # Full round-trip must reproduce the original text exactly
    restored = restore(tok_text, tmap)
    assert restored == text, f"Round-trip failed.\nExpected: {text!r}\nGot:      {restored!r}"


def test_restore_token_1_does_not_corrupt_token_10():
    """
    [EMAIL_1] is a substring of [EMAIL_10].
    restore() must replace [EMAIL_10] before [EMAIL_1] to avoid corruption.
    """
    token_map = {
        "[EMAIL_1]":  "first@example.com",
        "[EMAIL_10]": "tenth@example.com",
    }
    tokenized = "[EMAIL_10] and [EMAIL_1]"
    restored = restore(tokenized, token_map)
    assert restored == "tenth@example.com and first@example.com", (
        f"Restoration order corrupted output: {restored!r}"
    )


def test_restore_longer_tokens_replaced_first():
    """Tokens with longer names are replaced before shorter substring tokens."""
    token_map = {
        "[EMAIL_1]":  "a@x.com",
        "[EMAIL_11]": "b@x.com",
        "[EMAIL_12]": "c@x.com",
    }
    tokenized = "[EMAIL_11] [EMAIL_12] [EMAIL_1]"
    restored = restore(tokenized, token_map)
    assert restored == "b@x.com c@x.com a@x.com"
