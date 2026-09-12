"""Replay a DesktopResearchCollector (DRC) ``collector.db`` as the snapshots the live system would see.

DRC is a separate, rule-based data-gathering tool. It saves a screenshot on every app switch, title
change, scroll burst, and every 90 s, and logs window/browser/input events alongside. The live
collector sends one snapshot per captured frame, so the replay produces **one capture per DRC
screenshot**, describing the context as it stood at that moment:

- **context**: the latest ``application_switch`` / ``window_title_change`` event at or before the
  screenshot in the same session (99.7% agree with the screenshot's own app name; the screenshot
  follows its trigger event by ~0.07 s). ``app`` is the process name (``msedge``, ``Code``), which is
  what the live collector reports and what the app-category map expects.
- **dwell so far**: time since the context (process + title) last changed, i.e. what the live
  collector reports at capture time. DRC's ``duration_ms`` is *not* used: it is the time spent in the
  *previous* window, and it is only known after leaving, which the live system never sees.
- **input counts**: DRC writes interaction counts about once a minute, the live collector counts a
  trailing 10 s window. Counts in the trailing minute are scaled to the live window so a model
  trained here is served compatible inputs. Mouse moves are not recorded by DRC (always 0).
- **domain**: the latest browser event in the same session since the context started.
- **idle**: inside a DRC ``idle_start``/``idle_end`` interval.

Privacy (this project's rules, applied on top of DRC's own exclusions):

- The source DB is opened **read-only**; DRC usually runs live and is never written to.
- ``csharp-collector``'s ``collector.config.json`` exclusions are applied before anything else, and
  fail closed: a capture with no identifiable context, or a browser capture whose domain is unknown
  while domain rules exist, is dropped (``fail_closed_unknown_domain``).
- The lock screen (``LockApp``) is time away from the desktop, not a context, so it is dropped.

Dropped captures are counted per reason in :class:`ReplayStats`, so a notebook can report them.
"""

from __future__ import annotations

import json
import re
import sqlite3
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from features.feature_assembly import SensingEvent

CONTEXT_EVENT_TYPES = ("application_switch", "window_title_change")
INTERACTION_KEYS = {
    "keyboard_activity_count": "key_presses",
    "mouse_click": "mouse_clicks",
    "mouse_scroll": "mouse_scrolls",
}
# Processes that are "away from the desktop", not a context.
AWAY_PROCESSES = frozenset({"lockapp"})
# The live collector's trailing activity window (collector.config.json activityWindowSeconds).
LIVE_ACTIVITY_WINDOW_SECONDS = 10
# DRC flushes interaction counts about once a minute (measured median gap 60 s).
DRC_INTERACTION_WINDOW = timedelta(seconds=60)
# A context event logged just after its screenshot still counts as that screenshot's context.
CONTEXT_MATCH_TOLERANCE = timedelta(seconds=1)
BROWSER_MATCH_TOLERANCE = timedelta(seconds=3)

DEFAULT_COLLECTOR_CONFIG = (
    Path(__file__).resolve().parents[2] / "csharp-collector" / "src" / "CsharpCollector" / "collector.config.json"
)


# =============================================================================================
# Exclusions (mirror of csharp-collector Filtering/ExclusionFilter.cs)
# =============================================================================================

@dataclass(frozen=True)
class Exclusions:
    apps: frozenset[str] = frozenset()
    domains: tuple[str, ...] = ()
    browsers: frozenset[str] = frozenset({"chrome", "msedge"})

    @classmethod
    def from_collector_config(cls, path: Path | str = DEFAULT_COLLECTOR_CONFIG) -> "Exclusions":
        """Read the collector's JSON-with-comments config. Missing file -> error (never run unfiltered)."""
        text = Path(path).read_text(encoding="utf-8")
        config = json.loads(_strip_json_comments(text))
        return cls(
            apps=frozenset(normalize_app(a) for a in config.get("excludedApps", [])),
            domains=tuple(d.strip().lower() for d in config.get("excludedDomains", []) if d.strip()),
            browsers=frozenset(normalize_app(b) for b in config.get("browserProcesses", ["chrome", "msedge"])),
        )

    def app_excluded(self, app: str | None) -> bool:
        return normalize_app(app) in self.apps

    def domain_excluded(self, domain: str | None) -> bool:
        if not domain:
            return False
        host = domain.strip().lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    def is_browser(self, app: str | None) -> bool:
        return normalize_app(app) in self.browsers


