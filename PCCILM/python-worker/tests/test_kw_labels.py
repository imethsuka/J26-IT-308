"""Tests for the user profile (Model 2's habits) and the Keep-Worthiness label built on it."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from adaptation.kw_labels import (
    KW_RULE_VERSION,
    KWLabelConstructor,
    KWParams,
    KWResult,
    kw_score,
    refindability,
)
from adaptation.label_construction import LabelConstructor, LabelResult, label_pending_records
from adaptation.user_profile import (
    USER_FEATURE_COLUMNS,
    ActivityIndex,
    UserProfile,
    load_profile,
    save_profile,
    user_features,
)

T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)  # a Thursday, 09:00 UTC
H = timedelta(hours=48)


@dataclass
class Rec:
    id: int
    ts: datetime
    app: str = "Code"
    domain: str | None = None
    title: str | None = "main.py"
    features: dict = field(default_factory=dict)


def at(hours: float = 0.0, minutes: float = 0.0) -> datetime:
    return T0 + timedelta(hours=hours, minutes=minutes)


def rec(i, ts, title="main.py", app="Code", keys=0.0, dwell=0.0, domain=None) -> Rec:
    return Rec(i, ts, app, domain, title, {"beh_key_presses": keys, "tmp_dwell_seconds": dwell, "tmp_hour_of_day": ts.hour})


def construct(records, **kw):
    constructor = KWLabelConstructor(label_delay=timedelta(0), label_horizon=H, **kw)
    now = max(r.ts for r in records) + H + timedelta(hours=1)
    results = dict(constructor.construct_batch(records, now=now))
    return constructor, results


def padded(records, until=60):
    """Records plus a trailing 'other' capture, so the 48 h horizon of the early ones is observed."""
    return list(records) + [rec(999, at(until), title="end", app="Notepad")]


# ----------------------------------------------------------------------------------- KW formula

class TestKwScore:
    def test_noisy_or_any_evidence_suffices(self):
        kw, need = kw_score(0.0, 0.0, None, None, loss=1.0, lam=0.3)
        assert (kw, need) == (0.0, 0.0)
        _, need = kw_score(0.5, 0.5, None, None, 1.0, 0.3)
        assert need == pytest.approx(0.75)

    def test_missing_signals_drop_out_not_count_as_zero(self):
        with_missing, _ = kw_score(0.6, 0.0, None, None, 1.0, 0.3)
        with_zero, _ = kw_score(0.6, 0.0, 0.0, 0.0, 1.0, 0.3)
        assert with_missing == with_zero  # a signal of 0 and a missing one differ only when > 0
        assert kw_score(0.6, 0.0, None, 0.5, 1.0, 0.3)[0] > with_missing  # retrieval adds evidence

    def test_loss_floor_keeps_easy_to_find_items_above_zero(self):
        easy, _ = kw_score(1.0, 0.0, None, None, loss=0.0, lam=0.3)
        hard, _ = kw_score(1.0, 0.0, None, None, loss=1.0, lam=0.3)
        assert easy == pytest.approx(0.3) and hard == pytest.approx(1.0)


class TestRefindability:
    def test_unseen_plain_page_is_hard_to_find(self):
        assert refindability(earlier_visits=0, app="Zed", domain=None, title="scratch") == 0.0

    def test_familiarity_makes_it_easier(self):
        assert refindability(earlier_visits=4, app="Zed", domain=None, title="x") > refindability(earlier_visits=1, app="Zed", domain=None, title="x")

    def test_own_file_is_findable_through_the_file_system(self):
        assert refindability(earlier_visits=0, app="Code", domain=None, title="notes.md - Code") == pytest.approx(0.8)

    def test_stable_page_vs_ephemeral_source(self):
        stable = refindability(earlier_visits=0, app="msedge", domain="docs.python.org", title="Docs")
        feed = refindability(earlier_visits=2, app="msedge", domain="facebook.com", title="Home")
        assert stable == pytest.approx(0.5)
        assert feed == pytest.approx((1 - math.exp(-1)) * 0.5)  # familiar, then halved for being ephemeral

    def test_search_results_and_chat_are_ephemeral(self):
        plain = refindability(earlier_visits=2, app="msedge", domain="google.com", title="python - Google Search")
        assert plain < refindability(earlier_visits=2, app="msedge", domain="google.com", title="Python docs")
        assert refindability(earlier_visits=2, app="Slack", domain=None, title="general") == pytest.approx((1 - math.exp(-1)) * 0.5)


# ---------------------------------------------------------------------------------- the signals

class TestNeedFromReturns:
    def labelled(self, later_hours, **kw):
        records = [rec(1, at(0), title="doc")] + [rec(10 + i, at(h), title="doc") for i, h in enumerate(later_hours)]
        # separate the visits: another context in between each return
        records += [rec(100 + i, at(h - 0.1), title=f"x{i}", app="Notepad") for i, h in enumerate(later_hours)]
        _, results = construct(padded(records), **kw)
        return results[1]

    def test_sooner_returns_count_more(self):
        soon, late = self.labelled([1]), self.labelled([40])
        assert soon.need_from_returns == pytest.approx(math.exp(-1 / 24), abs=1e-4)
        assert late.need_from_returns == pytest.approx(math.exp(-40 / 24), abs=1e-4)
        assert soon.need_from_returns > late.need_from_returns

    def test_more_returns_count_more_and_are_capped_at_one(self):
        assert self.labelled([1, 2]).need_from_returns > self.labelled([1]).need_from_returns
        assert self.labelled([1, 2, 3, 4, 5, 6]).need_from_returns == 1.0

    def test_no_return_is_zero_need(self):
        result = self.labelled([])
        assert result.need_from_returns == 0.0 and result.label == 0 and not result.revisited

    def test_a_return_outside_the_horizon_does_not_count(self):
        assert self.labelled([50]).need_from_returns == 0.0

    def test_consecutive_captures_in_one_visit_are_one_visit(self):
        records = [rec(1, at(0)), rec(2, at(0, 1)), rec(3, at(0, 2)), rec(4, at(5), title="other", app="Notepad")]
        _, results = construct(padded(records))
        assert results[1].need_from_returns == 0.0 and not results[1].revisited


class TestAttentionToAction:
    def result(self, typed_after, dwell=30.0, usual=None):
        records = [rec(1, at(0), dwell=dwell), rec(2, at(0, 2), title="other", app="Notepad", keys=typed_after)]
        profile = None if usual is None else UserProfile(n_records=1, typing_baseline=[usual] * 24)
        _, results = construct(padded(records), profile=profile)
        return results[1]

    def test_short_visits_get_no_credit(self):
        assert self.result(500, dwell=5).action == 0.0

    def test_typing_above_usual_counts_and_saturates(self):
        small, big = self.result(60).action, self.result(600).action
        assert 0 < small < big < 1

    def test_typing_at_or_below_the_users_usual_counts_for_nothing(self):
        # For a heavy typist whose usual is 200 keys, typing 100 is simply normal; 600 is not.
        assert self.result(100, usual=200).action == 0.0
        assert self.result(600, usual=200).action > 0.0
        assert self.result(600, usual=0).action > self.result(600, usual=200).action

    def test_typing_in_the_same_context_is_not_counted(self):
        records = [rec(1, at(0), dwell=30), rec(2, at(0, 2), keys=500)]  # still the same context
        _, results = construct(padded(records))
        assert results[1].action == 0.0


class TestCombination:
    def test_returning_to_a_hard_to_find_page_beats_returning_to_an_easy_file(self):
        records = [
            rec(1, at(0), title="a.py"), rec(2, at(1), title="x", app="Notepad"), rec(3, at(2), title="a.py"),
            rec(4, at(3), title="ephemeral", app="Slack"), rec(5, at(4), title="y", app="Notepad"), rec(6, at(5), title="ephemeral", app="Slack"),
        ]
        _, results = construct(padded(records))
        assert results[4].kw > results[1].kw  # same need, but the chat message is harder to find again
        assert results[4].loss > results[1].loss

    def test_result_is_a_label_result_with_components(self):
        records = [rec(1, at(0)), rec(2, at(1), title="x", app="Notepad"), rec(3, at(2))]
        _, results = construct(padded(records))
        r = results[1]
        assert isinstance(r, KWResult) and isinstance(r, LabelResult)
        assert r.kw == r.score and r.reuse is None and not r.retrieval_available
        assert r.label == int(r.kw >= r.threshold)

    def test_retrieval_feedback_counts_as_evidence(self):
        records = [rec(1, at(0)), rec(2, at(1), title="x", app="Notepad")]
        base = construct(padded(records))[1][1]
        boosted = construct(padded(records), retrieval_fn=lambda r: True)[1][1]
        assert boosted.kw > base.kw and boosted.retrieval_available


class TestConstructor:
    def test_label_version_is_stamped_apart_from_the_default_rule(self):
        assert KWLabelConstructor().label_version == f"kw{KW_RULE_VERSION}/h48"
        assert KWLabelConstructor(label_horizon=timedelta(hours=1)).label_version == "kw1/h1"
        assert KWLabelConstructor().label_version != LabelConstructor().label_version

    def test_censored_records_are_not_labelled(self):
        records = [rec(1, at(0)), rec(2, at(10), title="x", app="Notepad")]  # data ends 10 h after record 1
        constructor = KWLabelConstructor(label_delay=timedelta(0), label_horizon=H)
        assert constructor.construct_batch(records, now=at(100)) == []

    def test_one_hour_horizon_for_quick_testing(self):
        records = [rec(1, at(0)), rec(2, at(0, 20), title="x", app="Notepad"), rec(3, at(0, 40)), rec(4, at(3), title="end", app="Notepad")]
        constructor = KWLabelConstructor(label_delay=timedelta(hours=1), label_horizon=timedelta(hours=1))
        results = dict(constructor.construct_batch(records, now=at(5)))
        assert results[1].revisited and constructor.label_version == "kw1/h1"

    def test_a_fixed_profile_is_used_as_given(self):
        profile = UserProfile.fit([rec(i, at(0, i), title=f"t{i}", keys=0) for i in range(5)])
        constructor = KWLabelConstructor(profile=profile, label_delay=timedelta(0), label_horizon=H)
        constructor.construct_batch([rec(1, at(0))], now=at(100))
        assert constructor.profile is profile

    def test_label_for_needs_a_batch_first(self):
        with pytest.raises(RuntimeError):
            KWLabelConstructor().label_for(rec(1, at(0)), None)  # type: ignore[arg-type]

    def test_stored_labels_carry_the_kw_version_and_are_never_rewritten(self):
        from storage.context_store import ContextRecord, insert_context_record, list_context_records
        from storage.database import connect, migrate

        conn = connect(":memory:")
        migrate(conn)
        spec = [(at(0), "a.py", "Code"), (at(1), "x", "Notepad"), (at(2), "a.py", "Code"), (at(70), "end", "Notepad")]
        for ts, title, app in spec:
            insert_context_record(conn, ContextRecord(ts=ts, decision="save", features={"beh_key_presses": 0}, app=app, title=title))
        constructor = KWLabelConstructor(label_delay=timedelta(0), label_horizon=H)
        first = label_pending_records(conn, constructor, now=at(200))
        stored = list_context_records(conn)
        assert {r.label_version for r in stored if r.label is not None} == {"kw1/h48"}
        assert label_pending_records(conn, constructor, now=at(300)) == []  # immutable once written
        assert len(first) == len([r for r in stored if r.label is not None])
        conn.close()


# ---------------------------------------------------------------------------------- the profile

class TestActivityIndex:
    def test_typing_in_other_contexts_only(self):
        records = [rec(1, at(0), title="a"), rec(2, at(0, 2), title="b", keys=40), rec(3, at(0, 4), title="a", keys=99), rec(4, at(0, 5), title="c", keys=10)]
        index = ActivityIndex(records)
        assert index.keys_after(at(0), index.keys[0]) == 50  # b and c, not a's own 99
        assert index.keys_after(at(0, 3), index.keys[0]) == 10  # window starts after b

    def test_visits_and_earlier_visits(self):
        records = [rec(1, at(0), title="a"), rec(2, at(0, 1), title="a"), rec(3, at(0, 5), title="b"), rec(4, at(0, 9), title="a")]
        index = ActivityIndex(records)
        assert len(index.visits) == 3
        assert index.earlier_visits(0) == 0 and index.earlier_visits(3) == 1


class TestUserProfile:
    def day(self, start_hour, keys, count=12, base_id=0):
        return [rec(base_id + i, at(start_hour, i * 3), title=f"t{base_id + i}", app="Code", keys=keys) for i in range(count)]

    def test_hourly_typing_baseline_reflects_when_the_user_types(self):
        morning = self.day(0, keys=30)            # 09:xx
        night = self.day(12, keys=0, base_id=100)  # 21:xx
        profile = UserProfile.fit(morning + night)
        assert profile.typing_usual(9) > profile.typing_usual(21)
        assert profile.snapshot_keys[9] > profile.snapshot_keys[21]

    def test_unseen_hours_shrink_to_the_global_value(self):
        profile = UserProfile.fit(self.day(0, keys=30))
        assert profile.typing_usual(3) == pytest.approx(profile.typing_global)

    def test_return_behaviour_per_app(self):
        records = []
        for d in range(6):  # Code: always comes back; Notepad: never
            base = d * 1.0
            records += [rec(d * 10, at(base), title="proj", app="Code"), rec(d * 10 + 1, at(base, 5), title="pad", app="Notepad")]
        records += [rec(500 + d, at(d * 1.0 + 0.5), title="proj", app="Code") for d in range(6)]
        records.append(rec(999, at(120), title="end", app="Zed"))
        profile = UserProfile.fit(records)
        assert profile.app_return_rate["code"] > profile.app_return_rate["notepad"]
        assert 0 < profile.return_hours_global <= 48

    def test_transitions_and_routine(self):
        records, i = [], 0
        for _ in range(6):
            for app in ("Code", "msedge"):
                records.append(rec(i, at(0, i), title=f"{app}{i}", app=app)); i += 1
        profile = UserProfile.fit(records)
        assert profile.transitions["code"]["msedge"] > profile.transition_floor
        assert profile.hour_share[9] > profile.hour_share[3]
        assert sum(profile.hour_share) == pytest.approx(1.0)

    def test_features_fall_back_when_nothing_is_known(self):
        empty = UserProfile.fit([])
        features = empty.features("Code", 9, 5.0)
        assert set(features) == set(USER_FEATURE_COLUMNS)
        assert all(math.isfinite(v) for v in features.values())

    def test_unusual_typing_for_this_user_stands_out(self):
        profile = UserProfile.fit(self.day(0, keys=10))
        quiet = profile.features("Code", 9, 10.0)["usr_keys_vs_usual"]
        burst = profile.features("Code", 9, 100.0)["usr_keys_vs_usual"]
        assert burst > 5 * quiet / 2 and quiet == pytest.approx(10 / 11, rel=0.2)

    def test_user_features_use_the_previous_app_for_transitions(self):
        profile = UserProfile.fit([rec(i, at(0, i), title=f"t{i}", app=("Code", "msedge")[i % 2]) for i in range(12)])
        rows = [{"app": "Code", "features": {"tmp_hour_of_day": 9, "beh_key_presses": 0}},
                {"app": "msedge", "features": {"tmp_hour_of_day": 9, "beh_key_presses": 0}}]
        first, second = user_features(profile, rows)
        assert first["usr_transition_prob"] == profile.transition_floor
        assert second["usr_transition_prob"] > profile.transition_floor

    def test_save_load_roundtrip_and_append_only(self, tmp_path):
        profile = UserProfile.fit(self.day(0, keys=30))
        path = save_profile(profile, tmp_path / "v1")
        assert load_profile(tmp_path / "v1").to_dict() == profile.to_dict()
        with pytest.raises(FileExistsError):
            save_profile(profile, tmp_path / "v1")
        assert path.name == "user_profile.json"

    def test_unknown_schema_is_rejected(self):
        with pytest.raises(ValueError):
            UserProfile.from_dict({"schema": 99})


class TestOfflineWiring:
    def test_with_user_features_adds_the_columns_without_touching_the_rest(self):
        import pandas as pd

        from offline.training import with_user_features

        rows = [{"id": i, "ts": at(0, i), "app": ("Code", "msedge")[i % 2], "features": {"tmp_hour_of_day": 9, "beh_key_presses": 1.0, "x": 7}} for i in range(10)]
        df = pd.DataFrame(rows).sample(frac=1, random_state=1)  # shuffled input must not matter
        out = with_user_features(df, UserProfile.fit([rec(i, at(0, i), title=f"t{i}") for i in range(10)]))
        assert all(set(USER_FEATURE_COLUMNS) <= set(f) and f["x"] == 7 for f in out["features"])
        assert list(out.index) == list(df.index)

    def test_add_labels_accepts_the_kw_constructor(self):
        import pandas as pd

        from offline.training import add_labels

        records = [rec(1, at(0)), rec(2, at(1), title="x", app="Notepad"), rec(3, at(2)), rec(4, at(80), title="end", app="Notepad")]
        df = pd.DataFrame([{"id": r.id, "ts": r.ts, "app": r.app, "domain": r.domain, "title": r.title, "features": r.features} for r in records])
        out = add_labels(df, constructor=KWLabelConstructor(label_delay=timedelta(0), label_horizon=H))
        assert out.loc[out["id"] == 1, "label_version"].iloc[0] == "kw1/h48"
        assert out.loc[out["id"] == 1, "label"].iloc[0] == 1
