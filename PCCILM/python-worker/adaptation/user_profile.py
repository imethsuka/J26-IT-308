"""Model 2's picture of the user: how this person works, learned from their own captures.

Model 2 (the scheduled learning loop) used to know one fact per record, "did the context come back".
This module learns *habits* instead, from the stored records only (no labels needed):

``typing_baseline[h]``     usual key presses in the 10 minutes after a moment at hour ``h``, counting only
                           typing in *other* contexts (what "worked on something else" normally looks like)
``snapshot_keys[h]``       usual key presses in a snapshot's activity window at hour ``h``
``app_return_rate[app]``   how often a visit to this app is followed by a return to the same context
                           within the horizon (shrunk towards the global rate for rarely used apps)
``app_return_hours[app]``  how long that return usually takes
``transitions[a][b]``      P(next app is ``b`` | current app is ``a``), within a session
``hour_share[h]``          how much of the user's activity falls in hour ``h`` (their routine)

Two consumers:

* :mod:`adaptation.kw_labels` uses the typing baseline to decide what counts as "unusual typing".
* :func:`user_features` turns the profile into ``usr_*`` features for Model 1: "this moment is unusual for
  *you*". They are opt-in extra columns (``USER_FEATURE_COLUMNS``); the pinned ``FEATURE_COLUMNS`` layout is untouched.

Every statistic is shrunk towards the global value with ``SHRINKAGE`` pseudo-observations, so a sparse
hour or a rarely used app is not over-read. For offline experiments fit the profile on the training
split only, otherwise the held-out period leaks into the features.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .label_construction import (
    DEFAULT_LABEL_HORIZON,
    DEFAULT_SESSION_GAP,
    TitleNormalizer,
    context_key,
)

PROFILE_FILE = "user_profile.json"
PROFILE_SCHEMA = 1
SHRINKAGE = 5.0
DEFAULT_WINDOW = timedelta(minutes=10)

USER_FEATURE_COLUMNS: tuple[str, ...] = (
    "usr_keys_vs_usual",
    "usr_app_return_rate",
    "usr_app_return_hours",
    "usr_hour_share",
    "usr_transition_prob",
)


# =============================================================================================
# Activity index: visits and typing windows over a set of records
# =============================================================================================

@dataclass
class Visit:
    key: tuple[str, str, str]
    session: int
    first_ts: datetime
    last_ts: datetime
    active_dwell: float  # seconds: the larger of last-first and the largest dwell a snapshot reported


class ActivityIndex:
    """Sessions, visits and windowed key-press sums over records, using the same visit rule as
    :class:`adaptation.label_construction.Timeline` (a visit is a run of the same context in one session)."""

    def __init__(
        self,
        records: Iterable[Any],
        *,
        session_gap: timedelta = DEFAULT_SESSION_GAP,
        title_normalizer: TitleNormalizer | None = None,
    ) -> None:
        ordered = sorted(records, key=lambda r: _utc(r.ts))
        self.records = ordered
        self.ts = [_utc(r.ts) for r in ordered]
        self.keys = [context_key(r, title_normalizer) for r in ordered]
        self.presses = [_feature(r, "beh_key_presses") for r in ordered]
        self.hours = [int(_feature(r, "tmp_hour_of_day", default=t.hour)) % 24 for r, t in zip(ordered, self.ts)]
        self.index_of_id = {r.id: i for i, r in enumerate(ordered)}

        self.visit_of: list[int] = []
        self.visits: list[Visit] = []
        self.visits_by_key: dict[tuple[str, str, str], list[int]] = {}
        session, previous_ts, previous_key = 0, None, None
        for i, record in enumerate(ordered):
            ts, key = self.ts[i], self.keys[i]
            new_session = previous_ts is not None and (ts - previous_ts) > session_gap
            if new_session:
                session += 1
            dwell = _feature(record, "tmp_dwell_seconds")
            if new_session or key != previous_key or not self.visits:
                self.visits.append(Visit(key, session, ts, ts, dwell))
                self.visits_by_key.setdefault(key, []).append(len(self.visits) - 1)
            else:
                visit = self.visits[-1]
                visit.last_ts = ts
                visit.active_dwell = max(visit.active_dwell, dwell, (ts - visit.first_ts).total_seconds())
            self.visit_of.append(len(self.visits) - 1)
            previous_ts, previous_key = ts, key

        self._all = _prefix(self.presses)
        self._by_key: dict[tuple[str, str, str], tuple[list[datetime], list[float]]] = {}
        grouped: dict[tuple[str, str, str], tuple[list[datetime], list[float]]] = {}
        for ts, key, presses in zip(self.ts, self.keys, self.presses):
            times, values = grouped.setdefault(key, ([], []))
            times.append(ts)
            values.append(presses)
        for key, (times, values) in grouped.items():
            self._by_key[key] = (times, _prefix(values))

    @property
    def newest_ts(self) -> datetime | None:
        return self.ts[-1] if self.ts else None

    def keys_after(self, start: datetime, key: tuple[str, str, str], window: timedelta = DEFAULT_WINDOW) -> float:
        """Key presses in ``(start, start + window]`` in contexts other than ``key``."""
        end = start + window
        total = self._all[bisect_right(self.ts, end)] - self._all[bisect_right(self.ts, start)]
        times, prefix = self._by_key.get(key, ([], [0.0]))
        same = prefix[bisect_right(times, end)] - prefix[bisect_right(times, start)]
        return max(0.0, total - same)

    def later_visit_delays(self, i: int, horizon: timedelta) -> list[float]:
        """Hours until each *later visit* of record ``i``'s context within ``(ts, ts + horizon]``."""
        ts, own = self.ts[i], self.visit_of[i]
        cutoff = ts + horizon
        delays = []
        for v in self.visits_by_key.get(self.keys[i], ()):
            visit = self.visits[v]
            if v != own and ts < visit.first_ts <= cutoff:
                delays.append((visit.first_ts - ts).total_seconds() / 3600.0)
        return delays

    def earlier_visits(self, i: int) -> int:
        """How many visits to record ``i``'s context came before its own visit."""
        own = self.visit_of[i]
        return sum(1 for v in self.visits_by_key.get(self.keys[i], ()) if v < own)


