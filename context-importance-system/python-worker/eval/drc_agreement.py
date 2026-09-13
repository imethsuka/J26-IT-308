"""How faithfully does :class:`models.drc_rules.DrcRules` reproduce DRC's real capture decisions?

Replays DRC's recorded window/idle events through the frozen rules and compares the captures the rules
*would* make with the screenshots DRC *actually* took. Run it once while DRC's ``collector.db`` still
exists and report the numbers; afterwards the baseline needs no DRC data.

Only events are replayed. Screenshots are used as the answer key, never as an input, and the DRC
database is opened read-only.

Known limits, reported rather than hidden:
- ``scroll_activity`` needs wheel-notch timing; DRC stores counts per minute, so scroll captures are
  not predicted and show up as missed.
- The browser domain comes from DRC's own browser events; if DRC logged none for a context, the address
  counts as unreadable (DRC fails closed for screenshots).

    python -m eval.drc_agreement --drc D:\\DesktopResearchCollector\\collector.db
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from models import drc_rules as R
from offline.drc_replay import _browser_events, _parse_ts, open_read_only

MATCH_TOLERANCE = timedelta(seconds=3)
IDLE_THRESHOLD_SECONDS = 300  # DRC IdleThresholdSeconds
_MAX_PERIODIC_STEPS = 10_000  # guard against a policy that can never make progress


@dataclass
class Capture:
    ts: datetime
    trigger: str


@dataclass
class Agreement:
    actual: list[Capture] = field(default_factory=list)
    predicted: list[Capture] = field(default_factory=list)
    matched: list[tuple[Capture, Capture]] = field(default_factory=list)  # (actual, predicted)
    missed: list[Capture] = field(default_factory=list)  # actual, not predicted
    extra: list[Capture] = field(default_factory=list)  # predicted, not actual

    @property
    def precision(self) -> float:
        return len(self.matched) / len(self.predicted) if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return len(self.matched) / len(self.actual) if self.actual else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def per_reason(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = defaultdict(lambda: {"actual": 0, "matched": 0, "predicted": 0, "extra": 0})
        for c in self.actual:
            out[c.trigger]["actual"] += 1
        for a, _ in self.matched:
            out[a.trigger]["matched"] += 1
        for c in self.predicted:
            out[c.trigger]["predicted"] += 1
        for c in self.extra:
            out[c.trigger]["extra"] += 1
        return dict(out)

    def summary(self) -> str:
        reason_ok = sum(1 for a, p in self.matched if a.trigger == p.trigger)
        lines = [
            f"DRC screenshots (answer key): {len(self.actual)}",
            f"rules would capture:          {len(self.predicted)}",
            f"matched (within {MATCH_TOLERANCE.total_seconds():.0f} s):       {len(self.matched)}",
            f"precision {self.precision:.3f}   recall {self.recall:.3f}   F1 {self.f1:.3f}",
            f"trigger reason agrees on {reason_ok}/{len(self.matched)} matched captures",
            "",
            f"{'trigger':<22}{'actual':>8}{'matched':>9}{'recall':>8}{'predicted':>11}{'extra':>7}",
        ]
        for trigger, row in sorted(self.per_reason().items()):
            recall = row["matched"] / row["actual"] if row["actual"] else 0.0
            lines.append(f"{trigger:<22}{row['actual']:>8}{row['matched']:>9}{recall:>8.2f}{row['predicted']:>11}{row['extra']:>7}")
        return "\n".join(lines)


def load_policy(drc_db: Path | str) -> R.DrcPolicy:
    """DRC's newest recorded policy as a :class:`DrcPolicy`, to check the frozen defaults against it."""
    import json

    conn = open_read_only(drc_db)
    try:
        row = conn.execute("SELECT settings_json FROM collection_policies ORDER BY effective_from DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("no collection policy recorded in the DRC database")
    data = json.loads(row["settings_json"])
    s = data["screenshot_settings"]
    return R.DrcPolicy(
        capture_on_app_change=s["capture_on_app_change"],
        capture_on_window_change=s["capture_on_window_change"],
        capture_after_idle=s["capture_after_idle"],
        capture_on_scroll=s["capture_on_scroll"],
        periodic_interval_seconds=s["periodic_interval_seconds"],
        cooldown_seconds=s["cooldown_seconds"],
        scroll_cooldown_seconds=s["scroll_cooldown_seconds"],
        scroll_capture_threshold=s["scroll_capture_threshold"],
        max_per_day=s["max_per_day"],
        excluded_domains=tuple(data["excluded_domains"]),
        excluded_apps=tuple(data["excluded_screenshot_apps"]),
    )


def simulate(drc_db: Path | str, policy: R.DrcPolicy | None = None) -> list[Capture]:
    """Captures the rules would make over DRC's recorded events (time order, all sessions)."""
    conn = open_read_only(drc_db)
    try:
        browser = _browser_events(conn)
        inputs: dict[str, list[datetime]] = defaultdict(list)
        for r in conn.execute("SELECT session_id, timestamp FROM interaction_events ORDER BY timestamp"):
            inputs[r["session_id"]].append(_parse_ts(r["timestamp"]))
        rows = conn.execute(
            "SELECT session_id, timestamp, event_type, process_name, application_name, window_title "
            "FROM activity_events WHERE event_type IN "
            "('application_switch','window_title_change','idle_start','idle_end') ORDER BY timestamp"
        ).fetchall()
    finally:
        conn.close()

    by_session: dict[str, list] = defaultdict(list)
    for row in rows:
        by_session[row["session_id"]].append(row)

    out: list[Capture] = []
    for session, events in by_session.items():
        out.extend(_simulate_session(session, events, browser, inputs.get(session, []), R.DrcRules(policy)))
    out.sort(key=lambda c: c.ts)
    return out


def _simulate_session(session, events, browser, input_times: list[datetime], rules: R.DrcRules) -> list[Capture]:
    captures: list[Capture] = []
    ctx: dict | None = None
    idle = False

    def decide(trigger: str, ts: datetime) -> R.DrcDecision:
        domain = browser.domain_at(session, ts, since=ctx["since"]) if ctx else None
        decision = rules.decide(
            trigger, ts, app=ctx["process"], title=ctx["title"], domain=domain,
            application_name=ctx["application"], address_known=domain is not None,
        )
        if decision.capture:
            captures.append(Capture(ts, trigger))
        return decision

    def run_periodic(cursor: datetime, until: datetime) -> None:
        if ctx is None or idle:
            return
        steps = 0
        while steps < _MAX_PERIODIC_STEPS:
            steps += 1
            t = max(rules.periodic_due_at(cursor), cursor)
            if t >= until:
                return
            decision = decide(R.PERIODIC_CAPTURE, t)
            if decision.capture:
                cursor = t
            elif decision.blocked_by in ("daily_quota", "trigger_disabled"):
                return
            elif decision.blocked_by == "cooldown":
                cursor = t + timedelta(seconds=1)
            else:  # privacy: DRC retries after its retry delay, which rules._next_periodic_attempt holds
                cursor = t

    cursor: datetime | None = None
    for event in events:
        ts = _parse_ts(event["timestamp"])
        kind = event["event_type"]
        if cursor is not None:
            until = ts
            if kind in ("idle_start", "idle_end") and not idle:
                # If DRC was suspended (sleep) it logs the idle change only on waking. The user was
                # active until one idle threshold after the last recorded input (flushes lag input by
                # up to a minute, so an ordinary idle_start still resolves to ``ts``).
                i = bisect_right(input_times, ts)
                last_input = max(input_times[i - 1], cursor) if i else cursor
                until = min(ts, last_input + timedelta(seconds=IDLE_THRESHOLD_SECONDS))
            run_periodic(cursor, until)
        cursor = ts
        if kind == "idle_start":
            idle = True
        elif kind == "idle_end":
            idle = False
            if ctx is not None:
                decide(R.IDLE_RESUME, ts)
        else:
            process, title = event["process_name"], event["window_title"]
            changed_app = ctx is None or R._normalize_app(ctx["process"]) != R._normalize_app(process)
            ctx = {
                "process": process, "application": event["application_name"], "title": title,
                "since": ts if ctx is None or changed_app or (ctx["title"] or "") != (title or "") else ctx["since"],
            }
            decide(R.APPLICATION_CHANGE if kind == "application_switch" else R.WINDOW_TITLE_CHANGE, ts)
    return captures


def compare(actual: list[Capture], predicted: list[Capture]) -> Agreement:
    """Greedy one-to-one match by time (both lists time-sorted), within :data:`MATCH_TOLERANCE`."""
    result = Agreement(actual=list(actual), predicted=list(predicted))
    j = 0
    used = [False] * len(predicted)
    for a in actual:
        while j < len(predicted) and predicted[j].ts < a.ts - MATCH_TOLERANCE:
            j += 1
        best = None
        k = j
        while k < len(predicted) and predicted[k].ts <= a.ts + MATCH_TOLERANCE:
            if not used[k] and (best is None or abs(predicted[k].ts - a.ts) < abs(predicted[best].ts - a.ts)):
                best = k
            k += 1
        if best is None:
            result.missed.append(a)
        else:
            used[best] = True
            result.matched.append((a, predicted[best]))
    result.extra = [p for p, u in zip(predicted, used) if not u]
    return result


def actual_captures(drc_db: Path | str) -> list[Capture]:
    conn = open_read_only(drc_db)
    try:
        rows = conn.execute("SELECT timestamp, capture_reason FROM screenshots ORDER BY timestamp").fetchall()
    finally:
        conn.close()
    return [Capture(_parse_ts(r["timestamp"]), r["capture_reason"]) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--drc", required=True, help="path to DRC's collector.db (opened read-only)")
    args = parser.parse_args()

    recorded = load_policy(args.drc)
    frozen = R.DrcPolicy()
    print("frozen policy == DRC's newest recorded policy:", recorded == frozen)
    agreement = compare(actual_captures(args.drc), simulate(args.drc, frozen))
    print(agreement.summary())
    extra = Counter(c.trigger for c in agreement.extra)
    missed = Counter(c.trigger for c in agreement.missed)
    print("\nrules captured but DRC did not:", dict(extra))
    print("DRC captured but rules did not:", dict(missed))


if __name__ == "__main__":
    main()
