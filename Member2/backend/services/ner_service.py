"""
Contextual Named Entity Recognition for CAPRLM.

Wraps the existing regex-based sensitive_detection with:
  1. Per-match confidence scores  (0.0 – 1.0)
  2. Contextual trigger words that boost or lower confidence
  3. Combined output compatible with the existing detection_output schema

This is a rule+context hybrid, not a trained neural NER model.
It extends the regex layer rather than replacing it.
When the regex produces a match the score is ≥ 0.70 by default;
presence of contextual trigger words (e.g. "email:", "password:") raises
the score to ≥ 0.90.  Absence of context does not suppress the match.

Output schema (superset of sensitive_detection.detect_sensitive_entities):
{
  "EMAIL": {
    "matches": [...],
    "count": int,
    "scored_matches": [{"value": str, "confidence": float, "context_trigger": str | None}]
  },
  ...
  "total_sensitive_entity_count": int,
  "ner_method": "regex+contextual_heuristic"
}
"""

from __future__ import annotations

import re
from typing import Any

from backend.services.sensitive_detection import detect_sensitive_entities

# ---------------------------------------------------------------------------
# Context trigger patterns (window around the entity)
# ---------------------------------------------------------------------------

_CONTEXT_TRIGGERS: dict[str, list[tuple[re.Pattern[str], str, float]]] = {
    "EMAIL": [
        (re.compile(r"\b(?:email|e-mail|from|to|cc|bcc|reply[- ]?to|sender|recipient)\s*[:\-]?\s*$", re.I), "email-label", 0.20),
        (re.compile(r"\b(?:contact|inbox|mail)\b", re.I), "mail-context", 0.10),
    ],
    "PHONE": [
        (re.compile(r"\b(?:phone|mobile|tel|fax|call|dial|number)\s*[:\-]?\s*$", re.I), "phone-label", 0.20),
        (re.compile(r"\b(?:reach|contact)\b", re.I), "contact-context", 0.10),
    ],
    "URL": [
        (re.compile(r"\b(?:link|url|website|visit|open|navigate|href|src)\s*[:\-]?\s*$", re.I), "url-label", 0.15),
    ],
    "IP_ADDRESS": [
        (re.compile(r"\b(?:ip|address|host|server|gateway|subnet|ping)\s*[:\-]?\s*$", re.I), "ip-label", 0.20),
    ],
    "DATE": [
        (re.compile(r"\b(?:date|dob|born|expiry|expires|deadline|due|on)\s*[:\-]?\s*$", re.I), "date-label", 0.10),
    ],
    "WINDOWS_PATH": [
        (re.compile(r"\b(?:path|directory|folder|file|saved|located|at)\s*[:\-]?\s*$", re.I), "path-label", 0.20),
    ],
}

# Negative context — words that suggest the entity is an example or template
_NEGATIVE_TRIGGERS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:example|sample|test|placeholder|dummy|fake|redacted)\b", re.I),
]

# Base confidence for a regex match with no context
_BASE_CONFIDENCE = 0.75


def _extract_left_context(text: str, match_start: int, window: int = 60) -> str:
    """Return up to `window` characters to the left of a match."""
    return text[max(0, match_start - window): match_start]


def _score_match(
    category: str,
    value: str,
    text: str,
    match_start: int,
) -> tuple[float, str | None]:
    """
    Return (confidence, trigger_name | None).

    Applies context triggers and negative triggers.
    """
    left = _extract_left_context(text, match_start)
    score = _BASE_CONFIDENCE
    trigger_name: str | None = None

    # Positive context triggers
    for pattern, name, boost in _CONTEXT_TRIGGERS.get(category, []):
        if pattern.search(left):
            score = min(1.0, score + boost)
            trigger_name = name
            break  # first match wins

    # Negative context (e.g. "example email: ...")
    for neg_pat in _NEGATIVE_TRIGGERS:
        if neg_pat.search(left):
            score = max(0.0, score - 0.30)
            break

    return round(score, 3), trigger_name


def detect_entities_with_scores(text: str) -> dict[str, Any]:
    """
    Run regex detection and annotate every match with a confidence score.

    Returns a dict compatible with detect_sensitive_entities() plus:
      - scored_matches per category
      - ner_method field
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    base = detect_sensitive_entities(text)
    result: dict[str, Any] = {}

    for category in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"):
        cat_info = base.get(category, {"matches": [], "count": 0})
        matches: list[str] = cat_info.get("matches", [])

        scored: list[dict[str, Any]] = []
        # Find each match position for context scoring
        search_start = 0
        for value in matches:
            idx = text.find(value, search_start)
            if idx == -1:
                idx = 0  # fallback: no positional context
            conf, trigger = _score_match(category, value, text, idx)
            scored.append({"value": value, "confidence": conf, "context_trigger": trigger})
            search_start = max(0, idx)

        result[category] = {
            "matches": matches,
            "count": len(matches),
            "scored_matches": scored,
        }

    result["total_sensitive_entity_count"] = base.get("total_sensitive_entity_count", 0)
    result["ner_method"] = "regex+contextual_heuristic"
    return result


def high_confidence_entities(
    scored_output: dict[str, Any],
    threshold: float = 0.80,
) -> dict[str, list[str]]:
    """
    Return only the entity values whose confidence exceeds `threshold`.

    Useful for filtering noisy matches when deciding what to redact.
    """
    out: dict[str, list[str]] = {}
    for category in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"):
        cat = scored_output.get(category, {})
        filtered = [
            sm["value"]
            for sm in cat.get("scored_matches", [])
            if sm["confidence"] >= threshold
        ]
        if filtered:
            out[category] = filtered
    return out
