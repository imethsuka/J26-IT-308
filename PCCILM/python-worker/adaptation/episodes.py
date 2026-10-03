"""Group buffered screen captures into episodes and merge each page's text once (SCREEN_TEXT_PLAN.md sections 6-7).

An **episode** is an unbroken stretch in the same context, where the context is ``(app, domain, normalized
title)`` (``storage.raw_buffer.make_context_key``, the same identity the v4 labels use). It ends when the
context changes or the user is away for more than ``gap`` (30 minutes by default). Coming back later starts a *new*
episode that shares the same ``doc_id``, so "returned on Tuesday" stays visible.

This module is pure: it reads :class:`~storage.raw_buffer.RawCapture` objects and returns episodes and merged
documents in memory. Persisting them is the deferred job's business, and it must pass the text through the
privacy filter (Member 2) first. Nothing here writes to disk.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable

from features.text_merge import TextDocument
from storage.raw_buffer import RawCapture

DEFAULT_GAP = timedelta(minutes=30)
# Captures happen when the screen changes, so a long gap inside an episode usually means a quiet page being read.
# Count such a gap as active time only up to this much, and not at all when the user was idle before it.
ACTIVE_GAP_CAP = timedelta(minutes=5)


def doc_id_for(context_key: str) -> str:
    """Stable id of the document behind a context key (the same page on another day gives the same id)."""
    return hashlib.sha1(context_key.encode("utf-8")).hexdigest()[:16]


@dataclass
class Episode:
    doc_id: str
    context_key: str
    app: str | None
    domain: str | None
    title: str | None
    start: datetime
    end: datetime
    capture_ids: list[int] = field(default_factory=list)
    n_captures: int = 0
    active_seconds: float = 0.0
    key_presses: int = 0
    mouse_clicks: int = 0
    mouse_scrolls: int = 0
    mouse_moves: int = 0
    max_score: float | None = None
    words_seen: int = 0       # words in all its captures, counting repeats
    words_added: int = 0      # words that were new to the document
    truncated_captures: int = 0
    revisit: bool = False     # an earlier episode of the same document exists


def split_episodes(captures: Iterable[RawCapture], *, gap: timedelta = DEFAULT_GAP) -> list[list[RawCapture]]:
    """Runs of consecutive captures with the same context key and no gap longer than ``gap``."""
    runs: list[list[RawCapture]] = []
    for capture in sorted(captures, key=lambda c: (c.ts, c.id)):
        last = runs[-1][-1] if runs else None
        if last is None or capture.context_key != last.context_key or capture.ts - last.ts > gap:
            runs.append([capture])
        else:
            runs[-1].append(capture)
    return runs


def build_episodes(
    captures: Iterable[RawCapture],
    *,
    gap: timedelta = DEFAULT_GAP,
    documents: dict[str, TextDocument] | None = None,
    text_transform: Callable[[RawCapture], str | None] | None = None,
) -> tuple[list[Episode], dict[str, TextDocument]]:
    """Episodes in time order, and one merged :class:`TextDocument` per ``doc_id``.

    ``documents`` lets a caller continue earlier documents (a page seen yesterday) instead of starting empty.
    Captures whose text the size cap already dropped still count for timing and activity, but add no words.

    ``text_transform`` decides what text of a capture is merged: by default its own, but the deferred job passes the
    privacy filter here (returning ``None`` drops the capture's text), so unfiltered text is never merged or kept.
    """
    docs = documents if documents is not None else {}
    seen_docs: set[str] = set(docs)
    episodes: list[Episode] = []

    for run in split_episodes(captures, gap=gap):
        first = run[0]
        doc_id = doc_id_for(first.context_key)
        document = docs.setdefault(doc_id, TextDocument())
        episode = Episode(
            doc_id=doc_id, context_key=first.context_key, app=first.app, domain=first.domain, title=first.title,
            start=first.ts, end=run[-1].ts, revisit=doc_id in seen_docs,
        )
        previous: RawCapture | None = None
        for capture in run:
            episode.capture_ids.append(capture.id)
            episode.n_captures += 1
            episode.key_presses += capture.key_presses
            episode.mouse_clicks += capture.mouse_clicks
            episode.mouse_scrolls += capture.mouse_scrolls
            episode.mouse_moves += capture.mouse_moves
            episode.truncated_captures += int(capture.truncated)
            if capture.score is not None:
                episode.max_score = capture.score if episode.max_score is None else max(episode.max_score, capture.score)
            if previous is not None and not previous.is_idle:
                episode.active_seconds += min(capture.ts - previous.ts, ACTIVE_GAP_CAP).total_seconds()
            text = capture.text if text_transform is None else text_transform(capture)
            if text:
                result = document.merge(text)
                episode.words_seen += result.words
                episode.words_added += result.new_words
            previous = capture
        seen_docs.add(doc_id)
        episodes.append(episode)
    return episodes, docs
