from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adaptation.rollups import (
    RollupConfig, RollupJob, group_sessions, parse_session_note, plain_sentence, run_rollups, session_key, to_items,
)
from features.text_merge import TextDocument
from storage.memory_store import MemoryStore
from tests.test_raw_buffer import XorProtector

UTC = timezone.utc
D = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


class FakeModel:
    name = "fake-1b"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def complete(self, system, user, *, max_tokens):
        self.prompts.append((system, user))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


GOOD_SESSION = "TITLE: Reviewing the payroll plan\nTAG: finance\nTLDR: You read the payroll plan. You noted the review dates."
GOOD_DAY = "You reviewed the payroll plan. You then checked the docs. You finished with a short code change."


def add(memory: MemoryStore, n: int, start: datetime, minutes: float = 5, *, tier="high", title=None, summary=None, app="msedge"):
    doc = TextDocument()
    doc.merge("Some filtered page text about the plan and the review dates")
    eid = memory.save_episode(
        doc_id=f"doc{n}", context_key=f"key{n}", app=app, domain="docs.example.com", title=title or f"Page {n}",
        document=doc, filter_version="caprlm/test",
        episode={"start": start, "end": start + timedelta(minutes=minutes), "n_captures": 5, "active_seconds": minutes * 60,
                 "key_presses": 1, "mouse_clicks": 1, "mouse_scrolls": 1, "mouse_moves": 0, "max_score": 0.9 if tier == "high" else 0.3,
                 "words_seen": 10, "words_added": 10, "truncated_captures": 0},
        entry_text="x", entry_json={"app": app, "domain": "docs.example.com", "title": title or f"Page {n}",
                                    "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat(),
                                    "active_seconds": minutes * 60},
        tier=tier, needs_summary=False, text_persisted=True)
    if summary:
        memory.set_summary(eid, summary, "fake-1b")
    return eid


@pytest.fixture()
def memory(tmp_path):
    return MemoryStore(tmp_path / "memory.db", protector=XorProtector())


def job(memory, model, **cfg):
    return RollupJob(memory=memory, model_factory=lambda: model, config=RollupConfig(**cfg))


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


# ---- the grouping rule and key are shared with the C# SessionBuilder: one example, same answers in both ---------------

def test_shared_example_grouping_and_keys(memory):
    add(memory, 1, D, 10)                                   # 09:00-09:10
    add(memory, 2, D + timedelta(minutes=35), 5)            # 09:35: 25 min after the first ended -> same session
    add(memory, 3, D + timedelta(minutes=125), 5)           # 11:05: more than 30 min after 09:40 -> new session

    sessions = group_sessions(to_items(memory.rollup_entries(since_us=0)))

    assert [[i.id for i in s.items] for s in sessions] == [[1, 2], [3]]
    assert [s.key for s in sessions] == ["2219fecb861ae82b", "77de68daecd823ba"]
    assert session_key([2, 1]) == session_key([1, 2])        # order does not matter


def test_a_long_entry_keeps_the_session_open_until_it_ends(memory):
    add(memory, 1, D, 5)                                    # 09:00-09:05
    add(memory, 2, D + timedelta(minutes=20), 70)           # 09:20-10:30, a long one
    add(memory, 3, D + timedelta(minutes=55), 5)            # 09:55: 50 min after entry 1 ended, but inside entry 2: same session

    assert len(group_sessions(to_items(memory.rollup_entries(since_us=0)))) == 1


def test_a_long_busy_stretch_is_cut_into_hour_sessions(memory):
    for n in range(8):                                       # an entry every 20 minutes for 2 h 20 min, never a 30 min gap
        add(memory, n + 1, D + timedelta(minutes=20 * n), 5)

    sessions = group_sessions(to_items(memory.rollup_entries(since_us=0)))

    # cut when an entry starts more than 60 min after the session's first one: 09:00, 09:20, 09:40, 10:00 | 10:20 ... 11:20
    assert [[i.id for i in s.items] for s in sessions] == [[1, 2, 3, 4], [5, 6, 7, 8]]


