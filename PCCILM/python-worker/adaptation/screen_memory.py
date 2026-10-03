"""The deferred stage that turns buffered screen captures into memory entries (docs/SCREEN_TEXT_PLAN.md sections 3, 6-8).

Run it only when the PC is idle and on AC power (it is a stage of ``adaptation/scheduled_job.py``). For every *closed*
episode in ``raw_buffer.db``:

    1. Tier it by Model 1's highest score during the episode: high / medium / low (section 3).
    2. low: keep one metadata line; the raw text is dropped, never merged or stored.
       medium / high: pass each capture's text through the **privacy filter**, then merge it into the page's single
       document (continuing the copy stored on earlier days).
    3. Write the document, the episode and a memory entry to ``memory.db`` in one transaction.
    4. Delete the raw rows of the episode ("the deferred job deletes rows as soon as it has processed them").

Rules that matter:

* **Nothing unfiltered is stored.** Text is filtered *per capture before it is merged* (a deliberate change from the
  plan's "filter the merged text": the stored page is then always filtered, and a later visit merges filtered text
  into filtered text, so the overlap matching stays consistent). If the filter raises, or blocks the page, that
  capture's text is dropped.
* **No filter, no text.** Without a ``PrivacyFilter`` the entries are metadata-only and no text is persisted at all.
* **An episode still in progress is left alone**: it is closed only once its last capture is older than the gap.
* **Reprocessing is a no-op** (``episodes`` is unique per document and start time), so a crash between the write and
  the delete cannot duplicate an entry.
* **Summaries are not written here.** High-tier entries with text are marked ``needs_summary`` for the local-LLM stage.

Tier thresholds are starting guesses, not tuned values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from adaptation.episodes import DEFAULT_GAP, Episode, build_episodes, doc_id_for, split_episodes
from adaptation.privacy import PrivacyContext, PrivacyFilter
from storage.memory_store import MemoryStore
from storage.raw_buffer import RawBuffer, RawCapture

logger = logging.getLogger("worker.adaptation.screen_memory")

HIGH = "high"
MEDIUM = "medium"
LOW = "low"


@dataclass
class ScreenMemoryConfig:
    gap: timedelta = DEFAULT_GAP
    high_threshold: float = 0.5     # the worker's keep threshold
    medium_threshold: float = 0.2   # a guess; nothing here is tuned
    max_captures: int = 5000        # per run, oldest first; the rest wait for the next run
    privacy_filter: PrivacyFilter | None = None
    tz: tzinfo | None = None        # time zone for the entry text; local time by default


@dataclass
class ScreenMemoryJob:
    """What the scheduled job needs to run this stage."""

    raw: RawBuffer
    memory: MemoryStore
    config: ScreenMemoryConfig = field(default_factory=ScreenMemoryConfig)


def tier_for(score: float | None, config: ScreenMemoryConfig) -> str:
    if score is None:
        return LOW
    if score >= config.high_threshold:
        return HIGH
    if score >= config.medium_threshold:
        return MEDIUM
    return LOW


def _minutes(seconds: float) -> str:
    return "<1 min" if seconds < 60 else f"{round(seconds / 60)} min"


def render_entry(
    episode: Episode, *, tier: str, visit_number: int, doc_words: int, text_persisted: bool, tz: tzinfo | None = None,
) -> tuple[str, dict[str, Any]]:
    """The entry as text (for Members 3 and 4) and as a dict. Deterministic: no summary is written here."""
    start, end = episode.start.astimezone(tz), episode.end.astimezone(tz)
    when = f"{start:%d/%m/%Y} {start:%H:%M}–{end:%H:%M}"
    importance = "n/a" if episode.max_score is None else f"{episode.max_score:.2f}"
    site = episode.domain or "-"
    page = f'"{episode.title}"' if episode.title else "(untitled)"
    if tier == LOW:
        text = f"{when} · {episode.app} · {site} · {page} · {_minutes(episode.active_seconds)} · importance {importance} (low)"
    else:
        lines = [
            f"Episode: {when} ({_minutes(episode.active_seconds)} active)",
            f"App: {episode.app} · Site: {site}",
            f"Page: {page}",
        ]
        if tier == HIGH:
            lines.append(
                f"Activity: {episode.n_captures} captures, {episode.mouse_scrolls} scrolls, {episode.key_presses} key presses"
                f" · visit {visit_number} of this page"
            )
        else:
            lines.append(f"Visit {visit_number} of this page")
        if text_persisted:
            lines.append(f"Text: {episode.words_added} new words this visit; the page has {doc_words} words in total")
        lines.append(f"Importance: {importance} ({tier})")
        text = "\n".join(lines)
    data = {
        "doc_id": episode.doc_id, "app": episode.app, "domain": episode.domain, "title": episode.title,
        "start": episode.start.isoformat(), "end": episode.end.isoformat(), "tier": tier,
        "importance": episode.max_score, "n_captures": episode.n_captures, "active_seconds": round(episode.active_seconds, 1),
        "key_presses": episode.key_presses, "mouse_clicks": episode.mouse_clicks, "mouse_scrolls": episode.mouse_scrolls,
        "words_added": episode.words_added, "doc_words": doc_words, "text_persisted": text_persisted,
    }
    return text, data


def run_screen_memory(job: ScreenMemoryJob, *, now: datetime | None = None) -> dict[str, Any]:
    """Process every closed episode in the raw buffer. Returns a JSON-able report for the run summary."""
    cfg, raw, memory = job.config, job.raw, job.memory
    now = now or datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "captures_read": 0, "episodes_processed": 0, "episodes_left_open": 0, "entries_written": 0,
        "tiers": {HIGH: 0, MEDIUM: 0, LOW: 0}, "episodes_with_text": 0, "captures_blocked": 0, "filter_errors": 0,
        "redactions": 0, "already_saved": 0, "raw_rows_deleted": 0,
        "text_persisted": cfg.privacy_filter is not None,
    }
    if cfg.privacy_filter is None:
        report["note"] = "no privacy filter: entries are metadata-only and no screen text is stored"

    captures = list(raw.iter_unprocessed(limit=cfg.max_captures))
    report["captures_read"] = len(captures)
    runs = split_episodes(captures, gap=cfg.gap)
    batch_full = len(captures) >= cfg.max_captures

    for i, run in enumerate(runs):
        still_open = now - run[-1].ts <= cfg.gap or (batch_full and i == len(runs) - 1)
        if still_open:
            report["episodes_left_open"] += 1
            continue
        try:
            _process_run(run, job, report)
        except Exception as exc:  # one bad episode must not stop the others; its raw rows stay for the next run
            logger.warning("episode %s not processed (%s)", doc_id_for(run[0].context_key), type(exc).__name__)
            report["errors"] = report.get("errors", 0) + 1
    return report


def _process_run(run: list[RawCapture], job: ScreenMemoryJob, report: dict[str, Any]) -> None:
    cfg, raw, memory = job.config, job.raw, job.memory
    first = run[0]
    doc_id = doc_id_for(first.context_key)
    ids = [c.id for c in run]

    if memory.episode_exists(doc_id, first.ts):  # saved by an earlier run that crashed before deleting
        report["already_saved"] += 1
        report["raw_rows_deleted"] += raw.delete(ids)
        return

    scores = [c.score for c in run if c.score is not None]
    tier = tier_for(max(scores) if scores else None, cfg)
    keep_text = tier != LOW and cfg.privacy_filter is not None
    stored = memory.load_document(doc_id)
    documents = {doc_id: stored.document} if stored is not None else {}
    versions: set[str] = set()

    def transform(capture: RawCapture) -> str | None:
        if not keep_text or not capture.text:
            return None
        assert cfg.privacy_filter is not None
        ctx = PrivacyContext(app=capture.app, domain=capture.domain, title=capture.title, source=capture.source)
        try:
            result = cfg.privacy_filter.filter(capture.text, ctx)
            if not isinstance(result.text, str):
                raise TypeError("filter returned non-text")
        except Exception as exc:  # fail closed: this capture's text is dropped
            report["filter_errors"] += 1
            logger.warning("privacy filter failed (%s); this capture's text is dropped", type(exc).__name__)
            return None
        if result.blocked:
            report["captures_blocked"] += 1
            return None
        report["redactions"] += result.redactions
        versions.add(result.filter_version or cfg.privacy_filter.version)
        return result.text

    (episode,), docs = build_episodes(run, gap=cfg.gap, documents=documents, text_transform=transform)
    document = docs[doc_id]
    text_persisted = keep_text and document.word_count > 0
    visits_before = stored.visits if stored is not None else 0
    entry_text, entry_json = render_entry(
        episode, tier=tier, visit_number=visits_before + 1, doc_words=document.word_count,
        text_persisted=text_persisted, tz=cfg.tz,
    )
    filter_version = ",".join(sorted(versions)) if versions else (cfg.privacy_filter.version if keep_text else None)
    saved = memory.save_episode(
        doc_id=doc_id, context_key=first.context_key, app=first.app, domain=first.domain, title=first.title,
        document=document if keep_text else None, filter_version=filter_version,
        episode={
            "start": episode.start, "end": episode.end, "n_captures": episode.n_captures,
            "active_seconds": episode.active_seconds, "key_presses": episode.key_presses,
            "mouse_clicks": episode.mouse_clicks, "mouse_scrolls": episode.mouse_scrolls,
            "mouse_moves": episode.mouse_moves, "max_score": episode.max_score, "words_seen": episode.words_seen,
            "words_added": episode.words_added, "truncated_captures": episode.truncated_captures,
        },
        entry_text=entry_text, entry_json=entry_json, tier=tier,
        needs_summary=tier == HIGH and text_persisted, text_persisted=text_persisted,
    )
    report["episodes_processed"] += 1
    report["tiers"][tier] += 1
    if saved is None:
        report["already_saved"] += 1
    else:
        report["entries_written"] += 1
        report["episodes_with_text"] += int(text_persisted)
    report["raw_rows_deleted"] += raw.delete(ids)
