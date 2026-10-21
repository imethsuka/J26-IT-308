"""Tests for the DRC replay (offline.drc_replay) against a small synthetic DRC database.

Covers the facts the replay depends on: context = the latest window event at/before the screenshot,
dwell counted from the last real context change (not DRC's backward-looking ``duration_ms``),
per-minute interaction counts scaled to the live 10 s window, browser domain matching, idle
intervals, and every drop reason (lock screen, excluded app/domain, unknown domain, missing image).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from offline.drc_replay import Exclusions, normalize_app, open_read_only, replay_drc

T0 = datetime(2026, 10, 8, 9, 0, 0, tzinfo=timezone.utc)
SESSION = "s1"

SCHEMA = """
CREATE TABLE activity_events (event_id TEXT PRIMARY KEY, session_id TEXT, timestamp TEXT, event_type TEXT,
    application_name TEXT, process_name TEXT, window_title TEXT, duration_ms INTEGER);
CREATE TABLE browser_events (browser_event_id TEXT PRIMARY KEY, session_id TEXT, timestamp TEXT, domain TEXT);
CREATE TABLE interaction_events (interaction_id TEXT PRIMARY KEY, session_id TEXT, timestamp TEXT,
    interaction_type TEXT, count INTEGER);
CREATE TABLE screenshots (screenshot_id TEXT PRIMARY KEY, session_id TEXT, timestamp TEXT, capture_reason TEXT,
    application_name TEXT, window_title TEXT, local_file_path TEXT, file_size INTEGER);
