"""Merge the overlapping screen texts of one page into a single copy (docs/SCREEN_TEXT_PLAN.md section 7).

Each capture shows only the part of a page that is on screen, overlapping the one before it. A page is kept as
**fragments** (lists of words). Merging a capture ``N`` into a fragment ``F``:

1. Index ``F`` by 8-word shingles and find where ``N`` lines up (``offset`` of ``N[0]`` inside ``F``).
2. Check the overlap word by word: at least ``MIN_OVERLAP`` words and at most ``MAX_MISMATCH`` of them different.
3. The result covers both: where they overlap ``N``'s (newer) wording wins, except for ``N``'s first and last word,
   which the screen edge may have cut in half. That one rule covers all the cases: ``N`` inside ``F`` (nothing new),
   scrolling down (append), scrolling up (prepend), and ``F`` inside ``N`` (more of the page loaded).
4. No usable overlap: ``N`` becomes a new fragment (the user jumped far down). Then fragments that now overlap
   are joined (a gap got filled).

Text that scrolled away is never deleted. Cost is linear in the text length.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

SHINGLE_WORDS = 8
MIN_OVERLAP = 8          # shorter overlaps ("Click here") must not join unrelated text
MAX_MISMATCH = 0.10      # share of the overlap that may differ (counters, timestamps, edits)
MAX_CANDIDATE_OFFSETS = 5
MAX_JOIN_FRAGMENTS = 50   # past this, skip the quadratic join pass (noisy pages); nothing is dropped

_WORD = re.compile(r"\S+")


def tokenize(text: str) -> tuple[list[str], list[bool]]:
    """``(words, newline_before)``: the words, and whether a line break came before each one."""
    words: list[str] = []
    breaks: list[bool] = []
    last_end = 0
    for match in _WORD.finditer(text):
        gap = text[last_end:match.start()]
        words.append(match.group())
        breaks.append("\n" in gap)
        last_end = match.end()
    return words, breaks


@dataclass
class Fragment:
    words: list[str]
    breaks: list[bool]

    def __len__(self) -> int:
        return len(self.words)

    def text(self) -> str:
        parts: list[str] = []
        for word, newline in zip(self.words, self.breaks):
            if parts:
                parts.append("\n" if newline else " ")
            parts.append(word)
        return "".join(parts)


@dataclass(frozen=True)
class MergeResult:
    """What one capture added to the document."""

    words: int            # words in the capture
    new_words: int        # words that were not in the document before
    new_ratio: float      # new_words / words (1.0 for the first capture, ~0 for a repeat)
    fragment: int         # index of the fragment it ended up in


def _shingle_index(words: list[str]) -> dict[tuple[str, ...], list[int]]:
    index: dict[tuple[str, ...], list[int]] = {}
    for i in range(len(words) - SHINGLE_WORDS + 1):
        index.setdefault(tuple(words[i:i + SHINGLE_WORDS]), []).append(i)
    return index


def _candidate_offsets(base: list[str], new: list[str]) -> list[int]:
    """Where ``new[0]`` could sit inside ``base``, best-supported first (votes = shared shingles)."""
    index = _shingle_index(base)
    votes: Counter[int] = Counter()
    for j in range(len(new) - SHINGLE_WORDS + 1):
        for i in index.get(tuple(new[j:j + SHINGLE_WORDS]), ()):
            votes[i - j] += 1
    return [offset for offset, _ in votes.most_common(MAX_CANDIDATE_OFFSETS)]


def _score_offset(base: list[str], new: list[str], offset: int) -> int | None:
    """The overlap length if ``new`` fits ``base`` at ``offset`` well enough, else ``None``."""
    start, end = max(0, offset), min(len(base), offset + len(new))
    overlap = end - start
    if overlap < MIN_OVERLAP:
        return None
    mismatches = 0
    for k in range(start, end):
        j = k - offset
        if base[k] != new[j] and 0 < j < len(new) - 1:  # N's edge words may be cut by the screen
            mismatches += 1
    return overlap if mismatches <= MAX_MISMATCH * overlap else None


def _best_fit(base: list[str], new: list[str]) -> tuple[int, int] | None:
    """``(offset, overlap)`` of the best alignment of ``new`` within ``base``, or ``None``."""
    best: tuple[int, int] | None = None
    for offset in _candidate_offsets(base, new):
        overlap = _score_offset(base, new, offset)
        if overlap is not None and (best is None or overlap > best[1]):
            best = (offset, overlap)
    return best


def _combine(base: Fragment, new: Fragment, offset: int) -> tuple[Fragment, int]:
    """One fragment covering both, and the number of words that were not in ``base`` before."""
    start, end = min(0, offset), max(len(base), offset + len(new))
    words: list[str] = []
    breaks: list[bool] = []
    added = 0
    for k in range(start, end):
        j = k - offset
        in_new = 0 <= j < len(new)
        in_base = 0 <= k < len(base)
        keep_base = in_base and (not in_new or j in (0, len(new) - 1))  # the screen edge may have cut N's end words
        if keep_base:
            words.append(base.words[k])
            breaks.append(base.breaks[k])
        else:
            words.append(new.words[j])
            breaks.append(new.breaks[j])
        if in_new and not in_base:
            added += 1
    return Fragment(words, breaks), added


@dataclass
class TextDocument:
    """One page or document: its merged fragments across every capture (and episode) of the same context."""

    fragments: list[Fragment] = field(default_factory=list)
    captures: int = 0

    @property
    def word_count(self) -> int:
        return sum(len(f) for f in self.fragments)

    def text(self, separator: str = "\n\n") -> str:
        return separator.join(f.text() for f in self.fragments)

    def merge(self, text: str) -> MergeResult:
        words, breaks = tokenize(text)
        self.captures += 1
        if not words:
            return MergeResult(0, 0, 0.0, -1)
        new = Fragment(words, breaks)

        best: tuple[int, int, int] | None = None  # (overlap, fragment index, offset)
        for i, fragment in enumerate(self.fragments):
            fit = _best_fit(fragment.words, new.words)
            if fit is not None and (best is None or fit[1] > best[0]):
                best = (fit[1], i, fit[0])

        if best is None:
            self.fragments.append(new)
            index, added = len(self.fragments) - 1, len(words)
        else:
            _, index, offset = best
            merged, added = _combine(self.fragments[index], new, offset)
            self.fragments[index] = merged
            if len(self.fragments) <= MAX_JOIN_FRAGMENTS:
                index = self._join_overlapping(index)
        return MergeResult(len(words), added, added / len(words), index)

    def _join_overlapping(self, index: int) -> int:
        """Join fragments that now overlap (the gap between them was filled). Returns ``index``'s new position."""
        changed = True
        while changed:
            changed = False
            for a in range(len(self.fragments)):
                for b in range(a + 1, len(self.fragments)):
                    fit = _best_fit(self.fragments[a].words, self.fragments[b].words)
                    if fit is None:
                        continue
                    merged, _ = _combine(self.fragments[a], self.fragments[b], fit[0])
                    self.fragments[a] = merged
                    del self.fragments[b]
                    index = a if index == b else index - (1 if index > b else 0)
                    changed = True
                    break
                if changed:
                    break
        return index

    def to_dict(self) -> dict[str, Any]:
        return {
            "captures": self.captures,
            "fragments": [{"words": f.words, "breaks": f.breaks} for f in self.fragments],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TextDocument":
        return cls(
            fragments=[Fragment(list(f["words"]), list(f["breaks"])) for f in data.get("fragments", [])],
            captures=int(data.get("captures", 0)),
        )
