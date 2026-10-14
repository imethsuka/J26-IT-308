"""
CAPRLM reversible tokenization service — T5 prototype.

Replaces detected sensitive entity values in OCR text with category-scoped
placeholder tokens.  The restoration mapping is EPHEMERAL: it is created
per-request, used within the same request to mask entity values in the API
response, and then discarded.  It is never stored, logged, or returned to
the client.

Risk-adaptive behaviour
-----------------------
The tokenizer inspects the ML model's predicted risk_level and confidence
value to decide whether to apply tokenization:

  HIGH or CRITICAL risk               → always tokenize
  MEDIUM risk                         → always tokenize
  UNAVAILABLE risk (model not loaded) → always tokenize (fail-safe)
  LOW risk, confidence is None        → tokenize (fail-safe)
  LOW risk, confidence < threshold    → tokenize (uncertain prediction)
  LOW risk, confidence >= threshold   → skip (confident safe prediction)

The default skip threshold (DEFAULT_MIN_CONFIDENCE_TO_SKIP = 0.75) is a
design choice, NOT an empirically optimised value.  It is configurable per
call-site.

Confidence semantics
--------------------
Confidence is the probability mass of the predicted risk bucket as returned
by predict_privacy_risk() in risk_prediction.py:
  LOW    prediction → confidence = P(LOW)
  MEDIUM prediction → confidence = P(MEDIUM) + P(HIGH) + P(CRITICAL)
  HIGH   prediction → confidence = P(HIGH)   + P(CRITICAL)
Values are in [0, 1].  These values are threshold-adjusted and are NOT
calibrated class probabilities; they should not be interpreted as absolute
probability estimates.

Security properties and limitations
------------------------------------
- The token_map returned by tokenize() MUST NOT be included in any API
  response, logged, or persisted.  Exposing it re-exposes all original
  sensitive values.
- Restoration across separate requests is not supported.  Persistent
  reversible masking requires secure key management, which is outside
  the scope of this prototype.
- Token format: [CATEGORY_N].  If OCR text already contains a literal
  string matching this format (e.g. a UI element reading "[EMAIL_1]"),
  the tokenized text will be indistinguishable from a genuine token and
  restoration will corrupt the text at that position.  This edge case is
  unlikely in typical desktop screenshots and is documented as a known
  limitation rather than handled at runtime.
- This module is a prototype demonstration of reversible masking.  It is
  not a production-grade privacy primitive and provides no cryptographic
  protection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Categories processed — must stay in sync with sensitive_detection._PATTERNS
_ENTITY_CATEGORIES: tuple[str, ...] = (
    "EMAIL",
    "PHONE",
    "URL",
    "IP_ADDRESS",
    "DATE",
    "WINDOWS_PATH",
)

# Default confidence threshold below which a LOW prediction is considered
# uncertain enough to warrant tokenization.  NOT empirically optimised.
DEFAULT_MIN_CONFIDENCE_TO_SKIP: float = 0.75


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_token_map(detection_output: Mapping[str, Any]) -> dict[str, str]:
    """
    Assign tokens to each unique entity value across all categories.

    Within each category, each unique matched string is assigned one token
    [CATEGORY_N] (N is the 1-based count of distinct values seen so far).
    Duplicate occurrences in the matches list are collapsed to a single
    token; the same value always maps to the same token.

    Returns {token: original_value}.
    """
    token_map: dict[str, str] = {}
    for category in _ENTITY_CATEGORIES:
        cat_data = detection_output.get(category)
        if not isinstance(cat_data, Mapping):
            continue
        matches = cat_data.get("matches", [])
        if not isinstance(matches, list):
            continue

        seen: dict[str, str] = {}  # value → token (for deduplication)
        counter = 1
        for value in matches:
            if not isinstance(value, str) or not value:
                continue
            if value not in seen:
                token = f"[{category}_{counter}]"
                seen[value] = token
                token_map[token] = value
                counter += 1

    return token_map


def _should_tokenize(
    risk_level: str,
    confidence: float | None,
    min_confidence_to_skip: float,
) -> bool:
    """Return True if tokenization should be applied given risk context."""
    level = str(risk_level).upper().strip() if risk_level else "UNAVAILABLE"
    if level in ("HIGH", "CRITICAL", "MEDIUM", "UNAVAILABLE"):
        return True
    if level == "LOW":
        if confidence is None:
            return True  # fail-safe when model confidence is unavailable
        return float(confidence) < min_confidence_to_skip
    return True  # unknown level — fail-safe


def _strip_matches(detection_output: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return a copy of detection_output with all matches lists emptied.

    Counts and total_sensitive_entity_count are preserved so callers can
    still determine how many entities were detected.  Used when tokenization
    is skipped (LOW + confident prediction) to prevent raw sensitive values
    from appearing in the API response.
    """
    result: dict[str, Any] = {}
    for key, val in detection_output.items():
        if key == "total_sensitive_entity_count":
            result[key] = val
        elif isinstance(val, Mapping):
            result[key] = {"matches": [], "count": val.get("count", 0)}
        else:
            result[key] = val
    return result