def normalize_app(app: str | None) -> str:
    """``C:\\...\\Code.exe`` / ``code.EXE`` / ``Code`` -> ``code``."""
    name = (app or "").strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _strip_json_comments(text: str) -> str:
    # Drops // line comments outside strings (the config has no /* */ blocks).
    return re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*', lambda m: m.group(1) or "", text)


# =============================================================================================
# Replay
# =============================================================================================

@dataclass
class DrcCapture:
    """One replayed snapshot: a DRC screenshot plus the context the live system would have sent."""

    screenshot_id: str
    image_path: Path
    file_size: int
    ts: datetime
    session_id: str
    capture_reason: str
    event: SensingEvent

    @property
    def app(self) -> str | None:
        return self.event.app

    @property
    def title(self) -> str | None:
        return self.event.title

    @property
    def domain(self) -> str | None:
        return self.event.domain


@dataclass
class ReplayStats:
    screenshots: int = 0
    kept: int = 0
    dropped: Counter = field(default_factory=Counter)
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    def summary(self) -> str:
        lines = [f"DRC screenshots: {self.screenshots}", f"kept as captures: {self.kept}"]
        for reason, n in self.dropped.most_common():
            lines.append(f"dropped ({reason}): {n}")
        if self.first_ts and self.last_ts:
            lines.append(f"span: {self.first_ts:%Y-%m-%d %H:%M} -> {self.last_ts:%Y-%m-%d %H:%M} UTC")
        return "\n".join(lines)


def open_read_only(source_db: Path | str) -> sqlite3.Connection:
    """Open a SQLite file strictly read-only (safe while DRC is writing to it)."""
    path = Path(source_db).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"DRC database not found: {path}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def snapshot_db(source_db: Path | str, target_db: Path | str) -> Path:
    """Consistent single-file copy of a (possibly live, WAL-mode) DRC database, for uploading.

    Uses SQLite's backup API through a read-only connection, so DRC can keep running and nothing in
    the source is changed. Refuses to overwrite an existing target.
    """
    target = Path(target_db)
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    source = open_read_only(source_db)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    return target


def replay_drc(
    source_db: Path | str,
    *,
    exclusions: Exclusions | None = None,
    screenshots_dir: Path | str | None = None,
    fail_closed_unknown_domain: bool = True,
    require_image_file: bool = True,
) -> tuple[list[DrcCapture], ReplayStats]:
    """Replay every DRC screenshot as a capture. Returns ``(captures in time order, stats)``.

    ``screenshots_dir`` overrides where images are looked up (by file name), for when the DRC folder
    was moved or copied; by default each row's ``local_file_path`` is used.
    """
    exclusions = exclusions if exclusions is not None else Exclusions.from_collector_config()
    conn = open_read_only(source_db)
    try:
        return _replay(conn, exclusions, screenshots_dir, fail_closed_unknown_domain, require_image_file)
    finally:
        conn.close()


