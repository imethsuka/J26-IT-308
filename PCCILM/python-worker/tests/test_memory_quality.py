"""What keeps task switching out of memories, and the rules that keep the model's sentences and notes honest."""

from __future__ import annotations

from datetime import timedelta

import pytest

from adaptation.memory_tidy import tidy_memories
from adaptation.privacy import PassThroughFilter
from adaptation.rollups import (
    NOTES_VERSION, copies_a_note, group_sessions, plain_day_recap, run_rollups, to_items,
)
from adaptation.screen_memory import run_screen_memory
from adaptation.summarizer import clean_summary, copied_from, real_words
from features.surfaces import is_non_context
from features.titles import display_title
from sensing.protocol import ScreenText
from tests.test_screen_memory import LATER, T0, make_job
from tests.test_text_merge import words


def cap(job, seconds: float, text: str, *, title: str = "Article", app: str = "chrome.exe", keys: int = 0, idle=False):
    job.raw.add(ScreenText("uia", len(text), False, 1.0, text), ts=T0 + timedelta(seconds=seconds), app=app,
                domain="medium.com" if app == "chrome.exe" else None, title=title, decision="save", score=0.9,
                activity={"key_presses": keys}, is_idle=idle)


def entries(job):
    return job.memory.entries()


# --------------------------------------------------------------------------- shell surfaces

@pytest.mark.parametrize("app, title, expected", [
    ("explorer.exe", "Task Switching", True), ("explorer", "Program Manager", True), ("explorer", "", True),
    ("explorer", "Downloads - File Explorer", False), ("SnippingTool", "Snipping Tool Overlay", True),
    ("zen", "Picture-in-Picture", True), ("Microsoft.AAD.BrokerPlugin", "Workplace or school account", True),
    ("msedge", "Task Switching in the brain - Wikipedia", False), ("Code", "pipeline.py - Visual Studio Code", False),
])
def test_shell_surfaces_are_not_contexts(app, title, expected) -> None:
    assert is_non_context(app, title) is expected


