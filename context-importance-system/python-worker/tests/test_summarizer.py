from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adaptation.summarizer import (
    SummarizeConfig, SummarizeJob, build_prompt, clean_summary, run_summaries, select_text,
)
from features.text_merge import TextDocument
from storage.memory_store import MemoryStore
from tests.test_raw_buffer import XorProtector

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 20, 9, 12, tzinfo=timezone.utc)


class FakeModel:
    name = "fake-1b"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def complete(self, system, user, *, max_tokens):
        self.prompts.append((system, user))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def add_entry(memory: MemoryStore, n: int, *, text="Quarterly plan for the platform team. We will migrate the queue.", needs=True, persisted=True):
    doc = TextDocument()
    if text:
        doc.merge(text)
    return memory.save_episode(
        doc_id=f"doc{n}", context_key=f"key{n}", app="msedge", domain="docs.example.com", title=f"Plan {n}",
        document=doc if persisted else None, filter_version="caprlm/test",
        episode={"start": T0, "end": T1, "n_captures": 5, "active_seconds": 700.0, "key_presses": 10, "mouse_clicks": 2,
                 "mouse_scrolls": 30, "mouse_moves": 0, "max_score": 0.9, "words_seen": 20, "words_added": 20,
                 "truncated_captures": 0},
        entry_text="x", entry_json={"app": "msedge", "domain": "docs.example.com", "title": f"Plan {n}", "start": T0.isoformat(),
                                    "active_seconds": 700.0, "doc_id": f"doc{n}"},
        tier="high", needs_summary=needs, text_persisted=persisted,
    )


@pytest.fixture()
def memory(tmp_path):
    return MemoryStore(tmp_path / "memory.db", protector=XorProtector())


def job(memory, model, **cfg):
    return SummarizeJob(memory=memory, model_factory=lambda: model, config=SummarizeConfig(**cfg))


def stored(memory):
    return {e["id"]: e for e in memory.entries()}


def test_a_waiting_entry_gets_its_summary_and_the_model_name(memory):
    eid = add_entry(memory, 1)
    model = FakeModel(["You read the quarterly plan for the platform team and its queue migration."])

    report = run_summaries(job(memory, model))

    entry = stored(memory)[eid]
    assert entry["summary"].startswith("You read the quarterly plan")
    assert entry["summary_model"] == "fake-1b" and entry["needs_summary"] == 0
    assert (report["waiting"], report["summarized"], report["model"]) == (1, 1, "fake-1b")


def test_the_prompt_names_the_page_and_carries_this_pages_text_only(memory):
    add_entry(memory, 1, text="Alpha beta gamma delta epsilon zeta eta theta")
    add_entry(memory, 2, text="Unrelated other page about gardening tools and soil")
    model = FakeModel(["You read page two.", "You read page one."])

    run_summaries(job(memory, model))

    first_system, first_user = model.prompts[0]     # newest entry first
    assert "Page title: Plan 2" in first_user and "Site: docs.example.com" in first_user
    assert "gardening" in first_user and "Alpha" not in first_user
    # app, time and duration are shown by the page itself: leaving them out stopped the model from just repeating them
    assert "msedge" not in first_user and "minutes" not in first_user and "Time spent" not in first_user
    assert "Do not invent" in first_system and "[PERSON_1]" in first_system


def test_nothing_waiting_means_the_model_is_never_loaded(memory):
    add_entry(memory, 1, needs=False)
    loaded = []
    j = SummarizeJob(memory=memory, model_factory=lambda: loaded.append(1) or FakeModel([]))

    report = run_summaries(j)

    assert loaded == [] and report["waiting"] == 0


def test_entries_without_kept_text_are_not_waiting(memory):
    add_entry(memory, 1, text="", persisted=False, needs=True)
    assert memory.pending_summaries(limit=10) == []


def test_a_model_error_leaves_the_rest_waiting_for_the_next_run(memory):
    ids = [add_entry(memory, n) for n in (1, 2, 3)]
    model = FakeModel(["You read page three, a plan for the platform team.", RuntimeError("out of memory")])

    report = run_summaries(job(memory, model))

    done = stored(memory)
    assert done[ids[2]]["summary"] and done[ids[2]]["needs_summary"] == 0
    assert done[ids[1]]["needs_summary"] == 1 and done[ids[0]]["needs_summary"] == 1      # still waiting
    assert (report["summarized"], report["failed"]) == (1, 1)


