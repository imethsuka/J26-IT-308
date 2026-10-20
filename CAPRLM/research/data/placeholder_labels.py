"""PLACEHOLDER label protocol — NOT Wijesingha's.

Research decision #1 (research/README.md) — how risk ground truth is labelled — is open.
To let the pipeline run end-to-end, risk tiers here come from a hand-written table:
an entity-type prior adjusted by what the context's norms would expect (a nod to
Contextual Integrity). Because the labels are a deterministic function of (category,
context kind), any model that can see those two things will "learn" it near-perfectly.
That makes every number computed against this table a pipeline check, never a result.
"""

from __future__ import annotations

PLACEHOLDER_DATASET_NAME = "placeholder-not-for-reporting"

KINDS = ("banking", "health", "hr", "email", "work", "social", "code", "news", "shopping", "notes")

# Column order follows KINDS:
#                    banking  health   hr       email   work    social   code     news    shopping notes
_ROWS: dict[str, tuple[str, ...]] = {
    "EMAIL":            ("high",   "high",   "high",   "low",  "low",    "high",   "medium", "medium", "medium", "medium"),
    "PHONE":            ("high",   "high",   "high",   "low",  "low",    "high",   "medium", "medium", "medium", "medium"),
    "PERSON":           ("medium", "high",   "high",   "low",  "low",    "low",    "none",   "none",   "low",    "low"),
    "LOCATION":         ("medium", "high",   "medium", "low",  "none",   "medium", "none",   "none",   "medium", "low"),
    "ORG":              ("none",   "low",    "low",    "none", "none",   "none",   "none",   "none",   "none",   "none"),
    "DATE":             ("low",    "high",   "medium", "none", "none",   "low",    "none",   "none",   "none",   "low"),
    "FINANCIAL_FIGURE": ("high",   "medium", "high",   "low",  "medium", "low",    "none",   "none",   "low",    "medium"),
    # Context-independent on purpose: a context-blind baseline gets these right.
    "CARD_LIKE_NUMBER": ("high",) * 10,
    "CREDENTIAL":       ("high",) * 10,
}

TIER_TABLE: dict[str, dict[str, str]] = {
    cat: dict(zip(KINDS, tiers)) for cat, tiers in _ROWS.items()
}


def gold_tier(category: str, kind: str) -> str:
    return TIER_TABLE[category][kind]