def test_the_alt_tab_switcher_never_becomes_a_memory(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    cap(job, 0, words(0, 60))
    cap(job, 60, "WhatsApp\n1Password\nOutlook", app="explorer.exe", title="Task Switching")
    cap(job, 120, words(0, 60), title="Other")
    report = run_screen_memory(job, now=LATER)
    assert report["surfaces_dropped"] == 1
    assert [e["doc_id"] for e in entries(job)] and all("Task Switching" not in e["entry_json"] for e in entries(job))


# --------------------------------------------------------------------------- glances and bounces

def test_a_glance_gets_no_memory_and_the_page_around_it_is_one_visit(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    cap(job, 0, words(0, 40))                                   # reading the article
    cap(job, 30, words(0, 40), title="Inbox")                   # 4 s on the inbox ...
    cap(job, 34, words(30, 70))                                 # ... then straight back to the article
    cap(job, 200, words(0, 40), title="Next thing")
    report = run_screen_memory(job, now=LATER)

    assert (report["glances_dropped"], report["bounces_joined"]) == (1, 1)
    titles = [e["entry_json"] for e in entries(job)]
    assert len(titles) == 2 and not any('"Inbox"' in t for t in titles)
    assert '"visit_number": 1' in titles[0]                      # not "visit 2" after the glance


def test_a_short_stay_with_typing_is_not_a_glance(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    cap(job, 0, words(0, 40))
    cap(job, 30, words(0, 40), title="Chat", keys=12)            # a quick reply
    cap(job, 35, words(0, 40), title="Next thing")
    run_screen_memory(job, now=LATER)
    assert any('"Chat"' in e["entry_json"] for e in entries(job))


def test_time_on_a_page_runs_until_the_next_window(tmp_path) -> None:
    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    cap(job, 0, words(0, 60))                                   # one capture, then 3 minutes of reading without scrolling
    cap(job, 180, words(0, 40), title="Next thing")
    run_screen_memory(job, now=LATER)
    import json
    first = json.loads(entries(job)[0]["entry_json"])
    assert first["title"] == "Article" and first["active_seconds"] == pytest.approx(180)


# --------------------------------------------------------------------------- tidy: entries from before the filtering

def test_old_glances_and_surfaces_are_hidden_not_deleted(tmp_path, monkeypatch) -> None:
    import adaptation.memory_tidy as tidy
    import adaptation.screen_memory as sm

    job = make_job(tmp_path, privacy_filter=PassThroughFilter(), glance_seconds=0)   # write them the old way:
    monkeypatch.setattr(sm, "is_non_context", lambda app, title: False)              # no glances, no surface list
    monkeypatch.setattr(tidy, "is_non_context", lambda app, title: False)
    cap(job, 0, words(0, 40))
    cap(job, 3, words(0, 40), title="Inbox")
    cap(job, 60, "a b c", app="explorer.exe", title="Task Switching")
    cap(job, 90, words(0, 40), title="Last")
    run_screen_memory(job, now=LATER)
    monkeypatch.undo()
    before = len(entries(job))

    report = tidy_memories(job.memory, glance_seconds=10)
    assert report == {"hidden": 2, "task_switching": 1, "glances": 1}
    assert len(entries(job)) == before                           # nothing deleted
    assert job.memory.rollup_entries(since_us=0) and len(job.memory.rollup_entries(since_us=0)) == before - 2
    assert job.memory.unhide_all() == 2


# --------------------------------------------------------------------------- the model's sentences

@pytest.mark.parametrize("raw, expected", [
    ("You worked on a C# project in Visual Studio.", "You worked on a C# project in Visual Studio."),
    ("## You read about #3 issues", "You read about #3 issues"),
    ("You: 1. Which ContextDashboard is the base?", None),       # page text pasted back
    ("You said: let's stay node JS route then", None),            # a chat line pasted back
    ("This page explains LightGBM.", None),                      # not about "you"
    ("<think>hmm</think>You read about LightGBM trees.", "You read about LightGBM trees."),
])
def test_clean_summary_keeps_csharp_and_rejects_non_sentences(raw, expected) -> None:
    assert clean_summary(raw) == expected


def test_a_summary_copied_from_the_page_is_rejected() -> None:
    page = "The quick brown fox jumps over the lazy dog while the farmer watches from the old red barn door"
    copied = "You read that the quick brown fox jumps over the lazy dog while the farmer watches."
    assert copied_from(copied, page)
    assert clean_summary(copied, page_text=page) is None
    assert clean_summary("You read a short fable about a fox and a dog.", page_text=page) is not None
    titled = 'You watched "the quick brown fox jumps over the lazy dog while the farmer watches" on YouTube.'
    assert clean_summary(titled, page_text=page) is not None         # quoting the page's title is not copying


def test_too_little_text_is_counted_in_real_words() -> None:
    assert real_words("Snipping Tool Overlay\n▢\n▢\n42") == 3


def test_display_title_drops_the_browser_and_profile() -> None:
    assert display_title("(3) Inbox and 2 more pages - Personal - Microsoft​ Edge") == "Inbox"
    assert display_title("all features in zen browser - YouTube — Zen Browser") == "all features in zen browser - YouTube"
    assert display_title("CLAUDE.md - J26-IT-308 - Research - Visual Studio Code") == "CLAUDE.md - J26-IT-308 - Research"
    assert display_title("Microsoft Edge") == "Microsoft Edge"


# --------------------------------------------------------------------------- day recaps and note versions

NOTES = [
    {"title": "Screen text docs", "tldr": "You wrote the screen text documentation for the team. You fixed two typos.", "minutes": 70},
    {"title": "Zen Browser research", "tldr": "You compared Zen Browser features with Edge. You watched a guide.", "minutes": 25},
]


def test_a_recap_that_copies_one_session_is_caught() -> None:
    assert copies_a_note("You wrote the screen text documentation for the team and then rested.", NOTES)
    assert not copies_a_note("You spent the day on documentation and on comparing browsers.", NOTES)


def test_the_plain_recap_names_the_longest_sessions_first() -> None:
    assert plain_day_recap(NOTES) == "Most of your time went to Screen text docs (70 min) and Zen Browser research (25 min)."


class Echo:
    """Answers a session prompt with a fixed note and a day prompt by copying the first session's TLDR."""

    name = "echo"

    def __init__(self):
        self.prompts = []

    def complete(self, system, user, *, max_tokens):
        self.prompts.append(user)
        if user.startswith("Day "):
            return "You wrote the screen text documentation for the team. You fixed two typos."
        return "TITLE: Screen text docs\nTAG: docs\nTLDR: You wrote the screen text documentation for the team. You fixed two typos."


def test_old_notes_are_rewritten_and_a_copied_recap_is_replaced(tmp_path) -> None:
    from adaptation.rollups import RollupConfig, RollupJob

    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    for minute in (0, 40, 120, 160):                            # two sessions: 09:00-09:40 and 11:00-11:40
        cap(job, minute * 60, words(0, 60), title=f"Page {minute}")
    run_screen_memory(job, now=LATER)
    sessions = group_sessions(to_items(job.memory.rollup_entries(since_us=0)))
    for s in sessions:                                          # notes from an earlier version of the rules
        job.memory.save_session_note(key=s.key, day=s.day(), first_ts=0, last_ts=0, n_entries=1, title="old", tag="x",
                                     tldr="You visited Microsoft Edge for 1 minute.", model="m", version=1)

    model = Echo()
    report = run_rollups(RollupJob(job.memory, lambda: model, RollupConfig(days=3650)), now=LATER)

    assert report["session_notes"] == len(sessions)
    assert job.memory.session_note_keys(min_version=NOTES_VERSION) == {s.key for s in sessions}
    assert report.get("day_notes_plain") == 1                  # the copy was replaced by the fact-built recap


# --------------------------------------------------------------------------- starting the written part over

def test_reset_keeps_pages_clears_what_the_model_wrote_and_backs_up_first(tmp_path) -> None:
    from adaptation.memory_reset import backup, reset_memories

    job = make_job(tmp_path, privacy_filter=PassThroughFilter())
    cap(job, 0, words(0, 60))
    cap(job, 300, words(0, 60), title="Next")
    run_screen_memory(job, now=LATER)
    (first, *_) = entries(job)
    job.memory.set_summary(first["id"], "You read an article.", "old-model")
    job.memory.save_day_note(day="2026-10-20", sessions_key="k", tldr="You did things.", model="old-model")
    pages_before = job.memory.stats()["documents"]

    copy = backup(job.memory.path, tmp_path / "backups")
    report = reset_memories(job.memory)

    assert copy.exists() and copy.stat().st_size > 0
    assert report["day_notes_deleted"] == 1 and report["waiting_for_a_sentence"] >= 1
    assert all(e["summary"] is None for e in entries(job))
    assert job.memory.stats()["documents"] == pages_before       # the pages cannot be rebuilt: kept
    assert job.memory.load_document(first["doc_id"]).document.word_count > 0
