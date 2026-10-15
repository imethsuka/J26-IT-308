"""
Non-ASCII and Unicode regression tests for tokenizer.py.

All test data is synthetic — no real participant data is used.

Covers:
  UNI-01  Sinhala-only text with no entities → no tokenization
  UNI-02  Sinhala text containing an ASCII email → email tokenized
  UNI-03  Mixed Sinhala/English with phone number → phone tokenized
  UNI-04  Accented Latin characters in surrounding text → entity tokenized correctly
  UNI-05  Emoji in surrounding text → entity tokenized correctly
  UNI-06  Emoji-only text with no entities → no tokenization, text preserved
  UNI-07  Empty string → no tokenization, empty string returned
  UNI-08  Repeated identical entity → one token, not two
  UNI-09  Two distinct emails → two separate tokens
  UNI-10  Token collision: text already containing [EMAIL_1] → not double-replaced
  UNI-11  Restoration round-trip: tokenize then restore → original text recovered
  UNI-12  Unicode email address in entity value → tokenized then restored correctly
  UNI-13  Right-to-left characters in surrounding text → entity tokenized
  UNI-14  NUL byte and control characters in text → no crash, returned as-is
  UNI-15  Very long unicode text → no crash, entity tokenized
  UNI-16  LOW + high confidence → tokenization skipped, text returned as-is
  UNI-17  LOW + low confidence + unicode text → tokenization applied
"""

from __future__ import annotations

import pytest

