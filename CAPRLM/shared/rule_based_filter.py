"""
The rule-based baseline: entity-category detection by regex, risk decided purely by
category + the user's stated policy for that category. No context awareness at all.

This is NOT a stand-in for Member 2's research model — it's the baseline her learned
risk model has to beat. Its whole value is that it's simple enough to reason about and
will predictably get the context-dependent cases wrong (same entity, different real
risk depending on surrounding context) — that gap is what her research measures.

Entity patterns here are deliberately minimal (email, phone, card-like number). Wijesingha's
real detection stage (NER + OCR + CV) is a separate, much more capable thing — this file
exists only so the baseline is runnable and testable on its own.
"""

from __future__ import annotations

import re

from .types import FilterResult, PrivacyContext, RedactionRecord

_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "PHONE": re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"),
    "CARD_LIKE_NUMBER": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
}

# Default policy: conservative-by-default, per the privacy-by-design discussion —
# everything is treated as sensitive until the user opts a category out.
DEFAULT_POLICY: dict[str, str] = {
    "EMAIL": "tokenize",
    "PHONE": "tokenize",
    "CARD_LIKE_NUMBER": "drop",
}


class RuleBasedPrivacyFilter:
    """Category + user-policy lookup. Implements the PrivacyFilter protocol."""

    version = "rule-based/0.1"

    def __init__(self, user_policy: dict[str, str] | None = None):
        # category -> "pass_through" | "tokenize" | "mask" | "drop"
        self.policy = {**DEFAULT_POLICY, **(user_policy or {})}

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        audit: list[RedactionRecord] = []
        redactions = 0
        out = text

        for category, pattern in _PATTERNS.items():
            action = self.policy.get(category, "tokenize")
            matches = list(pattern.finditer(out))
            if not matches:
                continue
            for i, m in enumerate(matches):
                redactions += 1
                audit.append(RedactionRecord(category=category, risk_tier="medium", action=action))
                if action == "drop":
                    out = out.replace(m.group(0), "")
                elif action in ("tokenize", "mask"):
                    out = out.replace(m.group(0), f"[{category}_{i + 1}]")
                # "pass_through": leave as-is

        return FilterResult(text=out, blocked=False, redactions=redactions,
                             filter_version=self.version, audit=audit)
