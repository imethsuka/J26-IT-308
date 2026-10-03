"""The boundary to Member 2's privacy filter (docs/SCREEN_TEXT_PLAN.md sections 9 and 10).

Privacy, tokenisation and redaction are owned by Member 2. This module defines only the interface they implement and
the rules the deferred job applies around it, in the same way ``adaptation/label_construction.py`` takes an injected
retrieval callback. Nothing here decides what counts as sensitive.

The contract, which any implementation must meet (``tests/test_privacy_contract.py`` checks it):

* ``filter(text, ctx)`` returns a :class:`FilterResult`; it must not mutate anything and must not keep the text.
* ``blocked=True`` means "drop this page entirely": the job keeps none of that capture's text.
* ``filter_version`` identifies the rules used; it is stored with every memory entry for audit.
* The job fails **closed**: if ``filter`` raises, the capture's text is dropped, never stored unfiltered.

With no filter, no screen text is persisted at all (metadata-only entries). The pass-through filter below exists only
for development on the author's own data and must be requested explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PrivacyContext:
    """What the filter may use to decide. Never the raw frame, only where the text came from."""

    app: str | None
    domain: str | None
    title: str | None
    source: str = "uia"  # "uia", or "ocr" if an OCR fallback is added


@dataclass(frozen=True)
class FilterResult:
    text: str
    blocked: bool = False
    redactions: int = 0
    filter_version: str = ""


@runtime_checkable
class PrivacyFilter(Protocol):
    version: str

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult: ...


class PassThroughFilter:
    """Returns the text unchanged. **Development only**, on the author's own data: it protects nothing."""

    version = "passthrough-dev"

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        return FilterResult(text=text, filter_version=self.version)


class BlockAllFilter:
    """Drops everything. A safe stand-in and a handy test double."""

    version = "block-all"

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        return FilterResult(text="", blocked=True, filter_version=self.version)
