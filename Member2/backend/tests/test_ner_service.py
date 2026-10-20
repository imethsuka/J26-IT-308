"""
Tests for backend.services.ner_service — contextual NER.

NER-01  Email with label prefix gets boosted confidence (>= 0.75 base).
NER-02  Phone with label prefix gets boosted confidence.
NER-03  Entity without context gets base confidence (0.75).
NER-04  Negative context ("example email:") reduces confidence.
NER-05  All six entity types are returned in scored output.
NER-06  scored_matches list length matches matches list length.
NER-07  Total entity count matches sum of per-category counts.
NER-08  ner_method field present in output.
NER-09  high_confidence_entities filters below threshold correctly.
NER-10  Empty string produces no matches and no crash.
NER-11  Unicode text handled correctly.
NER-12  detect_entities_with_scores raises TypeError on non-string input.
"""

from __future__ import annotations

import pytest

from backend.services.ner_service import detect_entities_with_scores, high_confidence_entities


# ---------------------------------------------------------------------------
# NER-01  Email with label prefix → boosted confidence
# ---------------------------------------------------------------------------

def test_ner01_email_label_prefix_boosts_confidence():
    text = "email: alice@example.com please reply"
    result = detect_entities_with_scores(text)
    scored = result["EMAIL"]["scored_matches"]
    assert len(scored) == 1
    assert scored[0]["confidence"] > 0.75, (
        "Email with 'email:' prefix should have boosted confidence above base 0.75"
    )
    assert scored[0]["context_trigger"] is not None


# ---------------------------------------------------------------------------
# NER-02  Phone with label prefix
# ---------------------------------------------------------------------------

def test_ner02_phone_label_prefix_boosts_confidence():
    text = "phone: 555-123-4567 call us"
    result = detect_entities_with_scores(text)
    scored = result["PHONE"]["scored_matches"]
    assert len(scored) == 1
    assert scored[0]["confidence"] > 0.75


# ---------------------------------------------------------------------------
# NER-03  No context → base confidence 0.75
# ---------------------------------------------------------------------------

def test_ner03_no_context_base_confidence():
    text = "alice@example.com"
    result = detect_entities_with_scores(text)
    scored = result["EMAIL"]["scored_matches"]
    assert len(scored) == 1
    assert scored[0]["confidence"] == pytest.approx(0.75, abs=0.01)
    assert scored[0]["context_trigger"] is None


# ---------------------------------------------------------------------------
# NER-04  Negative context reduces confidence
# ---------------------------------------------------------------------------

def test_ner04_negative_context_reduces_confidence():
    # "sample email:" should lower confidence below base
    text = "this is a sample email: alice@example.com for testing"
    result = detect_entities_with_scores(text)
    scored = result["EMAIL"]["scored_matches"]
    assert len(scored) == 1
    # Must be lower than base 0.75 due to negative trigger
    assert scored[0]["confidence"] < 0.75


# ---------------------------------------------------------------------------
# NER-05  All six entity types always present
# ---------------------------------------------------------------------------

def test_ner05_all_six_categories_present():
    result = detect_entities_with_scores("hello world")
    for cat in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"):
        assert cat in result, f"Category {cat} missing from output"


def test_ner05b_credential_categories_are_scored():
    result = detect_entities_with_scores(
        "password: Synthetic-Pass_42! api_key=synthetickey_1234567890abcd"
    )
    assert result["PASSWORD"]["scored_matches"][0]["confidence"] > 0.75
    assert result["API_KEY"]["scored_matches"][0]["confidence"] > 0.75


# ---------------------------------------------------------------------------
# NER-06  scored_matches length matches matches length
# ---------------------------------------------------------------------------

def test_ner06_scored_matches_length_consistent():
    text = "alice@example.com and bob@test.org are both here"
    result = detect_entities_with_scores(text)
    cat = result["EMAIL"]
    assert len(cat["scored_matches"]) == len(cat["matches"])


# ---------------------------------------------------------------------------
# NER-07  Total count sum
# ---------------------------------------------------------------------------

def test_ner07_total_count_matches_sum():
    text = "email: alice@example.com phone: 555-123-4567"
    result = detect_entities_with_scores(text)
    total = result["total_sensitive_entity_count"]
    per_cat_sum = sum(
        result[c]["count"]
        for c in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH")
    )
    assert total == per_cat_sum


# ---------------------------------------------------------------------------
# NER-08  ner_method field
# ---------------------------------------------------------------------------

def test_ner08_ner_method_field_present():
    result = detect_entities_with_scores("anything")
    assert result.get("ner_method") == "regex+contextual_heuristic"


# ---------------------------------------------------------------------------
# NER-09  high_confidence_entities threshold filter
# ---------------------------------------------------------------------------

def test_ner09_high_confidence_entities_filters_correctly():
    # Base confidence is 0.75; threshold 0.80 should filter it out
    text = "alice@example.com"
    scored = detect_entities_with_scores(text)
    high = high_confidence_entities(scored, threshold=0.80)
    # No context boost → confidence 0.75, filtered out at 0.80
    assert "EMAIL" not in high

    # With email: prefix, confidence > 0.75+boost (0.20) = 0.95 → passes at 0.80
    text2 = "email: alice@example.com"
    scored2 = detect_entities_with_scores(text2)
    high2 = high_confidence_entities(scored2, threshold=0.80)
    assert "EMAIL" in high2


# ---------------------------------------------------------------------------
# NER-10  Empty string
# ---------------------------------------------------------------------------

def test_ner10_empty_string_no_crash():
    result = detect_entities_with_scores("")
    assert result["total_sensitive_entity_count"] == 0
    for cat in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"):
        assert result[cat]["count"] == 0
        assert result[cat]["scored_matches"] == []
    assert result["EMAIL_OBFUSCATED"]["count"] == 0


# ---------------------------------------------------------------------------
# NER-11  Unicode text
# ---------------------------------------------------------------------------

def test_ner11_unicode_text_handled():
    text = "ඔබේ ගිණුම user@example.com බලාගන්න"
    result = detect_entities_with_scores(text)
    assert result["EMAIL"]["count"] == 1
    assert result["EMAIL"]["scored_matches"][0]["value"] == "user@example.com"


# ---------------------------------------------------------------------------
# NER-12  TypeError on non-string
# ---------------------------------------------------------------------------

def test_ner12_non_string_raises_type_error():
    with pytest.raises(TypeError):
        detect_entities_with_scores(12345)