def _replay(conn, exclusions, screenshots_dir, fail_closed_unknown_domain, require_image_file):
    contexts = _context_events(conn)
    idle = _idle_intervals(conn)
    browser = _browser_events(conn)
    interactions = _interaction_series(conn)

    stats = ReplayStats()
    captures: list[DrcCapture] = []
    rows = conn.execute(
        "SELECT screenshot_id, session_id, timestamp, capture_reason, local_file_path, file_size "
        "FROM screenshots ORDER BY timestamp"
    ).fetchall()
    for row in rows:
        stats.screenshots += 1
        ts = _parse_ts(row["timestamp"])
        session = row["session_id"]

        context = contexts.at(session, ts)
        if context is None:
            stats.dropped["no context event (fail closed)"] += 1
            continue
        app = context.process
        if not app:
            stats.dropped["unidentified process (fail closed)"] += 1
            continue
        if normalize_app(app) in AWAY_PROCESSES:
            stats.dropped["lock screen (away)"] += 1
            continue
        if exclusions.app_excluded(app):
            stats.dropped["excluded app"] += 1
            continue

        domain = None
        if exclusions.is_browser(app):
            domain = browser.domain_at(session, ts, since=context.started_at)
            if domain is None and fail_closed_unknown_domain and exclusions.domains:
                stats.dropped["browser domain unknown (fail closed)"] += 1
                continue
            if exclusions.domain_excluded(domain):
                stats.dropped["excluded domain"] += 1
                continue

        image_path = Path(row["local_file_path"])
        if screenshots_dir is not None:
            image_path = Path(screenshots_dir) / Path(row["local_file_path"].replace("\\", "/")).name
        if require_image_file and not image_path.is_file():
            stats.dropped["image file missing"] += 1
            continue

        counts = interactions.trailing(session, ts)
        event = SensingEvent(
            ts=ts,
            app=app,
            title=context.title,
            domain=domain,
            excluded=False,
            dwell_seconds=max(0.0, (ts - context.started_at).total_seconds()),
            key_presses=counts["key_presses"],
            mouse_clicks=counts["mouse_clicks"],
            mouse_scrolls=counts["mouse_scrolls"],
            mouse_moves=0,
            window_seconds=LIVE_ACTIVITY_WINDOW_SECONDS,
            is_idle=idle.contains(session, ts),
            idle_seconds=0.0,
        )
        captures.append(DrcCapture(
            screenshot_id=row["screenshot_id"],
            image_path=image_path,
            file_size=int(row["file_size"] or 0),
            ts=ts,
            session_id=session,
            capture_reason=row["capture_reason"],
            event=event,
        ))
        stats.kept += 1
        stats.first_ts = stats.first_ts or ts
        stats.last_ts = ts
    return captures, stats


# ---------------------------------------------------------------------------------- indexes

@dataclass
class _Context:
    ts: datetime
    process: str | None
    title: str | None
    started_at: datetime  # when this process+title became current (dwell starts here)


class _ContextIndex:
    def __init__(self, by_session: dict[str, list[_Context]]):
        self._by_session = by_session
        self._ts = {s: [c.ts for c in cs] for s, cs in by_session.items()}

    def at(self, session: str, ts: datetime) -> _Context | None:
        times = self._ts.get(session)
        if not times:
            return None
        i = bisect_right(times, ts + CONTEXT_MATCH_TOLERANCE) - 1
        return self._by_session[session][i] if i >= 0 else None


def _context_events(conn) -> _ContextIndex:
    by_session: dict[str, list[_Context]] = defaultdict(list)
    placeholders = ",".join("?" for _ in CONTEXT_EVENT_TYPES)
    rows = conn.execute(
        f"SELECT session_id, timestamp, process_name, window_title FROM activity_events "
        f"WHERE event_type IN ({placeholders}) ORDER BY timestamp",
        CONTEXT_EVENT_TYPES,
    )
    for row in rows:
        ts = _parse_ts(row["timestamp"])
        session = by_session[row["session_id"]]
        process, title = row["process_name"], row["window_title"]
        previous = session[-1] if session else None
        # Same process + title again (e.g. a repeated title event): the context did not change, so
        # dwell keeps running from the original start, as in the live collector.
        same = previous is not None and normalize_app(previous.process) == normalize_app(process) and (previous.title or "") == (title or "")
        session.append(_Context(ts=ts, process=process, title=title, started_at=previous.started_at if same else ts))
    return _ContextIndex(dict(by_session))


