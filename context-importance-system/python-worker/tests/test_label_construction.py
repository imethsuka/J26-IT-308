"""Tests for weak-supervision label construction.

Covers each signal (revisit, continuation, retrieval) in isolation and the combination rule on the
edge cases called out in the spec: conflicting signals and missing feedback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from adaptation.label_construction import (
    DEFAULT_LABEL_DELAY,
    DEFAULT_LABEL_HORIZON,
    LabelConstructor,
    LabelWeights,
    Timeline,
    combine_importance_label,
    label_pending_records,
)

T0 = datetime(2026, 9, 20, 9, 0, 0, tzinfo=timezone.utc)


@dataclass
class Rec:
    id: int
    ts: datetime
    app: str | None = "chrome"
    domain: str | None = "docs.example.com"
    title: str | None = "Design doc"
    label: int | None = None


def at(minutes: float, **kw) -> Rec:
    return Rec(ts=T0 + timedelta(minutes=minutes), **kw)


# =========================================================================== combination rule

class TestCombineRule:
    def test_nothing_fired_is_not_important(self) -> None:
        r = combine_importance_label(revisited=False, continued=False, retrieval=False)
        assert r.score == 0.0 and r.label == 0

    def test_retrieval_alone_is_important(self) -> None:
        # retrieval weight 0.5 over full denominator 1.0 -> exactly threshold.
        r = combine_importance_label(revisited=False, continued=False, retrieval=True)
        assert r.score == pytest.approx(0.5)
        assert r.label == 1

    def test_revisit_plus_continuation_is_important_even_without_retrieval_use(self) -> None:
        r = combine_importance_label(revisited=True, continued=True, retrieval=False)
        # (0.3+0.2)/(0.5+0.3+0.2) = 0.5
        assert r.score == pytest.approx(0.5)
        assert r.label == 1

    def test_revisit_alone_depends_on_retrieval_feedback_presence(self) -> None:
        with_feedback = combine_importance_label(revisited=True, continued=False, retrieval=False)
        # 0.3 / 1.0
        assert with_feedback.score == pytest.approx(0.3) and with_feedback.label == 0

        missing_feedback = combine_importance_label(revisited=True, continued=False, retrieval=None)
        # retrieval drops out: 0.3 / (0.3+0.2) = 0.6
        assert missing_feedback.score == pytest.approx(0.6) and missing_feedback.label == 1

    def test_continuation_alone_is_not_important(self) -> None:
        r = combine_importance_label(revisited=False, continued=True, retrieval=False)
        assert r.score == pytest.approx(0.2) and r.label == 0

    # ---- conflicting signals ----

    def test_conflict_used_but_never_revisited_is_important(self) -> None:
        r = combine_importance_label(revisited=False, continued=False, retrieval=True)
        assert r.label == 1  # actual downstream use outweighs absence of behavioural proxies

    def test_conflict_revisited_and_continued_but_explicitly_not_retrieved(self) -> None:
        r = combine_importance_label(revisited=True, continued=True, retrieval=False)
        assert r.label == 1  # two proxies reach the threshold despite explicit non-use

    # ---- missing feedback ----

    def test_missing_retrieval_uses_behavioural_only(self) -> None:
        r = combine_importance_label(revisited=False, continued=False, retrieval=None)
        assert r.retrieval_available is False
        assert r.score == 0.0 and r.label == 0

    def test_missing_retrieval_does_not_penalise_strong_behaviour(self) -> None:
        r = combine_importance_label(revisited=True, continued=True, retrieval=None)
        # (0.3+0.2)/(0.3+0.2) = 1.0
        assert r.score == pytest.approx(1.0) and r.label == 1

    # ---- float retrieval score ----

    def test_float_retrieval_score_scales_contribution(self) -> None:
        r = combine_importance_label(revisited=False, continued=False, retrieval=0.4)
        # 0.5*0.4 / 1.0 = 0.2
        assert r.score == pytest.approx(0.2) and r.label == 0

    def test_float_retrieval_is_clamped(self) -> None:
        assert combine_importance_label(False, False, 5.0).score == pytest.approx(0.5)
        assert combine_importance_label(False, False, -1.0).score == pytest.approx(0.0)

    def test_threshold_is_configurable(self) -> None:
        r = combine_importance_label(revisited=True, continued=False, retrieval=None, threshold=0.7)
        assert r.score == pytest.approx(0.6) and r.label == 0

    def test_weights_are_configurable(self) -> None:
        weights = LabelWeights(retrieval=0.0, revisit=1.0, continuation=0.0)
        r = combine_importance_label(revisited=True, continued=False, retrieval=None, weights=weights)
        assert r.score == pytest.approx(1.0)


# =========================================================================== revisit signal

class TestRevisitSignal:
    def test_first_ever_context_is_not_revisited(self) -> None:
        target = at(0, id=1)
        timeline = Timeline([target])
        assert timeline.is_revisited(target) is False

    def test_same_context_later_counts_as_revisit(self) -> None:
        target = at(0, id=1)
        later = at(120, id=2)  # same app/domain/title, 2h later
        timeline = Timeline([target, later])
        assert timeline.is_revisited(target) is True

    def test_earlier_occurrence_does_not_count(self) -> None:
        earlier = at(-60, id=1)
        target = at(0, id=2)
        timeline = Timeline([earlier, target])
        assert timeline.is_revisited(target) is False  # revisit looks forward only

    def test_different_context_is_not_a_revisit(self) -> None:
        target = at(0, id=1)
        other = at(120, id=2, title="Unrelated page")
        timeline = Timeline([target, other])
        assert timeline.is_revisited(target) is False

    def test_consecutive_captures_of_one_visit_are_not_a_revisit(self) -> None:
        # The collector sends a snapshot per screen change: many same-context records, one visit.
        target = at(0, id=1)
        frames = [at(m, id=10 + m) for m in (1, 2, 3)]
        timeline = Timeline([target, *frames])
        assert timeline.is_revisited(target) is False

    def test_leaving_and_coming_back_is_a_revisit(self) -> None:
        target = at(0, id=1)
        elsewhere = at(5, id=2, title="Unrelated page")
        back = at(10, id=3)
        timeline = Timeline([target, elsewhere, back])
        assert timeline.is_revisited(target) is True
        assert timeline.is_revisited(back) is False  # nothing after it

    def test_every_frame_of_a_visit_sees_the_later_return(self) -> None:
        first, second = at(0, id=1), at(1, id=2)            # one visit, two frames
        timeline = Timeline([first, second, at(5, id=3, title="Other"), at(10, id=4)])
        assert timeline.is_revisited(first) is True
        assert timeline.is_revisited(second) is True

    def test_revisit_window_bounds_the_lookahead(self) -> None:
        target = at(0, id=1)
        later = at(200, id=2)  # 200 min later
        timeline = Timeline([target, later])
        assert timeline.is_revisited(target, window=timedelta(hours=1)) is False
        assert timeline.is_revisited(target, window=timedelta(hours=4)) is True


# =========================================================================== continuation signal

class TestContinuationSignal:
    def test_same_session_repeat_is_not_continuation(self) -> None:
        target = at(0, id=1)
        soon = at(5, id=2)  # 5 min later, within the 30-min session gap, nothing in between
        timeline = Timeline([target, soon], session_gap=timedelta(minutes=30))
        assert timeline.is_continued(target) is False  # same session
        assert timeline.is_revisited(target) is False  # and the same visit, so not a revisit either

    def test_reappearance_in_a_later_session_is_continuation(self) -> None:
        target = at(0, id=1)
        next_session = at(120, id=2)  # 2h later -> gap exceeds 30 min -> new session
        timeline = Timeline([target, next_session], session_gap=timedelta(minutes=30))
        assert timeline.is_continued(target) is True

    def test_continuation_needs_the_same_context(self) -> None:
        target = at(0, id=1)
        other_next_session = at(120, id=2, title="Different")
        timeline = Timeline([target, other_next_session], session_gap=timedelta(minutes=30))
        assert timeline.is_continued(target) is False

    def test_intervening_activity_keeps_one_session(self) -> None:
        # Continuous activity every 10 min keeps everything in session 0, so no continuation.
        target = at(0, id=1)
        fillers = [at(10 * k, id=10 + k, title="Other") for k in range(1, 12)]  # up to 110 min
        repeat = at(120, id=99)  # same context, but no >30min gap anywhere
        timeline = Timeline([target, *fillers, repeat], session_gap=timedelta(minutes=30))
        assert timeline.is_continued(target) is False
        assert timeline.is_revisited(target) is True  # still a revisit


# =========================================================================== constructor / due

class TestLabelConstructor:
    def test_only_due_and_unlabelled_records_are_labelled(self) -> None:
        now = T0 + timedelta(hours=100)
        old_unlabelled = at(0, id=1)
        recent_unlabelled = Rec(id=2, ts=now - timedelta(hours=1))
        old_labelled = at(30, id=3, label=1)

        # old_unlabelled is revisited+continued by a later-session copy.
        later = at(60 * 60, id=4)  # 60h later, same context
        records = [old_unlabelled, recent_unlabelled, old_labelled, later]

        constructor = LabelConstructor(retrieval_fn=None)
        results = constructor.construct_batch(records, now=now, labelled_ids={3})
        labelled_ids = {rid for rid, _ in results}

        assert 1 in labelled_ids          # old + unlabelled
        assert 2 not in labelled_ids      # too recent
        assert 3 not in labelled_ids      # already labelled

    def test_injected_retrieval_callback_is_used(self) -> None:
        now = T0 + timedelta(hours=100)
        target = at(0, id=1)  # no revisit/continuation
        tail = at(49 * 60, id=9, title="other")  # covers the target's horizon
        calls = []

        def retrieval_fn(record):
            calls.append(record.id)
            return True

        constructor = LabelConstructor(retrieval_fn=retrieval_fn)
        results = dict(constructor.construct_batch([target, tail], now=now))
        assert calls == [1]
        assert results[1].label == 1  # retrieval alone reaches threshold

    def test_default_constructor_treats_retrieval_as_missing(self) -> None:
        now = T0 + timedelta(hours=100)
        target = at(0, id=1)
        tail = at(49 * 60, id=9, title="other")
        result = dict(LabelConstructor().construct_batch([target, tail], now=now))[1]
        assert result.retrieval_available is False

    def test_default_label_delay_is_48h(self) -> None:
        assert DEFAULT_LABEL_DELAY == timedelta(hours=48)
        constructor = LabelConstructor()
        assert constructor.is_due(at(0, id=1), now=T0 + timedelta(hours=47)) is False
        assert constructor.is_due(at(0, id=1), now=T0 + timedelta(hours=49)) is True


# =========================================================================== horizon bounding

H48 = timedelta(hours=48)


class TestLabelHorizon:
    """Revisit and continuation may only look at ``(ts, ts + H]`` -- never further."""

    def test_default_horizon_is_48h(self) -> None:
        assert DEFAULT_LABEL_HORIZON == timedelta(hours=48)

    def test_revisit_beyond_horizon_is_ignored(self) -> None:
        target = at(0, id=1)
        beyond = at(60 * 60, id=2)  # same context 60h later -> outside a 48h horizon
        timeline = Timeline([target, beyond], horizon=H48)
        assert timeline.is_revisited(target) is False

    def test_revisit_inside_horizon_still_counts(self) -> None:
        timeline = Timeline([at(0, id=1), at(47 * 60, id=2)], horizon=H48)
        assert timeline.is_revisited(at(0, id=1)) is True

    def test_revisit_at_exactly_the_horizon_edge_counts(self) -> None:
        # The window is half-open at the start and closed at the end: (ts, ts + H].
        timeline = Timeline([at(0, id=1), at(48 * 60, id=2)], horizon=H48)
        assert timeline.is_revisited(at(0, id=1)) is True

    def test_continuation_beyond_horizon_is_ignored(self) -> None:
        target = at(0, id=1)
        beyond = at(60 * 60, id=2)  # a later session, but outside the horizon
        timeline = Timeline([target, beyond], horizon=H48, session_gap=timedelta(minutes=30))
        assert timeline.is_continued(target) is False

    def test_continuation_inside_horizon_still_counts(self) -> None:
        target = at(0, id=1)
        inside = at(10 * 60, id=2)
        timeline = Timeline([target, inside], horizon=H48, session_gap=timedelta(minutes=30))
        assert timeline.is_continued(target) is True

    def test_horizon_is_configurable_on_the_constructor(self) -> None:
        now = T0 + timedelta(hours=500)
        target = at(0, id=1)
        revisit = at(60 * 60, id=2)            # 60h later
        tail = at(200 * 60, id=3, title="unrelated")

        short = LabelConstructor(label_horizon=H48)
        long = LabelConstructor(label_horizon=timedelta(hours=72))
        assert dict(short.construct_batch([target, revisit, tail], now=now))[1].revisited is False
        assert dict(long.construct_batch([target, revisit, tail], now=now))[1].revisited is True


# =========================================================================== censoring

class TestCensoring:
    """A record whose horizon reaches past the newest ingested data cannot be labelled yet."""

    def test_record_without_a_full_horizon_of_data_is_left_unlabelled(self) -> None:
        now = T0 + timedelta(hours=100)              # wall clock says "past the delay"
        target = at(0, id=1)
        tail = at(10 * 60, id=2, title="other")      # newest ingested ts is only T0 + 10h
        results = LabelConstructor().construct_batch([target, tail], now=now)
        assert [rid for rid, _ in results] == []     # T0 + 48h > newest -> censored

    def test_record_with_a_full_horizon_of_data_is_labelled(self) -> None:
        now = T0 + timedelta(hours=100)
        target = at(0, id=1)
        tail = at(49 * 60, id=2, title="other")      # newest = T0 + 49h
        results = LabelConstructor().construct_batch([target, tail], now=now)
        assert [rid for rid, _ in results] == [1]

    def test_censoring_boundary_is_inclusive(self) -> None:
        now = T0 + timedelta(hours=100)
        target = at(0, id=1)
        tail = at(48 * 60, id=2, title="other")      # newest == T0 + 48h exactly
        results = LabelConstructor().construct_batch([target, tail], now=now)
        assert [rid for rid, _ in results] == [1]

    def test_only_records_with_a_full_horizon_are_labelled_near_the_end_of_data(self) -> None:
        # Hourly records over 60h. Only those followed by a full 48h of data are labellable;
        # everything in the trailing 48h is censored, however old the wall clock says it is.
        now = T0 + timedelta(hours=500)
        records = [at(60 * h, id=h, title=f"page-{h}") for h in range(61)]
        results = LabelConstructor().construct_batch(records, now=now)
        assert sorted(rid for rid, _ in results) == list(range(13))  # ts 0h..12h (12 + 48 = 60)


# =========================================================================== determinism

class TestLabelStability:
    """Labels must be reproducible: a later run over more data must not change them."""

    @staticmethod
    def _labels(records, now) -> dict:
        return {rid: r.label for rid, r in LabelConstructor().construct_batch(records, now=now)}

    def test_label_unchanged_when_later_data_is_added_beyond_the_horizon(self) -> None:
        now = T0 + timedelta(hours=500)
        target = at(0, id=1)
        tail = at(49 * 60, id=2, title="unrelated")        # uncensors the target; not a revisit
        first = self._labels([target, tail], now)
        assert first[1] == 0                                # nothing fired inside the horizon

        # The same context returns 60h later -- beyond the closed 48h horizon.
        late_revisit = at(60 * 60, id=3)                    # same app/domain/title as target
        later_tail = at(120 * 60, id=4, title="unrelated")
        second = self._labels([target, tail, late_revisit, later_tail], now)
        assert second[1] == first[1]

    def test_rerunning_on_a_superset_gives_identical_labels(self) -> None:
        now = T0 + timedelta(hours=500)
        base = [at(0, id=1), at(2 * 60, id=2), at(49 * 60, id=3, title="unrelated")]
        first = self._labels(base, now)

        grown = base + [at(60 * 60, id=4), at(200 * 60, id=5, title="unrelated")]
        second = self._labels(grown, now)

        assert first  # guard: the first run must actually have labelled something
        for rid, label in first.items():
            assert second[rid] == label, f"label for record {rid} changed on rerun"

    def test_rerunning_on_the_same_data_is_identical(self) -> None:
        now = T0 + timedelta(hours=500)
        records = [at(0, id=1), at(2 * 60, id=2), at(49 * 60, id=3, title="unrelated")]
        assert self._labels(records, now) == self._labels(records, now)

    def test_label_version_distinguishes_the_horizon(self) -> None:
        assert LabelConstructor().label_version == "v3/h48"
        assert LabelConstructor().label_version == LabelConstructor().label_version
        assert (
            LabelConstructor(label_horizon=H48).label_version
            != LabelConstructor(label_horizon=timedelta(hours=72)).label_version
        )


# =========================================================================== storage-backed runner

class TestLabelPendingRecords:
    """``label_pending_records`` against a real store: censoring, stability, label_version."""

    @staticmethod
    def _store():
        from storage.database import connect, migrate

        conn = connect(":memory:")
        migrate(conn)
        return conn

    @staticmethod
    def _add(conn, minutes: float, title: str = "Design doc") -> int:
        from storage.context_store import ContextRecord, insert_context_record

        return insert_context_record(conn, ContextRecord(
            ts=T0 + timedelta(minutes=minutes), decision="save", features={"tmp_dwell_seconds": 10.0},
            app="chrome", domain="docs.example.com", title=title,
        ))

    def test_censored_records_stay_unlabelled_in_the_store(self) -> None:
        from storage.context_store import list_context_records

        conn = self._store()
        try:
            self._add(conn, 0)                              # needs data out to T0 + 48h
            self._add(conn, 10 * 60, title="other")         # newest ingested is only T0 + 10h
            written = label_pending_records(conn, LabelConstructor(), now=T0 + timedelta(hours=100))
            assert written == []
            assert all(r.label is None for r in list_context_records(conn))
        finally:
            conn.close()

    def test_labels_are_stable_and_versioned_across_runs(self) -> None:
        from storage.context_store import list_context_records

        conn = self._store()
        now = T0 + timedelta(hours=500)
        try:
            target = self._add(conn, 0)
            self._add(conn, 49 * 60, title="unrelated")     # uncensors the target; not a revisit
            first = label_pending_records(conn, LabelConstructor(), now=now)
            assert [rid for rid, _ in first] == [target]
            before = {r.id: (r.label, r.label_version) for r in list_context_records(conn)}
            assert before[target][0] == 0
            assert before[target][1] == LabelConstructor().label_version

            # More activity arrives, including the same context well beyond the horizon.
            self._add(conn, 60 * 60)
            self._add(conn, 200 * 60, title="unrelated")
            label_pending_records(conn, LabelConstructor(), now=now)

            after = {r.id: (r.label, r.label_version) for r in list_context_records(conn)}
            assert after[target] == before[target], "an already-labelled record was relabelled"
        finally:
            conn.close()
