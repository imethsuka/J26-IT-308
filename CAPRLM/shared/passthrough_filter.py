"""
Dev-mode-only, no-op filter: returns text unchanged. This exists purely so Member 1 can
exercise the end-to-end pipeline (collector -> buffer -> merge -> filter -> storage)
before a real or rule-based filter is wired in.

Per the agreed rule: usable ONLY in dev mode, and ONLY on your own data (no consent
infrastructure yet for anyone else's captured content). It refuses to run unless
explicitly acknowledged every time, so it can't be left on by accident.
"""

from __future__ import annotations

from .types import FilterResult, PrivacyContext


class PassthroughFilterMisuseError(RuntimeError):
    pass


class PassthroughPrivacyFilter:
    """NEVER use this as a real privacy filter. It does not redact anything."""

    version = "dev-passthrough/unsafe"

    def __init__(self, *, acknowledge_dev_mode_on_own_data: bool):
        if not acknowledge_dev_mode_on_own_data:
            raise PassthroughFilterMisuseError(
                "PassthroughPrivacyFilter redacts nothing. Construct it only with "
                "acknowledge_dev_mode_on_own_data=True, and only when testing on your "
                "own captured data."
            )

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        return FilterResult(text=text, blocked=False, redactions=0, filter_version=self.version)
