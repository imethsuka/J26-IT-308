from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adaptation.privacy import FilterResult, PrivacyContext
from adaptation.refilter import refilter_memory
from adaptation.summarizer import SummarizeJob, run_summaries
from features.text_merge import TextDocument
from storage.memory_store import MemoryStore
from tests.test_raw_buffer import XorProtector

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
SECRET_TEXT = "Portal login: password hunter22xyz for the finance system, call 077-123-4567 about the Q3 salary review today"


class Redactor:
    """Hides the digits-and-secret words, keeps everything else."""

    version = "caprlm/test"

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        redacted = text.replace("hunter22xyz", "").replace("077-123-4567", "[PHONE_1]")
        return FilterResult(text=redacted, filter_version=self.version, redactions=int(redacted != text))


class BlockAll:
    version = "block/0"

    def filter(self, text, ctx):
        return FilterResult(text="", blocked=True, filter_version=self.version)


class Broken:
    version = "broken/0"

    def filter(self, text, ctx):
        raise ConnectionError("service down")


class FakeModel:
    name = "fake-1b"

    def __init__(self):
        self.prompts = []

    def complete(self, system, user, *, max_tokens):
        self.prompts.append(user)
        return "You read the finance portal notes about the salary review."


@pytest.fixture()
def memory(tmp_path):
    return MemoryStore(tmp_path / "memory.db", protector=XorProtector())


def add(memory, n, *, version, text=SECRET_TEXT, tier="high", needs=True):
    doc = TextDocument()
    doc.merge(text)
    return memory.save_episode(
        doc_id=f"doc{n}", context_key=f"key{n}", app="msedge", domain="portal.example.com", title=f"Portal {n}", document=doc,
        filter_version=version,
        episode={"start": T0 + timedelta(hours=n), "end": T0 + timedelta(hours=n, minutes=9), "n_captures": 4, "active_seconds": 500.0,
                 "key_presses": 1, "mouse_clicks": 1, "mouse_scrolls": 1, "mouse_moves": 0, "max_score": 0.9, "words_seen": 20,
                 "words_added": 20, "truncated_captures": 0},
        entry_text="x", entry_json={"app": "msedge", "title": f"Portal {n}", "start": T0.isoformat(), "active_seconds": 500.0},
        tier=tier, needs_summary=needs, text_persisted=True)


def page_text(memory, n):
    stored = memory.load_document(f"doc{n}")
    return " ".join(stored.document.text().split()) if stored and stored.document.fragments else ""


def entry(memory, eid):
    return {e["id"]: e for e in memory.entries()}[eid]


def test_a_dry_run_reports_and_changes_nothing(memory):
    add(memory, 1, version="passthrough-dev")

    report = refilter_memory(memory, Redactor(), apply=False)

    assert (report.pages, report.converted) == (1, 1)
    assert "hunter22xyz" in page_text(memory, 1)


def test_applying_replaces_the_text_and_marks_the_entries_filtered(memory):
    eid = add(memory, 1, version="passthrough-dev")

    report = refilter_memory(memory, Redactor(), apply=True)

    text = page_text(memory, 1)
    assert "hunter22xyz" not in text and "077-123-4567" not in text and "[PHONE_1]" in text
    e = entry(memory, eid)
    assert e["filter_version"] == "caprlm/test" and e["text_persisted"] == 1 and e["needs_summary"] == 1
    assert memory.load_document("doc1").filter_version == "caprlm/test"
    assert report.converted == 1 and report.versions == {"caprlm/test"}
    assert memory.documents_to_refilter() == []                      # nothing left to clean


def test_pages_that_already_went_through_a_real_filter_are_left_alone(memory):
    add(memory, 1, version="caprlm/logreg-v0.2", text="Already clean text about the plan and the review dates here")

    report = refilter_memory(memory, Redactor(), apply=True)

    assert report.pages == 0 and "Already clean" in page_text(memory, 1)


def test_a_blocked_page_loses_its_text_and_its_entries_stop_waiting(memory):
    eid = add(memory, 1, version="passthrough-dev")

    report = refilter_memory(memory, BlockAll(), apply=True)

    assert report.dropped == 1 and page_text(memory, 1) == ""
    e = entry(memory, eid)
    assert e["text_persisted"] == 0 and e["needs_summary"] == 0


def test_a_failing_filter_leaves_the_page_exactly_as_it_was(memory):
    eid = add(memory, 1, version="passthrough-dev")

    report = refilter_memory(memory, Broken(), apply=True)

    assert report.failed == 1 and report.converted == 0
    assert "hunter22xyz" in page_text(memory, 1)
    assert entry(memory, eid)["filter_version"] == "passthrough-dev"


def test_the_summariser_ignores_unfiltered_pages_until_they_are_cleaned(memory):
    add(memory, 1, version="passthrough-dev")
    model = FakeModel()
    before = run_summaries(SummarizeJob(memory=memory, model_factory=lambda: model))
    assert before["waiting"] == 0 and model.prompts == []

    refilter_memory(memory, Redactor(), apply=True)
    after = run_summaries(SummarizeJob(memory=memory, model_factory=lambda: model))

    assert after["summarized"] == 1
    assert "hunter22xyz" not in model.prompts[0] and "077-123-4567" not in model.prompts[0]


def test_limit_does_a_trial_on_the_newest_pages_only(memory):
    add(memory, 1, version="passthrough-dev")
    add(memory, 2, version="passthrough-dev")

    report = refilter_memory(memory, Redactor(), apply=True, limit=1)

    assert report.converted == 1 and len(memory.documents_to_refilter()) == 1
