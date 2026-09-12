"""Delayed weak-supervision label construction.

Importance is not known at capture time, so labels are built *after* a delay (``label_delay``,
default 48h), once later activity can confirm or deny that a context mattered. For each context
record that is old enough and still unlabelled, three signals are computed:

1. **Revisit** — did the user leave the same doc/page/task (app + domain + title) and come back to
   it *later*? Repeated captures within one uninterrupted visit do not count: the collector sends a
   snapshot on every screen change, so one stretch in a window produces many same-context records.
2. **Continuation** — did that task resume in a *later session* (after an inactivity gap), not just
   moments afterward within the same session? A later session is always a new visit, so every
   continuation is also a revisit.
3. **Retrieval / use** — was the context actually retrieved/used downstream? This is **Member 3's**
   component and is NOT implemented here: it is an injected callback (or mock) returning
   ``True/False``, a score in ``[0, 1]``, or ``None`` when no feedback is available.

The three are merged by :func:`combine_importance_label` — a single, isolated, documented function,
so the labelling rule can be cited and defended in the report independently of the plumbing.

**Bounded horizon.** Revisit and continuation look forward only within ``label_horizon`` (``H``,
default 48h): strictly the window ``(ts, ts + H]``. An unbounded lookahead would make a label a
function of *all* data collected so far, so the same record could be labelled 0 today and 1 next
month — labels would silently rewrite themselves and any evaluation built on them would be
irreproducible. A closed window makes the label a function of a fixed slice of time.

**Censoring.** A bounded window is only usable once it is fully observed. If ``ts + H`` is later
than the newest ingested record, the window is still filling, so the record is left unlabelled
(right-censored) rather than labelled from partial evidence. This is why the delay alone is not
enough: the wall clock can be well past ``ts + H`` while the data has a gap (machine off, collector
stopped), and labelling then would read "no revisit" from missing data rather than from absence.

Together these give determinism: once a record is labelled, re-running over a superset of the data
yields the same label, because every input to the label lies in a closed, fully-observed window.
``label_version`` stamps each label with the rule version and horizon that produced it, so labels
built under a different rule are identifiable and can be rebuilt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Protocol

DEFAULT_LABEL_DELAY = timedelta(hours=48)
DEFAULT_LABEL_HORIZON = timedelta(hours=48)
DEFAULT_SESSION_GAP = timedelta(minutes=30)

# Bumped when the labelling *rule* changes in a way that would produce different labels from the
# same data. Stored per row (with the horizon) as ``label_version``.
#   v2: bounded horizon + censoring.
#   v3: a revisit must come from a different visit (consecutive same-context captures are one visit).
LABEL_RULE_VERSION = 3


class Recordable(Protocol):
    """Minimum a record needs for label construction (satisfied by storage.ContextRecord)."""

    id: Any
    ts: datetime
    app: str | None
    domain: str | None
    title: str | None


# A retrieval oracle: given a record, return whether/how much it was used downstream, or None.
RetrievalFn = Callable[[Any], "bool | float | None"]


# =============================================================================================
# Combination rule — the citable core. Keep this function isolated and its logic self-contained.
# =============================================================================================

@dataclass(frozen=True)
class LabelWeights:
    """Evidence weights, in descending trust. Retrieval is direct evidence of use; revisit and
    continuation are behavioural proxies (continuation the weaker, being only a temporal pattern)."""

    retrieval: float = 0.5
    revisit: float = 0.3
    continuation: float = 0.2


DEFAULT_WEIGHTS = LabelWeights()


@dataclass
class LabelResult:
    label: int
    score: float
    revisited: bool
    continued: bool
    retrieval: bool | float | None
    retrieval_available: bool
    threshold: float
    weights: LabelWeights = field(default_factory=LabelWeights)


def combine_importance_label(
    revisited: bool,
    continued: bool,
    retrieval: bool | float | None,
    *,
    weights: LabelWeights = DEFAULT_WEIGHTS,
    threshold: float = 0.5,
) -> LabelResult:
    """
    Combine the three importance signals into a score in [0, 1] and a binary label.

    Rule (documented for methodological defence):

        Each *available* signal contributes its weight to the denominator; a signal that *fired*
        contributes its weight (times its strength, for a float retrieval score) to the numerator.

            score = (w_r·retrieval_strength + w_v·revisited + w_c·continued) / (w_r? + w_v + w_c)
            label = 1 if score >= threshold else 0

    Retrieval is the only signal that can be *missing* (``None``): revisit and continuation are
    always computable from the activity timeline (absence of later activity = ``False``, not
    missing). When retrieval is missing, ``w_r`` drops out of BOTH numerator and denominator, so the
    label is formed from the behavioural proxies alone and missing feedback is neither rewarded nor
    penalised. With default weights (0.5 / 0.3 / 0.2) and threshold 0.5:

        * retrieval alone (no revisit/continuation)      -> score 0.50 -> important
        * revisit + continuation (retrieval False/missing) -> score >= 0.50 -> important
        * revisit alone                                  -> score 0.30 or 0.60 -> depends on feedback
        * nothing fired                                  -> 0.0 -> not important

    Continuation never fires without revisit (a return in a later session is a return), so in data
    built by :class:`Timeline` the reachable cases are the ones above. Consequence, by design: while
    retrieval feedback is missing, revisit alone scores 0.60 and the label equals the revisit flag
    ("came back within the horizon"); continuation only raises the score from 0.60 to 1.00. Once
    retrieval is available, revisit alone (0.30) no longer suffices and continuation or use is needed.
    The function itself still accepts continuation without revisit (score 0.2 / 0.4 -> 0).
    """
    retrieval_available = retrieval is not None
    retrieval_strength = _retrieval_strength(retrieval)

    numerator = weights.revisit * float(revisited) + weights.continuation * float(continued)
    denominator = weights.revisit + weights.continuation
    if retrieval_available:
        numerator += weights.retrieval * retrieval_strength
        denominator += weights.retrieval

    score = numerator / denominator if denominator > 0 else 0.0
    return LabelResult(
        label=1 if score >= threshold else 0,
        score=round(score, 6),
        revisited=bool(revisited),
        continued=bool(continued),
        retrieval=retrieval,
        retrieval_available=retrieval_available,
        threshold=threshold,
        weights=weights,
    )


def _retrieval_strength(retrieval: bool | float | None) -> float:
    if retrieval is None:
        return 0.0
    if isinstance(retrieval, bool):
        return 1.0 if retrieval else 0.0
    return float(min(1.0, max(0.0, retrieval)))


# =============================================================================================
# Signals derived from the activity timeline
# =============================================================================================

def context_key(record: Recordable) -> tuple[str, str, str]:
    """Identity used to match a context across time: normalized (app, domain, title)."""
    return (
        (record.app or "").strip().lower(),
        (record.domain or "").strip().lower(),
        (record.title or "").strip().lower(),
    )


@dataclass
class _Occurrence:
    ts: datetime
    session: int
    visit: int
    record_id: Any


class Timeline:
    """Sessionized index of records for computing revisit/continuation efficiently.

    ``horizon`` bounds how far forward every signal may look. Session numbering runs oldest-first
    over the whole ordered set, so appending newer records never renumbers older ones and the
    *relative* session comparison inside a record's horizon is stable across runs.

    A *visit* is a maximal run of consecutive records (in time order, across all contexts) with the
    same context key inside one session. Like sessions, a record's visit depends only on the records
    before it, so visits are stable as newer data arrives.
    """

    def __init__(
        self,
        records: Iterable[Recordable],
        *,
        session_gap: timedelta = DEFAULT_SESSION_GAP,
        horizon: timedelta = DEFAULT_LABEL_HORIZON,
    ):
        ordered = sorted(records, key=lambda r: _as_utc(r.ts))
        self._session_gap = session_gap
        self._horizon = horizon
        self._by_id: dict[Any, _Occurrence] = {}
        self._by_key: dict[tuple[str, str, str], list[_Occurrence]] = {}
        self.newest_ts: datetime | None = _as_utc(ordered[-1].ts) if ordered else None

        session = 0
        visit = -1
        previous_ts: datetime | None = None
        previous_key: tuple[str, str, str] | None = None
        for record in ordered:
            ts = _as_utc(record.ts)
            key = context_key(record)
            new_session = previous_ts is not None and (ts - previous_ts) > session_gap
            if new_session:
                session += 1
            if new_session or key != previous_key:
                visit += 1
            previous_ts, previous_key = ts, key
            occ = _Occurrence(ts=ts, session=session, visit=visit, record_id=record.id)
            self._by_id[record.id] = occ
            self._by_key.setdefault(key, []).append(occ)

    def horizon_end(self, target: Recordable) -> datetime:
        """The last instant a signal for ``target`` may consider: ``ts + horizon``."""
        return self._by_id[target.id].ts + self._horizon

    def is_horizon_observed(self, target: Recordable) -> bool:
        """True once the data reaches ``ts + horizon``, so the window is fully observed."""
        return self.newest_ts is not None and self.horizon_end(target) <= self.newest_ts

    def is_revisited(self, target: Recordable, *, window: timedelta | None = None) -> bool:
        """True if the same context appears in a *later visit* within ``(ts, ts + window]``.

        Later captures within the target's own visit do not count. ``window`` defaults to the
        timeline's horizon and is clamped to it, so no caller can widen the lookahead past what the
        horizon allows.
        """
        occ = self._by_id[target.id]
        span = self._horizon if window is None else min(window, self._horizon)
        cutoff = occ.ts + span
        return any(
            other.visit != occ.visit and occ.ts < other.ts <= cutoff
            for other in self._by_key.get(context_key(target), ())
        )

    def is_continued(self, target: Recordable) -> bool:
        """True if the same context appears in a *later session* within ``(ts, ts + horizon]``."""
        occ = self._by_id[target.id]
        cutoff = occ.ts + self._horizon
        return any(
            other.session > occ.session and occ.ts < other.ts <= cutoff
            for other in self._by_key.get(context_key(target), ())
        )

    def session_of(self, record: Recordable) -> int:
        return self._by_id[record.id].session


# =============================================================================================
# Orchestration
# =============================================================================================

class LabelConstructor:
    """Builds labels for records that are past ``label_delay``, unlabelled, and uncensored."""

    def __init__(
        self,
        *,
        label_delay: timedelta = DEFAULT_LABEL_DELAY,
        label_horizon: timedelta = DEFAULT_LABEL_HORIZON,
        session_gap: timedelta = DEFAULT_SESSION_GAP,
        revisit_window: timedelta | None = None,
        retrieval_fn: RetrievalFn | None = None,
        weights: LabelWeights = DEFAULT_WEIGHTS,
        threshold: float = 0.5,
    ) -> None:
        self.label_delay = label_delay
        self.label_horizon = label_horizon
        self.session_gap = session_gap
        # Narrows revisit within the horizon; it can never widen past it (see Timeline).
        self.revisit_window = revisit_window
        # Member 3 injects this later; default treats retrieval as always-unknown (None).
        self.retrieval_fn = retrieval_fn
        self.weights = weights
        self.threshold = threshold

    @property
    def label_version(self) -> str:
        """Identifies the rule and horizon behind a label, e.g. ``"v2/h48"``.

        Both inputs change the label, so both are stamped: a label built under a 48h horizon is not
        comparable with one built under 72h, even from the same rule.
        """
        hours = self.label_horizon.total_seconds() / 3600.0
        hours_text = f"{hours:g}"
        return f"v{LABEL_RULE_VERSION}/h{hours_text}"

    def is_due(self, record: Recordable, now: datetime, *, already_labelled: bool = False) -> bool:
        """True if ``record`` is unlabelled and the delay has elapsed on the wall clock."""
        return (not already_labelled) and (_as_utc(now) - _as_utc(record.ts)) >= self.label_delay

    def is_censored(self, record: Recordable, newest_ts: datetime | None) -> bool:
        """True if the record's horizon reaches past the newest ingested data (window still open)."""
        if newest_ts is None:
            return True
        return (_as_utc(record.ts) + self.label_horizon) > _as_utc(newest_ts)

    def label_for(self, target: Recordable, timeline: Timeline) -> LabelResult:
        revisited = timeline.is_revisited(target, window=self.revisit_window)
        continued = timeline.is_continued(target)
        retrieval = self.retrieval_fn(target) if self.retrieval_fn is not None else None
        return combine_importance_label(
            revisited, continued, retrieval, weights=self.weights, threshold=self.threshold
        )

    def construct_batch(
        self,
        records: Iterable[Recordable],
        *,
        now: datetime | None = None,
        labelled_ids: set[Any] | None = None,
    ) -> list[tuple[Any, LabelResult]]:
        """
        Label every due, unlabelled, uncensored record, using ``records`` as the activity timeline.

        A record is labelled only when both hold: the delay has elapsed (``is_due``) *and* the data
        covers its whole horizon (``is_censored`` is False). The timeline includes newer records —
        that is what the delay buys us — but each record's signals are confined to its own
        ``(ts, ts + horizon]`` window, so the label does not change as more data arrives.
        ``labelled_ids`` marks records already labelled; those are never relabelled.
        """
        all_records = list(records)
        reference = _as_utc(now) if now is not None else datetime.now(timezone.utc)
        already = labelled_ids or set()
        timeline = Timeline(all_records, session_gap=self.session_gap, horizon=self.label_horizon)

        results: list[tuple[Any, LabelResult]] = []
        for record in all_records:
            if not self.is_due(record, reference, already_labelled=record.id in already):
                continue
            if self.is_censored(record, timeline.newest_ts):
                continue
            results.append((record.id, self.label_for(record, timeline)))
        return results

    def count_censored(
        self,
        records: Iterable[Recordable],
        *,
        now: datetime | None = None,
        labelled_ids: set[Any] | None = None,
    ) -> int:
        """How many otherwise-due records are held back because their horizon is still open."""
        all_records = list(records)
        reference = _as_utc(now) if now is not None else datetime.now(timezone.utc)
        already = labelled_ids or set()
        newest = max((_as_utc(r.ts) for r in all_records), default=None)
        return sum(
            1
            for record in all_records
            if self.is_due(record, reference, already_labelled=record.id in already)
            and self.is_censored(record, newest)
        )


def label_pending_records(
    conn,
    constructor: LabelConstructor,
    *,
    now: datetime | None = None,
    write: bool = True,
) -> list[tuple[Any, LabelResult]]:
    """
    Storage-backed runner: build labels for all due, unlabelled, uncensored records in ``conn`` and
    (by default) persist them via ``storage.set_label``, stamped with ``constructor.label_version``.
    Returns the (record_id, result) pairs it produced; censored records are left for a later run.
    """
    from storage.context_store import list_context_records, set_label

    all_records = list_context_records(conn)
    labelled_ids = {r.id for r in all_records if r.label is not None}
    results = constructor.construct_batch(all_records, now=now, labelled_ids=labelled_ids)
    if write:
        for record_id, result in results:
            set_label(conn, record_id, result.label, label_version=constructor.label_version)
    return results


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
