"""Tests for the frozen DRC capture rules, the live trigger observer, and agreement scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eval.drc_agreement import Capture, compare
from models import drc_rules as R
from models.drc_rules import DrcPolicy, DrcRules, LiveDrcObserver
from storage.context_store import ContextRecord, get_context_record, insert_context_record
from storage.database import connect, migrate

T0 = datetime(2026, 10, 10, 9, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def decide(rules, trigger, seconds, **kw):
    kw.setdefault("app", "Code")
    return rules.decide(trigger, at(seconds), **kw)


class TestGates:
    def test_first_app_change_captures(self):
        assert decide(DrcRules(), R.APPLICATION_CHANGE, 0).capture

    def test_cooldown_blocks_then_releases(self):
        rules = DrcRules()
        assert decide(rules, R.APPLICATION_CHANGE, 0).capture
        refused = decide(rules, R.WINDOW_TITLE_CHANGE, 14)
        assert not refused.capture and refused.blocked_by == "cooldown"
        assert decide(rules, R.WINDOW_TITLE_CHANGE, 15).capture

    def test_refused_capture_does_not_restart_cooldown(self):
        rules = DrcRules()
        decide(rules, R.APPLICATION_CHANGE, 0)
        decide(rules, R.APPLICATION_CHANGE, 10)  # refused
        assert decide(rules, R.APPLICATION_CHANGE, 15).capture  # still 15 s after the capture at 0

    def test_scroll_has_its_own_clock(self):
        rules = DrcRules()
        assert decide(rules, R.APPLICATION_CHANGE, 0).capture
        assert decide(rules, R.SCROLL_ACTIVITY, 1).capture  # not blocked by the app-change cooldown
        assert not decide(rules, R.SCROLL_ACTIVITY, 10).capture
        assert decide(rules, R.SCROLL_ACTIVITY, 21).capture

    def test_daily_quota_resets_next_day(self):
        rules = DrcRules(DrcPolicy(max_per_day=2, cooldown_seconds=0))
        assert decide(rules, R.APPLICATION_CHANGE, 0).capture
        assert decide(rules, R.APPLICATION_CHANGE, 1).capture
        assert decide(rules, R.APPLICATION_CHANGE, 2).blocked_by == "daily_quota"
        assert decide(rules, R.APPLICATION_CHANGE, 86400).capture

    def test_disabled_trigger(self):
        rules = DrcRules(DrcPolicy(capture_after_idle=False))
        assert decide(rules, R.IDLE_RESUME, 0).blocked_by == "trigger_disabled"

    def test_unknown_trigger_is_an_error(self):
        with pytest.raises(ValueError):
            decide(DrcRules(), "manual_capture", 0)


class TestPeriodic:
    def test_due_ninety_seconds_after_last_capture(self):
        rules = DrcRules()
        decide(rules, R.APPLICATION_CHANGE, 0)
        assert rules.periodic_due_at(at(5)) == at(90)

    def test_privacy_refusal_delays_the_retry_by_five_seconds(self):
        rules = DrcRules()
        decide(rules, R.APPLICATION_CHANGE, 0)
        refused = decide(rules, R.PERIODIC_CAPTURE, 90, app="Signal")
        assert refused.blocked_by == "excluded_app"
        assert rules.periodic_due_at(at(90)) == at(95)


class TestPrivacy:
    @pytest.mark.parametrize("title", ["My Password Vault", "News - InPrivate", "x (Incognito)", "Private Browsing"])
    def test_sensitive_titles_block(self, title):
        assert decide(DrcRules(), R.APPLICATION_CHANGE, 0, title=title).blocked_by == "sensitive_title"

    @pytest.mark.parametrize("app", ["Signal", "signal.exe", "KeePassXC", "Floorp", "Microsoft Teams"])
    def test_excluded_apps_block(self, app):
        assert decide(DrcRules(), R.APPLICATION_CHANGE, 0, app=app).blocked_by == "excluded_app"

    def test_application_name_also_matches(self):
        refused = decide(DrcRules(), R.APPLICATION_CHANGE, 0, app="ms-teams", application_name="Microsoft Teams")
        assert refused.blocked_by == "excluded_app"

    @pytest.mark.parametrize("domain", ["www.chase.com", "login.chase.com", "my.bank", "x.y.banking", "mail.yahoo.com"])
    def test_excluded_domains_block_in_browsers(self, domain):
        assert decide(DrcRules(), R.APPLICATION_CHANGE, 0, app="msedge", domain=domain).blocked_by == "excluded_domain"

    def test_other_domain_allowed(self):
        assert decide(DrcRules(), R.APPLICATION_CHANGE, 0, app="chrome", domain="github.com").capture

    def test_unknown_browser_address_fails_closed(self):
        assert decide(DrcRules(), R.APPLICATION_CHANGE, 0, app="chrome", domain=None).blocked_by == "browser_address_unknown"

    def test_non_browser_without_domain_is_fine(self):
        assert decide(DrcRules(), R.APPLICATION_CHANGE, 0, app="Code", domain=None).capture

    def test_privacy_refusal_does_not_consume_cooldown_or_quota(self):
        rules = DrcRules(DrcPolicy(max_per_day=1))
        assert not decide(rules, R.APPLICATION_CHANGE, 0, app="Signal").capture
        assert decide(rules, R.APPLICATION_CHANGE, 1).capture


class TestLiveObserver:
    def observe(self, obs, seconds, app="Code", title="a.py", domain=None, idle=False):
        return obs.observe(at(seconds), app=app, title=title, domain=domain, is_idle=idle)

    def test_first_snapshot_is_an_app_change(self):
        decision = self.observe(LiveDrcObserver(), 0)
        assert decision.capture and decision.trigger == R.APPLICATION_CHANGE

    def test_unchanged_context_has_no_trigger(self):
        obs = LiveDrcObserver()
        self.observe(obs, 0)
        decision = self.observe(obs, 5)
        assert not decision.capture and decision.blocked_by == "no_trigger"

    def test_title_and_app_changes(self):
        obs = LiveDrcObserver()
        self.observe(obs, 0)
        assert self.observe(obs, 20, title="b.py").trigger == R.WINDOW_TITLE_CHANGE
        assert self.observe(obs, 40, app="Notepad", title="b.py").trigger == R.APPLICATION_CHANGE

    def test_periodic_after_ninety_seconds(self):
        obs = LiveDrcObserver()
        self.observe(obs, 0)
        assert not self.observe(obs, 89).capture
        decision = self.observe(obs, 91)
        assert decision.capture and decision.trigger == R.PERIODIC_CAPTURE

    def test_idle_then_resume(self):
        obs = LiveDrcObserver()
        self.observe(obs, 0)
        idle = self.observe(obs, 400, idle=True)
        assert not idle.capture and idle.blocked_by == "idle"
        resumed = self.observe(obs, 500)
        assert resumed.capture and resumed.trigger == R.IDLE_RESUME

    def test_excluded_app_not_captured(self):
        assert not self.observe(LiveDrcObserver(), 0, app="Signal").capture


class TestAgreement:
    def test_greedy_matching_within_tolerance(self):
        actual = [Capture(at(0), "periodic_capture"), Capture(at(100), "application_change"), Capture(at(200), "scroll_activity")]
        predicted = [Capture(at(1), "periodic_capture"), Capture(at(104), "application_change"), Capture(at(300), "periodic_capture")]
        result = compare(actual, predicted)
        assert len(result.matched) == 1
        assert [c.ts for c in result.missed] == [at(100), at(200)]
        assert [c.ts for c in result.extra] == [at(104), at(300)]
        assert result.precision == pytest.approx(1 / 3) and result.recall == pytest.approx(1 / 3)

    def test_one_prediction_matches_one_capture(self):
        actual = [Capture(at(0), "a"), Capture(at(1), "b")]
        result = compare(actual, [Capture(at(0.5), "a")])
        assert len(result.matched) == 1 and len(result.missed) == 1


class TestStorage:
    def test_record_roundtrips_the_decision(self):
        conn = connect(":memory:")
        migrate(conn)
        rid = insert_context_record(
            conn, ContextRecord(ts=T0, decision="save", features={}, drc_would_capture=True, drc_trigger=R.APPLICATION_CHANGE)
        )
        rid2 = insert_context_record(conn, ContextRecord(ts=T0, decision="save", features={}))
        got = get_context_record(conn, rid)
        assert got.drc_would_capture is True and got.drc_trigger == R.APPLICATION_CHANGE
        none = get_context_record(conn, rid2)
        assert none.drc_would_capture is None and none.drc_trigger is None
        conn.close()