def _apply_tokens_to_text(
    text: str, value_to_token: dict[str, str]
) -> str:
    """
    Replace entity values in text with their tokens.

    Substitutions are applied longest-value-first to prevent a shorter
    value (e.g. "example.com") from being replaced before a longer one
    that contains it (e.g. "alice@example.com").
    """
    result = text
    for value in sorted(value_to_token.keys(), key=len, reverse=True):
        result = result.replace(value, value_to_token[value])
    return result


def _apply_tokens_to_detection(
    detection_output: Mapping[str, Any],
    value_to_token: dict[str, str],
) -> dict[str, Any]:
    """
    Return a copy of detection_output with matches replaced by token strings.

    Counts and the total_sensitive_entity_count are preserved unchanged
    (they reflect the number of detected entities, not the token count).
    """
    tokenized: dict[str, Any] = {}
    for key, val in detection_output.items():
        if key == "total_sensitive_entity_count":
            tokenized[key] = val
            continue
        if isinstance(val, Mapping):
            raw_matches = val.get("matches", [])
            tokenized_matches = [
                value_to_token.get(m, m) if isinstance(m, str) else m
                for m in (raw_matches if isinstance(raw_matches, list) else [])
            ]
            tokenized[key] = {
                "matches": tokenized_matches,
                "count": val.get("count", len(tokenized_matches)),
            }
        else:
            tokenized[key] = val
    return tokenized


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def tokenize(
    ocr_text: str,
    detection_output: Mapping[str, Any],
    risk_level: str,
    confidence: float | None,
    min_confidence_to_skip: float = DEFAULT_MIN_CONFIDENCE_TO_SKIP,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """
    Tokenize sensitive entity values in OCR text and detection output.

    Parameters
    ----------
    ocr_text:
        Raw OCR text string produced by ocr_service.extract_text().
    detection_output:
        Dict produced by sensitive_detection.detect_sensitive_entities().
    risk_level:
        Risk level string from predict_privacy_risk() (e.g. 'HIGH', 'LOW').
    confidence:
        Confidence value from predict_privacy_risk(), or None if unavailable.
        See module docstring for semantics.
    min_confidence_to_skip:
        For LOW predictions only: skip tokenization when confidence is at
        least this value.  Default is DEFAULT_MIN_CONFIDENCE_TO_SKIP.
        Not empirically optimised.

    Returns
    -------
    tokenized_text : str
        OCR text with each matched entity value replaced by its token.
        Equal to ocr_text when tokenization is skipped.
    token_map : dict[str, str]
        {token: original_value}.  Empty when tokenization is skipped.
        MUST NOT be returned to clients, logged, or persisted.
    tokenized_detection : dict[str, Any]
        Copy of detection_output with matches lists replaced by token lists.
        Equal to a shallow copy of detection_output when skipped.
    """
    if not isinstance(ocr_text, str):
        raise TypeError(f"ocr_text must be str, got {type(ocr_text).__name__}")
    if not isinstance(detection_output, Mapping):
        raise TypeError(
            f"detection_output must be a Mapping, got {type(detection_output).__name__}"
        )

    if not _should_tokenize(risk_level, confidence, min_confidence_to_skip):
        return ocr_text, {}, _strip_matches(detection_output)

    token_map = _build_token_map(detection_output)
    if not token_map:
        return ocr_text, {}, dict(detection_output)

    value_to_token: dict[str, str] = {v: k for k, v in token_map.items()}
    tokenized_text = _apply_tokens_to_text(ocr_text, value_to_token)
    tokenized_detection = _apply_tokens_to_detection(detection_output, value_to_token)

    return tokenized_text, token_map, tokenized_detection


def restore(tokenized_text: str, token_map: dict[str, str]) -> str:
    """
    Restore original entity values from a tokenized text.

    Parameters
    ----------
    tokenized_text:
        Text returned by tokenize() for the same request.
    token_map:
        The token_map returned by the same tokenize() call.
        Restoration is only meaningful within the same request scope.

    Returns
    -------
    Text with each token replaced by its original value.
    Tokens absent from token_map are left unchanged.
    """
    if not isinstance(tokenized_text, str):
        raise TypeError(
            f"tokenized_text must be str, got {type(tokenized_text).__name__}"
        )
    if not isinstance(token_map, dict):
        raise TypeError(f"token_map must be dict, got {type(token_map).__name__}")

    result = tokenized_text
    for token in sorted(token_map.keys(), key=len, reverse=True):
        result = result.replace(token, token_map[token])
    return result
