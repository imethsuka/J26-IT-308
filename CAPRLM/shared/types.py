"""
Shared data shapes for the privacy-filter interface between PCCILM (Member 1) and
CAPRLM (Member 2). This file is joint infrastructure, not research content: it defines
the contract, not how risk is actually detected or scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Source(str, Enum):
    """Where the text came from, as PCCILM's collector reports it."""
    UIA = "uia"    # read via UI Automation (the primary, higher-reliability path)
    OCR = "ocr"    # read via OCR fallback (lower reliability — see PRIVACY_HANDOFF.md)


@dataclass(frozen=True)
class PrivacyContext:
    """Everything about *where* a piece of text came from, beside the text itself.

    CAPRLM's whole research premise is that risk depends on context, not just entity
    type — so this is deliberately passed in full, not reduced to just the text.
    """
    app: str                 # e.g. "chrome", "outlook", "notepad"
    domain: str | None = None      # e.g. "mail.google.com"; None when not a browser
    url: str | None = None
    title: str | None = None       # window / page title
    source: Source = Source.UIA


@dataclass
class FilterResult:
    """What a PrivacyFilter hands back for one piece of text."""
    text: str                      # the redacted / tokenised text (or "" if blocked)
    blocked: bool = False          # True: this context must not be persisted at all
    redactions: int = 0            # how many spans were acted on (for audit / eval, not raw content)
    filter_version: str = "unknown"  # which filter implementation + version produced this

    # Structured, non-content audit trail — category + action per flagged span, not the raw
    # sensitive text itself. This is what lets evaluation (precision/recall, leakage) be
    # computed later without CAPRLM needing to log the actual sensitive content anywhere.
    audit: list["RedactionRecord"] = field(default_factory=list)


@dataclass(frozen=True)
class RedactionRecord:
    """One flagged span, logged without its actual content."""
    category: str        # e.g. "EMAIL", "FINANCIAL_FIGURE", "CREDENTIAL"
    risk_tier: str        # "none" | "low" | "medium" | "high"
    action: str           # "pass_through" | "tokenize" | "mask" | "drop"