# =============================================================================================
# The profile
# =============================================================================================

@dataclass
class UserProfile:
    n_records: int = 0
    typing_baseline: list[float] = field(default_factory=lambda: [0.0] * 24)
    typing_global: float = 0.0
    snapshot_keys: list[float] = field(default_factory=lambda: [0.0] * 24)
    snapshot_keys_global: float = 0.0
    app_return_rate: dict[str, float] = field(default_factory=dict)
    app_return_hours: dict[str, float] = field(default_factory=dict)
    return_rate_global: float = 0.0
    return_hours_global: float = 0.0
    transitions: dict[str, dict[str, float]] = field(default_factory=dict)
    transition_floor: float = 0.0
    hour_share: list[float] = field(default_factory=lambda: [1 / 24] * 24)
    horizon_hours: float = 48.0
    window_minutes: float = 10.0
    schema: int = PROFILE_SCHEMA

    # ---- learning -------------------------------------------------------------------------
    @classmethod
    def fit(
        cls,
        records: Iterable[Any],
        *,
        horizon: timedelta = DEFAULT_LABEL_HORIZON,
        window: timedelta = DEFAULT_WINDOW,
        session_gap: timedelta = DEFAULT_SESSION_GAP,
        title_normalizer: TitleNormalizer | None = None,
        index: ActivityIndex | None = None,
    ) -> "UserProfile":
        index = index or ActivityIndex(records, session_gap=session_gap, title_normalizer=title_normalizer)
        n = len(index.records)
        profile = cls(n_records=n, horizon_hours=horizon.total_seconds() / 3600, window_minutes=window.total_seconds() / 60)
        if n == 0:
            return profile

        # Typing: usual 10-minute typing elsewhere, and usual per-snapshot typing, by hour of day.
        after = [index.keys_after(index.ts[i], index.keys[i], window) for i in range(n)]
        profile.typing_global = sum(after) / n
        profile.typing_baseline = _shrunk_by_hour(index.hours, after, profile.typing_global)
        profile.snapshot_keys_global = sum(index.presses) / n
        profile.snapshot_keys = _shrunk_by_hour(index.hours, index.presses, profile.snapshot_keys_global)

        # Routine: share of activity per hour (add-one smoothed).
        counts = [0] * 24
        for h in index.hours:
            counts[h] += 1
        profile.hour_share = [(c + 1) / (n + 24) for c in counts]

        # Return behaviour per app, over visits whose whole horizon has been observed.
        newest = index.newest_ts
        returns: dict[str, list[tuple[bool, float]]] = {}
        for v, visit in enumerate(index.visits):
            if newest is None or visit.first_ts + horizon > newest:
                continue
            first_record = _first_record_of_visit(index, v)
            delays = index.later_visit_delays(first_record, horizon)
            returns.setdefault(_app_name(visit.key[0]), []).append((bool(delays), min(delays) if delays else 0.0))
        all_outcomes = [o for outcomes in returns.values() for o in outcomes]
        if all_outcomes:
            profile.return_rate_global = sum(1 for r, _ in all_outcomes if r) / len(all_outcomes)
            returned = [d for r, d in all_outcomes if r]
            profile.return_hours_global = sum(returned) / len(returned) if returned else profile.horizon_hours
            for app, outcomes in returns.items():
                hits = [d for r, d in outcomes if r]
                profile.app_return_rate[app] = (len(hits) + SHRINKAGE * profile.return_rate_global) / (len(outcomes) + SHRINKAGE)
                profile.app_return_hours[app] = (sum(hits) + SHRINKAGE * profile.return_hours_global) / (len(hits) + SHRINKAGE)
        else:
            profile.return_hours_global = profile.horizon_hours

        # Transitions between apps (consecutive visits in one session), add-one smoothed.
        pair_counts: dict[str, dict[str, int]] = {}
        apps = {_app_name(visit.key[0]) for visit in index.visits}
        for a, b in zip(index.visits, index.visits[1:]):
            na, nb = _app_name(a.key[0]), _app_name(b.key[0])
            if a.session == b.session and na != nb:
                pair_counts.setdefault(na, {})
                pair_counts[na][nb] = pair_counts[na].get(nb, 0) + 1
        k = max(1, len(apps))
        profile.transition_floor = 1.0 / k
        for a, nexts in pair_counts.items():
            total = sum(nexts.values()) + k
            profile.transitions[a] = {b: (c + 1) / total for b, c in nexts.items()}
            profile.transitions[a]["__floor__"] = 1.0 / total
        return profile

    # ---- using it -------------------------------------------------------------------------
    def typing_usual(self, hour: int) -> float:
        return self.typing_baseline[hour % 24] if self.n_records else 0.0

    def features(self, app: str | None, hour: int, key_presses: float, previous_app: str | None = None) -> dict[str, float]:
        """``usr_*`` features for one snapshot. Missing knowledge falls back to the global statistic."""
        name = _app_name(app)
        usual_keys = self.snapshot_keys[hour % 24] if self.n_records else 0.0
        if previous_app is None or self.n_records == 0:
            transition = self.transition_floor
        else:
            row = self.transitions.get(_app_name(previous_app))
            if name == _app_name(previous_app):
                transition = 1.0  # staying in the same app is not a transition
            elif row is None:
                transition = self.transition_floor
            else:
                transition = row.get(name, row.get("__floor__", self.transition_floor))
        return {
            "usr_keys_vs_usual": float(key_presses) / (usual_keys + 1.0),
            "usr_app_return_rate": self.app_return_rate.get(name, self.return_rate_global),
            "usr_app_return_hours": self.app_return_hours.get(name, self.return_hours_global),
            "usr_hour_share": self.hour_share[hour % 24],
            "usr_transition_prob": transition,
        }

    # ---- persistence ----------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UserProfile":
        if data.get("schema") != PROFILE_SCHEMA:
            raise ValueError(f"unsupported user-profile schema {data.get('schema')!r}")
        return cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})