"""

EXCLUSIONS = Exclusions(
    apps=frozenset({"1password"}),
    domains=("accounts.google.com",),
    browsers=frozenset({"msedge", "chrome"}),
)


def iso(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


class DrcBuilder:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.db = tmp_path / "collector.db"
        self.conn = sqlite3.connect(self.db)
        self.conn.executescript(SCHEMA)
        self.n = 0

    def _id(self) -> str:
        self.n += 1
        return f"id{self.n}"

    def window(self, at: float, process: str, title: str, event_type: str = "application_switch", duration_ms: int = 999_999):
        self.conn.execute(
            "INSERT INTO activity_events VALUES (?,?,?,?,?,?,?,?)",
            (self._id(), SESSION, iso(at), event_type, process.title(), process, title, duration_ms),
        )

    def idle(self, start: float, end: float):
        self.conn.execute("INSERT INTO activity_events VALUES (?,?,?,?,?,?,?,?)", (self._id(), SESSION, iso(start), "idle_start", None, None, None, 0))
        self.conn.execute("INSERT INTO activity_events VALUES (?,?,?,?,?,?,?,?)", (self._id(), SESSION, iso(end), "idle_end", None, None, None, 0))

    def browser(self, at: float, domain: str):
        self.conn.execute("INSERT INTO browser_events VALUES (?,?,?,?)", (self._id(), SESSION, iso(at), domain))

    def interaction(self, at: float, kind: str, count: int):
        self.conn.execute("INSERT INTO interaction_events VALUES (?,?,?,?,?)", (self._id(), SESSION, iso(at), kind, count))

    def screenshot(self, at: float, *, write_file: bool = True, size: int = 1000) -> str:
        sid = self._id()
        path = self.tmp / f"{sid}.jpg"
        if write_file:
            path.write_bytes(b"jpg")
        self.conn.execute(
            "INSERT INTO screenshots VALUES (?,?,?,?,?,?,?,?)",
            (sid, SESSION, iso(at), "application_change", "x", "x", str(path), size),
        )
        return sid

    def replay(self, **kw):
        self.conn.commit()
        return replay_drc(self.db, exclusions=kw.pop("exclusions", EXCLUSIONS), **kw)


@pytest.fixture
def drc(tmp_path):
    builder = DrcBuilder(tmp_path)
    yield builder
    builder.conn.close()


def test_context_is_latest_window_event_with_process_name(drc):
    drc.window(0, "Code", "main.py")
    drc.window(100, "Sublime_Text", "notes.txt")
    first = drc.screenshot(50)
    second = drc.screenshot(100.1)

    captures, stats = drc.replay()

    by_id = {c.screenshot_id: c for c in captures}
    assert by_id[first].app == "Code" and by_id[first].title == "main.py"
    assert by_id[second].app == "Sublime_Text"
    assert stats.kept == 2


def test_event_logged_just_after_its_screenshot_still_matches(drc):
    drc.window(0, "Code", "a.py")
    sid = drc.screenshot(10)
    drc.window(10.5, "Sublime_Text", "b.txt")  # trigger event committed 0.5 s after the screenshot

    captures, _ = drc.replay()

    assert captures[0].screenshot_id == sid and captures[0].app == "Sublime_Text"


def test_dwell_is_time_since_last_real_change_not_duration_ms(drc):
    drc.window(0, "Code", "a.py", duration_ms=5)
    drc.window(30, "Code", "a.py", event_type="window_title_change", duration_ms=5)  # same title again
    drc.screenshot(90)

    captures, _ = drc.replay()

    assert captures[0].event.dwell_seconds == pytest.approx(90.0)


def test_title_change_resets_dwell(drc):
    drc.window(0, "Code", "a.py")
    drc.window(60, "Code", "b.py", event_type="window_title_change")
    drc.screenshot(70)

    captures, _ = drc.replay()

    assert captures[0].event.dwell_seconds == pytest.approx(10.0)


def test_interaction_counts_trailing_minute_scaled_to_live_window(drc):
    drc.window(0, "Code", "a.py")
    drc.interaction(30, "keyboard_activity_count", 60)    # inside (T-60, T]
    drc.interaction(80, "mouse_click", 12)                # inside
    drc.interaction(5, "mouse_scroll", 600)               # older than 60 s -> excluded
    drc.interaction(100, "keyboard_activity_count", 999)  # after the screenshot -> excluded
    drc.screenshot(85)

    event = drc.replay()[0][0].event

    assert event.key_presses == 10      # 60 per minute -> 10 per 10 s
    assert event.mouse_clicks == 2
    assert event.mouse_scrolls == 0
    assert event.window_seconds == 10 and event.mouse_moves == 0


def test_browser_domain_matched_within_current_context(drc):
    drc.window(0, "msedge", "Docs")
    drc.browser(1, "docs.example.com")
    drc.screenshot(20)

    captures, _ = drc.replay()

    assert captures[0].domain == "docs.example.com"


def test_browser_event_from_before_the_context_is_not_used(drc):
    drc.window(0, "msedge", "Old")
    drc.browser(1, "old.example.com")
    drc.window(100, "msedge", "New", event_type="window_title_change")
    drc.screenshot(110)

    captures, stats = drc.replay()

    assert captures == []
    assert stats.dropped["browser domain unknown (fail closed)"] == 1


def test_unknown_browser_domain_can_be_kept_when_not_failing_closed(drc):
    drc.window(0, "msedge", "Page")
    drc.screenshot(5)

    captures, _ = drc.replay(fail_closed_unknown_domain=False)

    assert len(captures) == 1 and captures[0].domain is None


def test_drop_reasons(drc):
    drc.window(0, "LockApp", "Lock")
    drc.screenshot(1)
    drc.window(10, "1Password", "Vault")
    drc.screenshot(11)
    drc.window(20, "chrome", "Sign in")
    drc.browser(20, "accounts.google.com")
    drc.screenshot(21)
    drc.window(30, "Code", "a.py")
    drc.screenshot(31, write_file=False)

    captures, stats = drc.replay()

    assert captures == []
    assert stats.dropped == {
        "lock screen (away)": 1,
        "excluded app": 1,
        "excluded domain": 1,
        "image file missing": 1,
    }


def test_screenshot_before_any_window_event_fails_closed(drc):
    drc.screenshot(1)
    drc.window(5, "Code", "a.py")

    captures, stats = drc.replay()

    assert captures == [] and stats.dropped["no context event (fail closed)"] == 1


def test_idle_interval(drc):
    drc.window(0, "Code", "a.py")
    drc.idle(10, 100)
    drc.screenshot(50)
    drc.screenshot(150)

    captures, _ = drc.replay()

    assert [c.event.is_idle for c in captures] == [True, False]


def test_screenshots_dir_override(drc, tmp_path):
    moved = tmp_path / "moved"
    moved.mkdir()
    drc.window(0, "Code", "a.py")
    sid = drc.screenshot(1, write_file=False)
    (moved / f"{sid}.jpg").write_bytes(b"jpg")

    captures, _ = drc.replay(screenshots_dir=moved)

    assert captures[0].image_path == moved / f"{sid}.jpg"


def test_source_is_opened_read_only(drc):
    drc.conn.commit()
    conn = open_read_only(drc.db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM screenshots")
    finally:
        conn.close()


def test_snapshot_db_copies_without_touching_source(drc, tmp_path):
    from offline.drc_replay import snapshot_db

    drc.window(0, "Code", "a.py")
    drc.screenshot(1)
    drc.conn.commit()
    before = drc.db.read_bytes()

    copy = snapshot_db(drc.db, tmp_path / "out" / "snap.db")

    assert drc.db.read_bytes() == before
    captures, _ = replay_drc(copy, exclusions=EXCLUSIONS)
    assert len(captures) == 1
    with pytest.raises(FileExistsError):
        snapshot_db(drc.db, copy)


def test_exclusions_from_collector_config_with_comments(tmp_path):
    config = tmp_path / "collector.config.json"
    config.write_text(
        '{\n  // apps\n  "excludedApps": ["KeePass.exe", "1Password"],\n'
        '  "excludedDomains": ["bank.com"], // trailing\n  "browserProcesses": ["msedge"],\n'
        '  "note": "http://not-a-comment"\n}',
        encoding="utf-8",
    )
    ex = Exclusions.from_collector_config(config)

    assert ex.app_excluded("C:\\Apps\\keepass.EXE") and ex.app_excluded("1password")
    assert ex.domain_excluded("www.bank.com") and not ex.domain_excluded("notbank.com")
    assert ex.is_browser("msedge.exe") and not ex.is_browser("chrome")


def test_project_collector_config_loads():
    ex = Exclusions.from_collector_config()
    assert ex.app_excluded("1Password") and ex.is_browser("msedge")


@pytest.mark.parametrize("raw,expected", [("Code", "code"), ("C:/x/Code.exe", "code"), (None, ""), ("msedge.EXE", "msedge")])
def test_normalize_app(raw, expected):
    assert normalize_app(raw) == expected