def test_low_entries_are_not_part_of_the_narrative(memory):
    add(memory, 1, D, tier="low")
    add(memory, 2, D + timedelta(minutes=5), tier="medium")

    assert [i.id for i in to_items(memory.rollup_entries(since_us=0))] == [2]


def test_the_plain_sentence_matches_the_dashboards():
    assert plain_sentence("msedge", "docs.example.com", "Q3 plan", 600, 1) == "Spent 10 min on “Q3 plan” in msedge (docs.example.com)."
    assert plain_sentence("code", "-", "", 20, 3) == "Spent under a minute on a page in code, visit 3."


# ---- the stage ----------------------------------------------------------------------------------------------------

def test_a_closed_session_gets_a_note_and_the_day_gets_a_recap(memory):
    add(memory, 1, D, 10, summary="You read the payroll plan.")
    add(memory, 2, D + timedelta(minutes=15), 5)
    model = FakeModel([GOOD_SESSION, GOOD_DAY])

    report = run_rollups(job(memory, model), now=NOW, tz=UTC)

    key = group_sessions(to_items(memory.rollup_entries(since_us=0)))[0].key
    note = memory.get_session_notes([key])[key]
    assert note == {"title": "Reviewing the payroll plan", "tag": "finance", "tldr": "You read the payroll plan. You noted the review dates."}
    assert (report["session_notes"], report["day_notes"], report["model"]) == (1, 1, "fake-1b")
    assert "You read the payroll plan." in model.prompts[0][1]            # the entry's own summary is what the model sees
    assert "- “Page 2” in msedge (5 min)" in model.prompts[0][1]          # the entry without one: its title and time
    assert memory.day_note_key("2026-10-20") is not None


def test_an_open_session_is_left_alone(memory):
    add(memory, 1, NOW - timedelta(minutes=10), 5)          # ended 5 minutes ago: still open
    loaded = []

    report = run_rollups(RollupJob(memory, lambda: loaded.append(1) or FakeModel([])), now=NOW, tz=UTC)

    assert loaded == [] and report["session_notes"] == 0


def test_nothing_new_means_the_model_is_not_loaded_again(memory):
    add(memory, 1, D, 10)
    run_rollups(job(memory, FakeModel([GOOD_SESSION, GOOD_DAY])), now=NOW, tz=UTC)
    loaded = []

    report = run_rollups(RollupJob(memory, lambda: loaded.append(1) or FakeModel([])), now=NOW, tz=UTC)

    assert loaded == [] and report["session_notes"] == 0 and report["day_notes"] == 0


def test_a_session_that_grew_gets_a_new_note_and_a_new_recap(memory):
    add(memory, 1, D, 10)
    run_rollups(job(memory, FakeModel([GOOD_SESSION, GOOD_DAY])), now=NOW, tz=UTC)
    add(memory, 2, D + timedelta(minutes=20), 5)             # same session, now bigger: different key

    report = run_rollups(job(memory, FakeModel([GOOD_SESSION, GOOD_DAY])), now=NOW, tz=UTC)

    assert (report["session_notes"], report["day_notes"]) == (1, 1)


def test_unusable_model_output_is_stored_as_empty_and_not_retried(memory):
    add(memory, 1, D, 10)
    report = run_rollups(job(memory, FakeModel(["I'm sorry, I can't help with that."])), now=NOW, tz=UTC)
    key = group_sessions(to_items(memory.rollup_entries(since_us=0)))[0].key

    assert report["unusable"] == 1 and memory.get_session_notes([key])[key]["title"] is None
    assert key in memory.session_note_keys()                 # remembered, so the next run does not ask again