class _IdleIndex:
    def __init__(self, by_session: dict[str, list[tuple[datetime, datetime]]]):
        self._by_session = by_session

    def contains(self, session: str, ts: datetime) -> bool:
        return any(start <= ts <= end for start, end in self._by_session.get(session, ()))


def _idle_intervals(conn) -> _IdleIndex:
    by_session: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    pending: dict[str, datetime] = {}
    rows = conn.execute(
        "SELECT session_id, timestamp, event_type FROM activity_events "
        "WHERE event_type IN ('idle_start', 'idle_end') ORDER BY timestamp"
    )
    for row in rows:
        ts = _parse_ts(row["timestamp"])
        if row["event_type"] == "idle_start":
            pending[row["session_id"]] = ts
        elif row["session_id"] in pending:
            by_session[row["session_id"]].append((pending.pop(row["session_id"]), ts))
    far_future = datetime.max.replace(tzinfo=timezone.utc)
    for session, start in pending.items():  # idle when the session ended
        by_session[session].append((start, far_future))
    return _IdleIndex(dict(by_session))


class _BrowserIndex:
    def __init__(self, by_session: dict[str, list[tuple[datetime, str]]]):
        self._by_session = by_session
        self._ts = {s: [t for t, _ in rows] for s, rows in by_session.items()}

    def domain_at(self, session: str, ts: datetime, *, since: datetime) -> str | None:
        times = self._ts.get(session)
        if not times:
            return None
        i = bisect_right(times, ts + BROWSER_MATCH_TOLERANCE) - 1
        if i < 0 or times[i] < since - BROWSER_MATCH_TOLERANCE:
            return None
        return self._by_session[session][i][1]


def _browser_events(conn) -> _BrowserIndex:
    by_session: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    rows = conn.execute(
        "SELECT session_id, timestamp, domain FROM browser_events WHERE domain IS NOT NULL ORDER BY timestamp"
    )
    for row in rows:
        by_session[row["session_id"]].append((_parse_ts(row["timestamp"]), row["domain"]))
    return _BrowserIndex(dict(by_session))


class _InteractionIndex:
    """Per session and count type: flush timestamps with prefix sums, for O(log n) window sums."""

    def __init__(self, series: dict[tuple[str, str], tuple[list[datetime], list[int]]]):
        self._series = series

    def trailing(self, session: str, ts: datetime) -> dict[str, int]:
        scale = LIVE_ACTIVITY_WINDOW_SECONDS / DRC_INTERACTION_WINDOW.total_seconds()
        out = {}
        for key in INTERACTION_KEYS.values():
            times, prefix = self._series.get((session, key), ([], [0]))
            lo = bisect_right(times, ts - DRC_INTERACTION_WINDOW)
            hi = bisect_right(times, ts)
            out[key] = int(round((prefix[hi] - prefix[lo]) * scale))
        return out


def _interaction_series(conn) -> _InteractionIndex:
    raw: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    rows = conn.execute(
        "SELECT session_id, timestamp, interaction_type, count FROM interaction_events ORDER BY timestamp"
    )
    for row in rows:
        key = INTERACTION_KEYS.get(row["interaction_type"])
        if key:
            raw[(row["session_id"], key)].append((_parse_ts(row["timestamp"]), int(row["count"] or 0)))
    series = {}
    for k, items in raw.items():
        times = [t for t, _ in items]
        prefix = [0]
        for _, n in items:
            prefix.append(prefix[-1] + n)
        series[k] = (times, prefix)
    return _InteractionIndex(series)


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


__all__ = ["DrcCapture", "Exclusions", "ReplayStats", "normalize_app", "open_read_only", "replay_drc", "snapshot_db"]
