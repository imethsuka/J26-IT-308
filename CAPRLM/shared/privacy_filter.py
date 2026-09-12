"""
The interface contract between PCCILM and CAPRLM.

Member 1's pipeline calls `PrivacyFilter.filter(...)` on merged text before anything can
reach `memory_entries`. Member 2 (Wijesingha) implements this Protocol with her real
detection + risk-prediction + protection pipeline. Neither side imports the other's
internals — this file, `rule_based_filter.py` (the baseline), and `contract_tests.py`
are the entire surface they share.

FAIL-CLOSED RULE (see PRIVACY_HANDOFF.md): if no real filter is installed, or a filter
call raises, the caller must treat the content as blocked. Nothing is persisted without
a filter actually running on it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import FilterResult, PrivacyContext


@runtime_checkable
class PrivacyFilter(Protocol):
    """Implemented by Member 2's real filter, the rule-based baseline, and the dev
    pass-through fake — anything that processes a merged document's text.
    """

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        """Return the protected text (or blocked=True) for one merged document."""
        ...


@runtime_checkable
class ImageFilter(Protocol):
    """The screenshot path (PRIVACY_HANDOFF.md §4): OCR + detection on raw image bytes.

    A separate Protocol rather than a second method on `PrivacyFilter`, so existing
    implementations (rule-based, pass-through) and their isinstance checks are untouched.
    Fail-closed applies here too: if OCR is unavailable or fails, return blocked=True.
    """

    def filter_image(self, image: bytes, ctx: PrivacyContext) -> FilterResult:
        ...


@runtime_checkable
class UrlFilter(Protocol):
    """Separate from text filtering: query-string tokens, session IDs, embedded emails
    in URLs are a distinct problem from body-text redaction. Also Member 2's charter.
    """

    def filter_url(self, url: str) -> str:
        ...