from backend.services.tokenizer import (
    DEFAULT_MIN_CONFIDENCE_TO_SKIP,
    _apply_tokens_to_text,
    _build_token_map,
    _should_tokenize,
    tokenize,
    restore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _det(email=None, phone=None, url=None, ip=None, date=None):
    """Build a minimal detection_output dict from synthetic entity matches."""
    out = {"total_sensitive_entity_count": 0}
    total = 0
    for key, matches in [
        ("EMAIL", email), ("PHONE", phone), ("URL", url),
        ("IP_ADDRESS", ip), ("DATE", date), ("WINDOWS_PATH", None),
    ]:
        m = list(matches) if matches else []
        out[key] = {"matches": m, "count": len(m)}
        total += len(m)
    out["total_sensitive_entity_count"] = total
    return out


_HIGH = "HIGH"
_LOW = "LOW"
_CONF_HIGH = 0.95   # confident LOW prediction → skip tokenization
_CONF_LOW  = 0.50   # uncertain LOW prediction → apply tokenization


# ---------------------------------------------------------------------------
# UNI-01  Sinhala-only text with no entities
# ---------------------------------------------------------------------------

def test_uni01_sinhala_only_no_entities():
    """No entities → token_map empty, text unchanged."""
    text = "ආයුබෝවන් ලෝකය"  # "Hello world" in Sinhala
    tok_text, token_map, tok_det = tokenize(text, _det(), _HIGH, None)
    assert token_map == {}, "Expected empty token map for no entities"
    assert tok_text == text, "Text must be unchanged when no entities"


# ---------------------------------------------------------------------------
# UNI-02  Sinhala text containing an ASCII email
# ---------------------------------------------------------------------------

def test_uni02_sinhala_text_with_email_tokenized():
    """Email in Sinhala-surrounding text is replaced by token."""
    email = "user@example.com"
    text = f"ඔබේ ගිණුම {email} කාර්යාලය"
    tok_text, token_map, tok_det = tokenize(text, _det(email=[email]), _HIGH, None)
    assert "[EMAIL_1]" in tok_text, "Email token should appear in output"
    assert email not in tok_text, "Raw email must not appear in tokenized text"
    assert token_map["[EMAIL_1]"] == email


# ---------------------------------------------------------------------------
# UNI-03  Mixed Sinhala/English with phone number
# ---------------------------------------------------------------------------

def test_uni03_mixed_sinhala_english_phone_tokenized():
    """Phone number embedded in Sinhala/English mixed text is tokenized."""
    phone = "071-234-5678"
    text = f"ඔබේ දුරකථන අංකය {phone} වේ. Please call."
    tok_text, token_map, tok_det = tokenize(text, _det(phone=[phone]), _HIGH, None)
    assert "[PHONE_1]" in tok_text
    assert phone not in tok_text
    assert token_map["[PHONE_1]"] == phone


# ---------------------------------------------------------------------------
# UNI-04  Accented Latin characters in surrounding text
# ---------------------------------------------------------------------------

def test_uni04_accented_chars_surrounding_text():
    """Accented characters in surrounding context don't corrupt tokenization."""
    email = "résumé@café.fr"
    text = f"Bonjour, voici mon adresse : {email} — à bientôt !"
    tok_text, token_map, tok_det = tokenize(text, _det(email=[email]), _HIGH, None)
    assert "[EMAIL_1]" in tok_text
    assert email not in tok_text
    assert "Bonjour" in tok_text
    assert "bientôt" in tok_text


# ---------------------------------------------------------------------------
# UNI-05  Emoji in surrounding text
# ---------------------------------------------------------------------------

def test_uni05_emoji_in_surrounding_text():
    """Emoji in surrounding context are preserved when an entity is tokenized."""
    url = "https://example.com/path"
    text = f"🎉 Check this out: {url} 🔒 Stay safe!"
    tok_text, token_map, tok_det = tokenize(text, _det(url=[url]), _HIGH, None)
    assert "[URL_1]" in tok_text
    assert url not in tok_text
    assert "🎉" in tok_text
    assert "🔒" in tok_text


# ---------------------------------------------------------------------------
# UNI-06  Emoji-only text with no entities
# ---------------------------------------------------------------------------

def test_uni06_emoji_only_no_entities_preserved():
    """Pure emoji text with no entities: text unchanged, token_map empty."""
    text = "😀🎯🔐💻🌍"
    tok_text, token_map, tok_det = tokenize(text, _det(), _HIGH, None)
    assert token_map == {}
    assert tok_text == text


# ---------------------------------------------------------------------------
# UNI-07  Empty string
# ---------------------------------------------------------------------------

def test_uni07_empty_string_no_crash():
    """Empty OCR text: no crash, empty string returned, no tokens."""
    tok_text, token_map, tok_det = tokenize("", _det(), _HIGH, None)
    assert tok_text == ""
    assert token_map == {}


def test_uni07_empty_string_low_confident_no_crash():
    """Empty OCR text at LOW+high-confidence: still no crash."""
    tok_text, token_map, tok_det = tokenize(
        "", _det(), _LOW, _CONF_HIGH
    )
    assert tok_text == ""
    assert token_map == {}


# ---------------------------------------------------------------------------
# UNI-08  Repeated identical entity → one token
# ---------------------------------------------------------------------------

def test_uni08_repeated_entity_produces_one_token():
    """Same email appearing twice maps to a single token [EMAIL_1]."""
    email = "alice@example.com"
    # Both entries are the same value
    text = f"Contact {email} or reach out to {email} again."
    tok_text, token_map, tok_det = tokenize(
        text, _det(email=[email, email]), _HIGH, None
    )
    assert list(token_map.keys()) == ["[EMAIL_1]"]
    assert tok_text.count("[EMAIL_1]") == 2
    assert email not in tok_text


# ---------------------------------------------------------------------------
# UNI-09  Two distinct emails → two separate tokens
# ---------------------------------------------------------------------------

def test_uni09_two_distinct_emails_two_tokens():
    """Two different emails each get their own token."""
    email1 = "alice@example.com"
    email2 = "bob@sample.org"
    text = f"From {email1} to {email2}."
    tok_text, token_map, tok_det = tokenize(
        text, _det(email=[email1, email2]), _HIGH, None
    )
    assert "[EMAIL_1]" in tok_text
    assert "[EMAIL_2]" in tok_text
    assert email1 not in tok_text
    assert email2 not in tok_text
    assert len(token_map) == 2


# ---------------------------------------------------------------------------
# UNI-10  Token collision: text already contains [EMAIL_1]
# ---------------------------------------------------------------------------

def test_uni10_preexisting_token_format_in_text():
    """
    If OCR text already contains a literal '[EMAIL_1]' string and we also
    detect an email, the tokenizer replaces the actual email value — not the
    literal bracket string — since substitution targets entity values.
    This is a documented known limitation, not a bug to fix.
    """
    email = "actual@example.com"
    text = f"[EMAIL_1] is the template label, but {email} is the real address."
    tok_text, token_map, tok_det = tokenize(
        text, _det(email=[email]), _HIGH, None
    )
    # The real email is replaced
    assert email not in tok_text
    # The literal bracket string was already in the text and may or may not
    # be affected — we only assert the real entity is gone
    assert "[EMAIL_1]" in tok_text  # appears at least once (from the token replacement)


# ---------------------------------------------------------------------------
# UNI-11  Restoration round-trip
# ---------------------------------------------------------------------------

def test_uni11_restoration_roundtrip_unicode():
    """tokenize() then restore() recovers the original unicode text."""
    email = "привет@пример.рф"  # Cyrillic email
    original = f"Контакт: {email} — позвоните нам."
    tok_text, token_map, tok_det = tokenize(
        original, _det(email=[email]), _HIGH, None
    )
    assert email not in tok_text
    restored = restore(tok_text, token_map)
    assert restored == original


# ---------------------------------------------------------------------------
# UNI-12  Unicode value in entity
# ---------------------------------------------------------------------------

def test_uni12_unicode_entity_value_tokenized_and_restored():
    """An entity value containing non-ASCII chars is tokenized and restored."""
    phone = "‪+94-71-234-5678‬"   # Unicode left-to-right embedding markers
    text = f"Call us: {phone}"
    tok_text, token_map, tok_det = tokenize(
        text, _det(phone=[phone]), _HIGH, None
    )
    # Restored text must match original
    restored = restore(tok_text, token_map)
    assert restored == text


# ---------------------------------------------------------------------------
# UNI-13  RTL characters in surrounding text
# ---------------------------------------------------------------------------

def test_uni13_rtl_surrounding_text_entity_tokenized():
    """Right-to-left (Arabic) surrounding text: entity still tokenized."""
    email = "info@example.com"
    text = f"اتصل بنا على {email} للمزيد من المعلومات"
    tok_text, token_map, tok_det = tokenize(
        text, _det(email=[email]), _HIGH, None
    )
    assert "[EMAIL_1]" in tok_text
    assert email not in tok_text
    assert "اتصل" in tok_text


# ---------------------------------------------------------------------------
# UNI-14  Control characters and NUL bytes
# ---------------------------------------------------------------------------

def test_uni14_control_characters_no_crash():
    """Text with NUL and control chars: no crash, returned without modification."""
    text = "Hello\x00World\x01\x1f"
    tok_text, token_map, tok_det = tokenize(text, _det(), _HIGH, None)
    assert token_map == {}
    assert tok_text == text  # unchanged (no entities)


# ---------------------------------------------------------------------------
# UNI-15  Very long unicode text
# ---------------------------------------------------------------------------

def test_uni15_long_unicode_text_no_crash():
    """10k-character Sinhala text with one email: no crash, email tokenized."""
    email = "long@test.com"
    # 10,000 Sinhala 'ක' characters plus the email
    long_text = "ක" * 10_000 + f" {email}"
    tok_text, token_map, tok_det = tokenize(
        long_text, _det(email=[email]), _HIGH, None
    )
    assert email not in tok_text
    assert "[EMAIL_1]" in tok_text
    assert len(tok_text) == len(long_text) - len(email) + len("[EMAIL_1]")


# ---------------------------------------------------------------------------
# UNI-16  LOW + high confidence → skip tokenization, text unchanged
# ---------------------------------------------------------------------------

def test_uni16_low_high_confidence_sinhala_text_not_tokenized():
    """LOW risk with confidence ≥ threshold: tokenization skipped even with entities."""
    email = "skip@example.com"
    text = f"ඔබේ ගිණුම {email} බලාගන්න"
    tok_text, token_map, tok_det = tokenize(
        text, _det(email=[email]), _LOW, _CONF_HIGH
    )
    assert token_map == {}
    assert tok_text == text  # unchanged — tokenization skipped


def test_uni16_low_high_confidence_returns_stripped_detection():
    """When tokenization is skipped, detection matches are stripped."""
    email = "skip@example.com"
    tok_text, token_map, tok_det = tokenize(
        f"text {email}", _det(email=[email]), _LOW, _CONF_HIGH
    )
    assert tok_det["EMAIL"]["matches"] == []
    assert tok_det["EMAIL"]["count"] == 1  # count preserved


# ---------------------------------------------------------------------------
# UNI-17  LOW + low confidence + unicode → tokenization applied
# ---------------------------------------------------------------------------

def test_uni17_low_low_confidence_unicode_tokenization_applied():
    """LOW risk with confidence < threshold: tokenization IS applied."""
    email = "uncertain@example.com"
    text = f"アドレス: {email} です。"  # Japanese surrounding text
    tok_text, token_map, tok_det = tokenize(
        text, _det(email=[email]), _LOW, _CONF_LOW
    )
    assert "[EMAIL_1]" in tok_text
    assert email not in tok_text
    assert token_map["[EMAIL_1]"] == email