def save_profile(profile: UserProfile, version_dir: Path | str) -> Path:
    """Write ``user_profile.json`` into a model version folder. Versions are append-only: never overwrites."""
    path = Path(version_dir) / PROFILE_FILE
    if path.exists():
        raise FileExistsError(f"{path} already exists; model-version files are never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
    return path


def load_profile(version_dir: Path | str) -> UserProfile:
    return UserProfile.from_dict(json.loads((Path(version_dir) / PROFILE_FILE).read_text(encoding="utf-8")))


def user_features(profile: UserProfile, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, float]]:
    """``usr_*`` features for time-ordered rows, each a mapping with ``app``, ``features`` (and ``ts``).

    The previous row's app supplies the transition probability, so rows must be in time order.
    """
    out: list[dict[str, float]] = []
    previous: str | None = None
    for row in rows:
        features = row["features"]
        hour = int(features.get("tmp_hour_of_day", 0) or 0)
        out.append(profile.features(row["app"], hour, float(features.get("beh_key_presses", 0) or 0), previous))
        previous = row["app"]
    return out


# =============================================================================================
# helpers
# =============================================================================================

def _shrunk_by_hour(hours: list[int], values: list[float], global_mean: float) -> list[float]:
    sums, counts = [0.0] * 24, [0] * 24
    for h, v in zip(hours, values):
        sums[h] += v
        counts[h] += 1
    return [(sums[h] + SHRINKAGE * global_mean) / (counts[h] + SHRINKAGE) for h in range(24)]


def _first_record_of_visit(index: ActivityIndex, visit: int) -> int:
    # Visits are contiguous runs of records, so the first record of visit v follows the last of v-1.
    lo, hi = 0, len(index.visit_of) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if index.visit_of[mid] < visit:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _prefix(values: list[float]) -> list[float]:
    out = [0.0]
    for v in values:
        out.append(out[-1] + v)
    return out


def _feature(record: Any, name: str, default: float = 0.0) -> float:
    features = getattr(record, "features", None) or {}
    value = features.get(name, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _app_name(app: str | None) -> str:
    name = (app or "").strip().lower()
    return name[:-4] if name.endswith(".exe") else name


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