def test_unusable_output_is_discarded_and_not_retried_forever(memory):
    eid = add_entry(memory, 1)

    report = run_summaries(job(memory, FakeModel(["I'm sorry, I cannot help with that."])))

    entry = stored(memory)[eid]
    assert entry["summary"] is None and entry["summary_model"] is None and entry["needs_summary"] == 0
    assert report["discarded"] == 1


def test_only_max_entries_are_done_per_run(memory):
    for n in range(5):
        add_entry(memory, n)
    model = FakeModel(["You read a plan for the platform team."] * 5)

    run_summaries(job(memory, model, max_entries=2))

    assert len(model.prompts) == 2
    assert len(memory.pending_summaries(limit=10)) == 3


@pytest.mark.parametrize("raw, expected", [
    ("  \"You read the plan.  It was long and had many parts.  Extra third sentence here.\"  ",
     "You read the plan. It was long and had many parts."),
    ("**You** read `the` plan for the team.", "You read the plan for the team."),
    ("short", None), ("", None), ("As an AI I do not know what you did.", None),
    ("You read an email from [PERSON_1] to [EMAIL_2] about [AMOUNT_1].", "You read an email from [PERSON_1] to [EMAIL_2] about [AMOUNT_1]."),
])
def test_clean_summary(raw, expected):
    assert clean_summary(raw) == expected


def test_long_summaries_are_cut_at_a_word():
    out = clean_summary("You read " + "word " * 200, max_chars=60)
    assert out is not None and len(out) <= 64 and out.endswith("...")


def test_long_pages_keep_their_start_and_their_end():
    text = "start " + "middle " * 2000 + "finish"
    cut = select_text(text, 1000)

    assert cut.startswith("start") and cut.endswith("finish") and "[...]" in cut and len(cut) < 1100
    assert select_text("tiny page", 1000) == "tiny page"


def test_prompt_survives_missing_facts_and_notes_repeat_visits():
    _, bare = build_prompt({}, "some text here")
    assert bare == "Page text:\nsome text here"
    _, again = build_prompt({"title": "Plan", "visit_number": 3}, "x")
    assert "visit number 3" in again


def test_text_stored_without_a_real_filter_is_never_summarised(memory):
    doc = TextDocument()
    doc.merge("Password reminder: the portal login is hunter22xyz for the finance system")
    ep = {"start": T0, "end": T1, "n_captures": 3, "active_seconds": 700.0, "key_presses": 1, "mouse_clicks": 1,
          "mouse_scrolls": 1, "mouse_moves": 0, "max_score": 0.9, "words_seen": 10, "words_added": 10, "truncated_captures": 0}
    memory.save_episode(doc_id="raw1", context_key="rawk", app="msedge", domain="x.example.com", title="Portal", document=doc,
                        filter_version="passthrough-dev", episode=ep, entry_text="x", entry_json={"title": "Portal", "start": T0.isoformat()},
                        tier="high", needs_summary=True, text_persisted=True)
    model = FakeModel(["You read a page."])

    report = run_summaries(job(memory, model))

    assert report["waiting"] == 0 and model.prompts == []


def test_progress_is_reported_after_each_entry(memory):
    for n in (1, 2, 3):
        add_entry(memory, n)
    seen = []
    model = FakeModel(["You read a plan for the platform team."] * 3)

    run_summaries(SummarizeJob(memory=memory, model_factory=lambda: model, progress=lambda d, t: seen.append((d, t))))

    assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_the_model_does_not_see_menu_labels_when_the_page_has_content(memory):
    menu = "File\nEdit\nSelection\nView\nGo\nRun\nTerminal\nHelp\n"
    body = ("Quarterly plan for the platform team. We will migrate the queue service in two phases and review the "
            "rollout every Friday with the support team.\n") * 3
    add_entry(memory, 1, text=menu + body)
    model = FakeModel(["You read the quarterly plan for the platform team."])

    run_summaries(job(memory, model))

    assert "Quarterly plan" in model.prompts[0][1] and "Selection" not in model.prompts[0][1]
    run_summaries(job(memory, FakeModel([]), clean_pages=False))      # switching it off is possible


def test_cleaning_can_be_switched_off(memory):
    menu = "File\nEdit\nSelection\nView\nGo\nRun\nTerminal\nHelp\n"
    add_entry(memory, 1, text=menu + "Quarterly plan for the platform team and the queue migration schedule.")
    model = FakeModel(["You read the quarterly plan for the platform team."])

    run_summaries(job(memory, model, clean_pages=False))

    assert "Selection" in model.prompts[0][1]
