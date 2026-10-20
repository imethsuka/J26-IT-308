"""Session key store for pseudonymization. Lives in research/ on purpose: per
PRIVACY_HANDOFF.md the mapping back to real values never crosses to Member 1's code or to
shared/. It is in-memory only — nothing is written to disk — which is a default for the
still-open key-store design question (research/README.md #4), not a decision."""

from __future__ import annotations

import re


class SessionKeyStore:
    def __init__(self) -> None:
        self._forward: dict[tuple[str, str], str] = {}   # (category, original) -> surrogate
        self._reverse: dict[str, str] = {}               # surrogate -> original
        self._counts: dict[str, int] = {}

    def surrogate(self, category: str, original: str) -> str:
        key = (category, original)
        if key not in self._forward:
            n = self._counts[category] = self._counts.get(category, 0) + 1
            sur = _format_surrogate(category, n)
            self._forward[key] = sur
            self._reverse[sur] = original
        return self._forward[key]

    def reveal(self, text: str) -> str:
        """Reverse pseudonymization. Only possible with this store."""
        if not self._reverse:
            return text
        pattern = re.compile("|".join(re.escape(s) for s in sorted(self._reverse, key=len, reverse=True)))
        return pattern.sub(lambda m: self._reverse[m.group(0)], text)

    def clear(self) -> None:
        self._forward.clear()
        self._reverse.clear()
        self._counts.clear()

    def __len__(self) -> int:
        return len(self._forward)


def _format_surrogate(category: str, n: int) -> str:
    if category == "EMAIL":
        return f"user{n}@pseudonym.invalid"
    if category == "PHONE":
        return f"000-000-{n:04d}"
    if category == "CARD_LIKE_NUMBER":
        return f"0000 0000 0000 {n:04d}"
    return f"{category.title().replace('_', '')}-{n}"
