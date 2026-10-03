"""The deferred screen-memory stage: tiers, the privacy boundary, episodes across visits, idempotence, scheduling."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from adaptation.privacy import BlockAllFilter, FilterResult, PassThroughFilter, PrivacyContext
from adaptation.scheduled_job import CycleConfig, run_if_ready
from adaptation.screen_memory import (
    HIGH, LOW, MEDIUM, ScreenMemoryConfig, ScreenMemoryJob, render_entry, run_screen_memory, tier_for,
)
from sensing.protocol import ScreenText
from storage.database import connect, migrate
from storage.memory_store import MemoryStore
from storage.raw_buffer import RawBuffer
from tests.test_privacy_contract import DigitRedactor
from tests.test_raw_buffer import XorProtector
from tests.test_text_merge import words

T0 = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=1)


def make_job(tmp_path, *, privacy_filter=None, **cfg) -> ScreenMemoryJob:
    p = XorProtector()
    return ScreenMemoryJob(
        raw=RawBuffer(tmp_path / "raw_buffer.db", protector=p, ttl_days=36_500),
        memory=MemoryStore(tmp_path / "memory.db", protector=p),
        config=ScreenMemoryConfig(privacy_filter=privacy_filter, tz=timezone.utc, **cfg),
    )


def add(job: ScreenMemoryJob, minutes: float, text: str, *, score: float = 0.9, title: str = "Article",
        day: int = 0, scrolls: int = 0) -> None:
    job.raw.add(
        ScreenText("uia", len(text), False, 1.0, text), ts=T0 + timedelta(days=day, minutes=minutes), app="chrome.exe",
        domain="medium.com", title=title, decision="save", score=score, activity={"mouse_scrolls": scrolls},
    )


def read_page(job: ScreenMemoryJob, title: str = "Article") -> str:
    from adaptation.episodes import doc_id_for
    from storage.raw_buffer import make_context_key

    stored = job.memory.load_document(doc_id_for(make_context_key("chrome.exe", "medium.com", title)))
    return "" if stored is None else " ".join(stored.document.text().split())


# --------------------------------------------------------------------------- tiers

def test_tier_thresholds() -> None:
    cfg = ScreenMemoryConfig()
    assert [tier_for(s, cfg) for s in (None, 0.0, 0.19, 0.2, 0.49, 0.5, 1.0)] == [LOW, LOW, LOW, MEDIUM, MEDIUM, HIGH, HIGH]


def test_high_tier_keeps_filtered_merged_text_and_marks_it_for_summary(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40), scrolls=3)
    add(job, 2, words(25, 70), scrolls=4)
    report = run_screen_memory(job, now=LATER)

    assert report["entries_written"] == 1 and report["tiers"][HIGH] == 1 and report["episodes_with_text"] == 1
    assert read_page(job) == words(0, 70)
    (entry,) = job.memory.entries()
    assert entry["needs_summary"] == 1 and entry["text_persisted"] == 1 and entry["tier"] == HIGH
    assert "Page: \"Article\"" in entry["entry_text"] and "7 scrolls" in entry["entry_text"]
    assert "70 new words" in entry["entry_text"]
    assert words(0, 5) not in entry["entry_text"]  # the entry holds no screen text
    assert job.raw.stats()["rows"] == 0  # processed rows are deleted


def test_medium_tier_keeps_text_but_needs_no_summary(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40), score=0.3)
    run_screen_memory(job, now=LATER)
    (entry,) = job.memory.entries()
    assert entry["tier"] == MEDIUM and entry["needs_summary"] == 0 and entry["text_persisted"] == 1
    assert read_page(job) == words(0, 40)
    assert "Activity:" not in entry["entry_text"]


def test_low_tier_drops_the_text_and_writes_one_metadata_line(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40), score=0.05)
    run_screen_memory(job, now=LATER)
    (entry,) = job.memory.entries()
    assert entry["tier"] == LOW and entry["text_persisted"] == 0 and "\n" not in entry["entry_text"]
    assert read_page(job) == ""
    assert job.raw.stats()["rows"] == 0  # the raw text is gone too


# --------------------------------------------------------------------------- the privacy boundary

def test_without_a_filter_no_text_is_stored_at_all(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=None)
    add(job, 0, words(0, 40))
    report = run_screen_memory(job, now=LATER)
    assert report["text_persisted"] is False and "no privacy filter" in report["note"]
    (entry,) = job.memory.entries()
    assert entry["text_persisted"] == 0 and entry["needs_summary"] == 0
    assert read_page(job) == ""
    assert b"Lorem" not in (tmp_path / "memory.db").read_bytes()


def test_only_filtered_text_is_merged_and_stored(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=DigitRedactor())
    add(job, 0, "Order 4417 shipped to apartment 12 on the third floor of the building near the river bank today")
    report = run_screen_memory(job, now=LATER)
    page = read_page(job)
    assert "4417" not in page and "[NUM]" in page
    assert report["redactions"] == 2
    (entry,) = job.memory.entries()
    assert entry["filter_version"] == "toy-digits/1"


def test_a_blocked_page_keeps_no_text(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=BlockAllFilter())
    add(job, 0, words(0, 40))
    report = run_screen_memory(job, now=LATER)
    assert report["captures_blocked"] == 1
    assert read_page(job) == ""
    (entry,) = job.memory.entries()
    assert entry["text_persisted"] == 0


def test_a_failing_filter_fails_closed_and_leaks_nothing_to_the_log(tmp_path, caplog) -> None:
    class Exploding:
        version = "boom/1"

        def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
            raise RuntimeError(f"cannot handle {text}")

    job = make_job(tmp_path, privacy_filter=Exploding())
    add(job, 0, words(0, 40))
    report = run_screen_memory(job, now=LATER)
    assert report["filter_errors"] == 1 and read_page(job) == ""
    assert "Lorem" not in caplog.text


def test_a_filter_returning_garbage_fails_closed(tmp_path) -> None:
    class Garbage:
        version = "garbage/1"

        def filter(self, text: str, ctx: PrivacyContext):
            return FilterResult(text=None, filter_version="garbage/1")  # type: ignore[arg-type]

    job = make_job(tmp_path, privacy_filter=Garbage())
    add(job, 0, words(0, 40))
    assert run_screen_memory(job, now=LATER)["filter_errors"] == 1
    assert read_page(job) == ""


def test_the_filter_sees_where_the_text_came_from(tmp_path) -> None:
    seen: list[PrivacyContext] = []

    class Spy(PassThroughFilter):
        def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
            seen.append(ctx)
            return super().filter(text, ctx)

    job = make_job(tmp_path, privacy_filter=Spy())
    add(job, 0, words(0, 40))
    run_screen_memory(job, now=LATER)
    assert (seen[0].app, seen[0].domain, seen[0].title, seen[0].source) == ("chrome.exe", "medium.com", "Article", "uia")


# --------------------------------------------------------------------------- episodes over time

def test_an_episode_still_in_progress_is_left_alone(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40))
    report = run_screen_memory(job, now=T0 + timedelta(minutes=10))  # last capture is 10 min old: user may still be there
    assert report["episodes_left_open"] == 1 and report["entries_written"] == 0
    assert job.raw.stats()["rows"] == 1
    assert run_screen_memory(job, now=T0 + timedelta(minutes=31))["entries_written"] == 1


def test_coming_back_later_is_visit_two_of_the_same_document(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40))
    add(job, 5, words(30, 60), title="Other", score=0.1)  # task switch
    add(job, 10, words(0, 40))                             # back on the article (new episode, same document)
    run_screen_memory(job, now=LATER)
    add(job, 5, words(25, 70), day=2)                      # two days later, another visit
    run_screen_memory(job, now=T0 + timedelta(days=3))

    article = [e for e in job.memory.entries() if '"Article"' in e["entry_text"]]
    visits = [json.loads(e["entry_json"])["visit_number"] for e in article]
    assert visits == [1, 2, 3]
    assert read_page(job) == words(0, 70)  # one copy; nothing duplicated across visits and days
    assert job.memory.stats()["documents"] == 2


def test_reprocessing_after_a_crash_does_not_duplicate_the_entry(tmp_path, monkeypatch) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40))
    monkeypatch.setattr(job.raw, "delete", lambda ids: 0)  # crash after the write, before the delete
    run_screen_memory(job, now=LATER)
    assert job.raw.stats()["rows"] == 1 and job.memory.stats()["entries"] == 1
    monkeypatch.undo()

    report = run_screen_memory(job, now=LATER)
    assert report["already_saved"] == 1 and job.memory.stats() == {"documents": 1, "episodes": 1, "entries": 1}
    assert job.raw.stats()["rows"] == 0


def test_a_low_importance_revisit_does_not_erase_the_stored_page(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40))
    run_screen_memory(job, now=LATER)
    add(job, 0, words(0, 40), score=0.01, day=2)
    run_screen_memory(job, now=T0 + timedelta(days=3))
    assert read_page(job) == words(0, 40)
    assert job.memory.stats()["entries"] == 2


def test_memory_files_are_only_created_when_there_is_something(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    report = run_screen_memory(job, now=LATER)
    assert report["captures_read"] == 0
    assert not (tmp_path / "memory.db").exists() and not (tmp_path / "raw_buffer.db").exists()


def test_render_entry_is_deterministic_text() -> None:
    from adaptation.episodes import Episode

    e = Episode(
        doc_id="d", context_key="k", app="chrome", domain="medium.com", title="Understanding LightGBM", start=T0,
        end=T0 + timedelta(minutes=29), n_captures=14, active_seconds=29 * 60, mouse_scrolls=120, key_presses=12,
        max_score=0.82, words_added=340,
    )
    text, data = render_entry(e, tier=HIGH, visit_number=2, doc_words=1250, text_persisted=True, tz=timezone.utc)
    assert text.splitlines() == [
        "Episode: 20/10/2026 09:00–09:29 (29 min active)",
        "App: chrome · Site: medium.com",
        'Page: "Understanding LightGBM"',
        "Activity: 14 captures, 120 scrolls, 12 key presses · visit 2 of this page",
        "Text: 340 new words this visit; the page has 1250 words in total",
        "Importance: 0.82 (high)",
    ]
    assert data["doc_words"] == 1250 and data["tier"] == HIGH


# --------------------------------------------------------------------------- the scheduled job

@pytest.fixture
def conn():
    c = connect(":memory:", check_same_thread=False)
    migrate(c)
    yield c
    c.close()


def test_scheduled_job_runs_the_stage_and_records_it(conn, tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    add(job, 0, words(0, 40))
    summary = run_if_ready(conn, CycleConfig(screen_memory=job), force=True, now=LATER)
    assert summary.stages["screen_memory"]["entries_written"] == 1
    assert "screen_memory" in summary.to_dict()["stages"]


def test_a_failing_stage_does_not_change_the_learning_outcome(conn, tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    job.raw.iter_unprocessed = lambda **kw: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error"))  # type: ignore
    summary = run_if_ready(conn, CycleConfig(screen_memory=job), force=True)
    assert "error" in summary.stages["screen_memory"]
    assert summary.error is None  # the learning stages ran as usual


def test_without_the_stage_nothing_changes(conn) -> None:
    summary = run_if_ready(conn, CycleConfig(), force=True)
    assert "screen_memory" not in summary.stages


def test_cli_flags_build_the_stage(tmp_path) -> None:
    from adaptation.scheduled_job import main

    db = tmp_path / "context.db"
    log = tmp_path / "runs.jsonl"
    assert main(["--db", str(db), "--summary-log", str(log), "--force", "--no-require-ac", "--screen-memory"]) == 0
    assert '"screen_memory"' in log.read_text(encoding="utf-8")
    assert "no privacy filter" in log.read_text(encoding="utf-8")  # default: no filter, so no text would be stored