def test_a_model_error_keeps_what_was_written_and_retries_the_rest(memory):
    for n in range(3):
        add(memory, n + 1, D + timedelta(hours=3 * n), 10)  # three separate sessions
    model = FakeModel([GOOD_SESSION, RuntimeError("out of memory")])

    report = run_rollups(job(memory, model), now=NOW, tz=UTC)

    assert report["session_notes"] == 1 and report["failed"] == 1
    assert len(memory.session_note_keys()) == 1


def test_only_max_sessions_notes_are_written_per_run_and_the_newest_first(memory):
    for n in range(4):
        add(memory, n + 1, D + timedelta(hours=3 * n), 10)
    model = FakeModel([GOOD_SESSION] * 2)

    run_rollups(job(memory, model, max_sessions=2), now=NOW, tz=UTC)

    assert len(model.prompts) == 2 and "Page 3" in model.prompts[0][1] and "Page 4" in model.prompts[1][1]   # the newest two
    assert len(memory.session_note_keys()) == 2
    assert memory.day_note_key("2026-10-20") is None         # not every session of the day has a note yet


@pytest.mark.parametrize("raw, expected", [
    (GOOD_SESSION, ("Reviewing the payroll plan", "finance", "You read the payroll plan. You noted the review dates.")),
    ("**TITLE:** Sprint planning\nTAG: Planning work\nTLDR: You planned the sprint goals and risks for the team.",
     ("Sprint planning", "planning", "You planned the sprint goals and risks for the team.")),
    ("TITLE: Claude session\nTAG: coding\n\nYou spent 19 minutes working in Claude on one conversation.",
     ("Claude session", "coding", "You spent 19 minutes working in Claude on one conversation.")),
    # a generic or copied title is dropped, the sentences are kept: the dashboard names the session after its main page
    ("TITLE: Work Session\nTAG: work\nTLDR: You did a thing here today.", ("", "work", "You did a thing here today.")),
    ("TITLE: Session Wednesday 21 October, 12:03 to 13:03\nTAG: work\nTLDR: You read three pages about plans.", ("", "work", "You read three pages about plans.")),
    ("TITLE: ab\nTAG: x\nTLDR: You did a thing here today.", ("", "x", "You did a thing here today.")),
    ("TITLE: Plan review\nTAG: plan\nTLDR: I spent 11 minutes on the plan and then I read more.", None),
    ("just some free text with no fields at all", ("", "", "just some free text with no fields at all")),   # sentences with no labels still count
    ("", None), ("TITLE: Fine title here\nTAG: x", None),                                                      # nothing to say
    ("TITLE: Fine title here\nTAG: x\nTLDR: short", None),
])
def test_parse_session_note(raw, expected):
    assert parse_session_note(raw) == expected


def test_the_prompt_lists_each_page_once_with_its_totals(memory):
    for n in range(6):                                       # six visits to the same page
        add(memory, 1 + n, D + timedelta(minutes=8 * n), 5, title="Budget sheet")
    add(memory, 20, D + timedelta(minutes=50), 5, title="Notes", summary="You wrote up the budget review.")
    model = FakeModel([GOOD_SESSION])

    run_rollups(job(memory, model), now=NOW, tz=UTC)

    user = model.prompts[0][1]
    assert user.count("Budget sheet") == 1 and "“Budget sheet” in msedge (30 min)" in user
    assert "You wrote up the budget review." in user


def test_the_prompt_lists_each_page_once_with_its_totals(memory):
    for n in range(6):                                       # six visits to the same page
        add(memory, 1 + n, D + timedelta(minutes=8 * n), 5, title="Budget sheet")
    add(memory, 20, D + timedelta(minutes=50), 5, title="Notes", summary="You wrote up the budget review.")
    model = FakeModel([GOOD_SESSION])

    run_rollups(job(memory, model), now=NOW, tz=UTC)

    user = model.prompts[0][1]
    assert user.count("Budget sheet") == 1 and "“Budget sheet” in msedge (30 min)" in user
    assert "You wrote up the budget review." in user
