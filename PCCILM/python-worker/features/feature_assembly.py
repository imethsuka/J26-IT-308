"""Assemble a flat feature dict for one captured context.

``assemble_features`` combines four groups into scalar/token features for the importance model:

  behavioural  input counts in the trailing window, app-switch rate
  temporal     dwell time, time of day, time since last save
  contextual   app / title tokens / domain, revisit-so-far (counted in visits, see below)
  visual       cosine similarity to the nearest importance prototype, and a novelty score against
               recently saved embeddings

The raw 512-d embedding is **never** placed in the output. It is summarized into two scalars —
``vis_proto_similarity`` and ``vis_novelty`` — as required; the model sees those, not the vector.

Prototypes are an injected list (empty at cold start). Recently saved embeddings live in a small
``SavedEmbeddingRing`` the caller owns and updates when a context is saved; this function only reads
it. Feature assembly is dimension-agnostic, so tests can use short synthetic vectors.

Revisit features count *visits*, not records. The collector sends a snapshot on every screen
change, so one stretch in the same window yields many consecutive records with the same context;
counting those would mark almost every capture "seen before". A visit is a maximal run of
consecutive history entries with the same context and no gap longer than :data:`VISIT_GAP`; the
visit the current event continues is in progress, not a revisit.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DEFAULT_RING_CAPACITY = 32
# A gap longer than this ends a visit even if the context is unchanged. Matches
# adaptation.label_construction.DEFAULT_SESSION_GAP, so features and labels agree on what a visit is.
VISIT_GAP = timedelta(minutes=30)
# Reported for time-since-last-save when nothing has been saved yet. Paired with tmp_has_prior_save
# so a model can tell "no prior save" apart from a genuine gap.
_NO_SAVE_SENTINEL = -1.0


@dataclass
class SensingEvent:
    """Normalized view of one sensing snapshot, decoupled from the IPC wire types."""

    ts: datetime
    app: str | None = None
    title: str | None = None
    domain: str | None = None
    excluded: bool = False
    dwell_seconds: float = 0.0
    key_presses: int = 0
    mouse_clicks: int = 0
    mouse_scrolls: int = 0
    mouse_moves: int = 0
    window_seconds: int = 0
    is_idle: bool = False
    idle_seconds: float = 0.0

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "SensingEvent":
        context = getattr(snapshot, "context", {}) or {}
        activity = getattr(snapshot, "activity", None) or {}
        idle = getattr(snapshot, "idle", {}) or {}
        return cls(
            ts=_as_utc(snapshot.ts),
            app=context.get("app"),
            title=context.get("title"),
            domain=context.get("domain"),
            excluded=bool(context.get("excluded")),
            dwell_seconds=float(context.get("dwell_seconds", 0.0) or 0.0),
            key_presses=int(activity.get("key_presses", 0) or 0),
            mouse_clicks=int(activity.get("mouse_clicks", 0) or 0),
            mouse_scrolls=int(activity.get("mouse_scrolls", 0) or 0),
            mouse_moves=int(activity.get("mouse_moves", 0) or 0),
            window_seconds=int(activity.get("window_seconds", 0) or 0),
            is_idle=bool(idle.get("is_idle", False)),
            idle_seconds=float(idle.get("idle_seconds", 0.0) or 0.0),
        )


@dataclass
class ContextHistoryEntry:
    """One prior context, used for app-switch rate and revisit detection."""

    ts: datetime
    app: str | None = None
    title: str | None = None
    domain: str | None = None


class SavedEmbeddingRing:
    """
    Fixed-capacity ring of recently *saved* (deemed important) embeddings, with timestamps.

    Embeddings are stored L2-normalized so novelty is a plain dot product. The caller adds to it
    when a context is saved; ``assemble_features`` only reads it. It also tracks the time of the
    last save, which :meth:`mark_saved` updates for saves that have no embedding.
    """

    def __init__(self, capacity: int = _DEFAULT_RING_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._embeddings: deque[np.ndarray] = deque(maxlen=capacity)
        self._timestamps: deque[datetime] = deque(maxlen=capacity)
        self._last_save: datetime | None = None

    def add(self, embedding: Sequence[float], ts: datetime) -> None:
        self._embeddings.append(_unit(np.asarray(embedding, dtype=np.float32)))
        self._timestamps.append(_as_utc(ts))
        self.mark_saved(ts)

    def mark_saved(self, ts: datetime) -> None:
        """Record a save at ``ts`` without an embedding (no frame, or embedding disabled)."""
        ts = _as_utc(ts)
        if self._last_save is None or ts > self._last_save:
            self._last_save = ts

    def __len__(self) -> int:
        return len(self._embeddings)

    def matrix(self) -> np.ndarray:
        """(N, D) of unit embeddings, or an empty (0, 0) array when nothing is saved."""
        if not self._embeddings:
            return np.empty((0, 0), dtype=np.float32)
        return np.vstack(self._embeddings)

    def latest_timestamp(self) -> datetime | None:
        """Time of the most recent save, with or without an embedding."""
        return self._last_save


def assemble_features(
    sensing_event: Any,
    embedding: Sequence[float] | None,
    recent_context_history: Iterable[Any] | None,
    *,
    prototypes: Sequence[Any] | None = None,
    saved_embeddings: SavedEmbeddingRing | None = None,
    now: datetime | None = None,
    title_normalizer: Callable[[str | None], str | None] | None = None,
) -> dict[str, Any]:
    """
    Build the feature dict for ``sensing_event``.

    ``embedding`` is the frame's 512-d vector (summarized, not emitted); ``None`` disables the
    visual features. ``prototypes`` is a list of importance-prototype vectors or ``(label, vector)``
    pairs (empty/omitted at cold start). ``saved_embeddings`` is the novelty ring; ``now`` overrides
    the reference time (defaults to the event timestamp). ``title_normalizer`` (opt-in, e.g.
    ``features.titles.normalize_title``) is applied to titles before revisit matching and title
    tokenization, matching the v4 label rule; by default exact titles are used.
    """
    event = sensing_event if isinstance(sensing_event, SensingEvent) else SensingEvent.from_snapshot(sensing_event)
    reference_time = _as_utc(now) if now is not None else event.ts
    history = [_as_history_entry(item) for item in (recent_context_history or [])]

    features: dict[str, Any] = {}
    features.update(_behavioural(event, history))
    features.update(_temporal(event, reference_time, saved_embeddings))
    features.update(_contextual(event, history, title_normalizer))
    features.update(_visual(embedding, prototypes, saved_embeddings))
    return features


def _behavioural(event: SensingEvent, history: list[ContextHistoryEntry]) -> dict[str, Any]:
    window = event.window_seconds
    interactions = event.key_presses + event.mouse_clicks + event.mouse_scrolls
    switches, span_minutes, distinct = _app_switches(history, event)
    return {
        "beh_key_presses": event.key_presses,
        "beh_mouse_clicks": event.mouse_clicks,
        "beh_mouse_scrolls": event.mouse_scrolls,
        "beh_mouse_moves": event.mouse_moves,
        "beh_window_seconds": window,
        "beh_interactions_per_second": round(interactions / window, 4) if window > 0 else 0.0,
        "beh_app_switches": switches,
        "beh_app_switch_rate_per_min": round(switches / span_minutes, 4) if span_minutes > 0 else 0.0,
        "beh_distinct_apps": distinct,
    }


def _temporal(event: SensingEvent, now: datetime, ring: SavedEmbeddingRing | None) -> dict[str, Any]:
    local = now.astimezone()  # time of day is a human-facing notion; use the machine's local zone
    seconds_into_day = local.hour * 3600 + local.minute * 60 + local.second
    fraction = seconds_into_day / 86400.0

    last_save = ring.latest_timestamp() if ring is not None else None
    if last_save is None:
        time_since_save = _NO_SAVE_SENTINEL
        has_prior_save = 0
    else:
        time_since_save = round(max(0.0, (now - last_save).total_seconds()), 3)
        has_prior_save = 1

    return {
        "tmp_dwell_seconds": round(event.dwell_seconds, 3),
        "tmp_hour_of_day": local.hour,
        "tmp_tod_sin": round(math.sin(2 * math.pi * fraction), 6),
        "tmp_tod_cos": round(math.cos(2 * math.pi * fraction), 6),
        "tmp_is_weekend": 1 if local.weekday() >= 5 else 0,
        "tmp_is_idle": 1 if event.is_idle else 0,
        "tmp_time_since_last_save_seconds": time_since_save,
        "tmp_has_prior_save": has_prior_save,
    }


def _contextual(event: SensingEvent, history: list[ContextHistoryEntry], title_normalizer=None) -> dict[str, Any]:
    normalize = title_normalizer or (lambda title: title)
    key = _context_key(event.app, event.domain, normalize(event.title))
    visits = _visits(history, normalize)
    # The visit the current event continues (same context, no gap) is in progress, not a revisit.
    if visits and visits[-1][0] == key and (event.ts - visits[-1][1]) <= VISIT_GAP:
        visits = visits[:-1]
    revisit_count = 0
    if not event.excluded:
        revisit_count = sum(1 for visit_key, _ in visits if visit_key == key)
    title_tokens = _tokenize(normalize(event.title))
    return {
        "ctx_app": event.app,
        "ctx_domain": event.domain,
        "ctx_title_tokens": title_tokens,
        "ctx_title_token_count": len(title_tokens),
        "ctx_excluded": 1 if event.excluded else 0,
        "ctx_seen_before": 1 if revisit_count > 0 else 0,
        "ctx_revisit_count": revisit_count,
        # Share of earlier visits that were to this context.
        "ctx_revisit_ratio": round(revisit_count / len(visits), 4) if visits else 0.0,
    }


def _visits(history: list[ContextHistoryEntry], normalize=lambda title: title) -> list[tuple[tuple[str, str, str], datetime]]:
    """Collapse history into visits, oldest first, as ``(context_key, last_seen_ts)`` pairs."""
    visits: list[tuple[tuple[str, str, str], datetime]] = []
    for entry in sorted(history, key=lambda e: e.ts):
        key = _context_key(entry.app, entry.domain, normalize(entry.title))
        if visits and visits[-1][0] == key and (entry.ts - visits[-1][1]) <= VISIT_GAP:
            visits[-1] = (key, entry.ts)
        else:
            visits.append((key, entry.ts))
    return visits


def prototype_features(embedding: Sequence[float] | None, prototypes: Sequence[Any] | None) -> dict[str, Any]:
    """Just the prototype-similarity part of the visual features (the ``vis_proto_*`` keys).

    Lets a retrain recompute these from a stored embedding against freshly learned prototypes
    (``features.update(prototype_features(embedding, new_prototypes))``), so the model is trained on
    the same prototypes it will be served with. Novelty is left alone: it depends on what was saved
    at capture time, not on the prototypes.
    """
    result = {
        "vis_proto_similarity": 0.0,
        "vis_proto_distance": 1.0,
        "vis_nearest_prototype": None,
        "vis_has_prototypes": 0,
    }
    if embedding is None:
        return result
    labels, proto_matrix = _prototype_matrix(prototypes)
    if proto_matrix.size:
        sims = proto_matrix @ _unit(np.asarray(embedding, dtype=np.float32))
        best = int(np.argmax(sims))
        result["vis_proto_similarity"] = round(float(sims[best]), 6)
        result["vis_proto_distance"] = round(float(1.0 - sims[best]), 6)
        result["vis_nearest_prototype"] = labels[best]
        result["vis_has_prototypes"] = 1
    return result


def _visual(
    embedding: Sequence[float] | None,
    prototypes: Sequence[Any] | None,
    ring: SavedEmbeddingRing | None,
) -> dict[str, Any]:
    # Cold start / no frame: neutral similarity, maximal novelty, with flags so the model can tell.
    result = {
        **prototype_features(embedding, prototypes),
        "vis_novelty": 1.0,
        "vis_max_saved_similarity": 0.0,
        "vis_has_saved": 0,
    }
    if embedding is None:
        return result

    query = _unit(np.asarray(embedding, dtype=np.float32))

    if ring is not None and len(ring):
        saved = ring.matrix()
        max_sim = float(np.max(saved @ query))
        result["vis_max_saved_similarity"] = round(max_sim, 6)
        # Novelty: 1 at completely dissimilar, 0 at an exact repeat. Clipped to [0, 1].
        result["vis_novelty"] = round(float(np.clip(1.0 - max_sim, 0.0, 1.0)), 6)
        result["vis_has_saved"] = 1

    return result


def _app_switches(history: list[ContextHistoryEntry], event: SensingEvent) -> tuple[int, float, int]:
    ordered = sorted(history, key=lambda e: e.ts)
    apps = [e.app for e in ordered] + [event.app]
    switches = sum(1 for a, b in zip(apps, apps[1:]) if a != b)
    distinct = len({a for a in apps if a is not None})
    if ordered:
        span = (event.ts - ordered[0].ts).total_seconds()
        span_minutes = max(span, 0.0) / 60.0
    else:
        span_minutes = 0.0
    return switches, span_minutes, distinct


def _prototype_matrix(prototypes: Sequence[Any] | None) -> tuple[list[Any], np.ndarray]:
    # len() rather than truthiness, so a 2-D numpy centroid array (from prototype_learning) works
    # without raising "truth value of an array is ambiguous".
    if prototypes is None or len(prototypes) == 0:
        return [], np.empty((0, 0), dtype=np.float32)
    labels: list[Any] = []
    rows: list[np.ndarray] = []
    for index, proto in enumerate(prototypes):
        if isinstance(proto, tuple) and len(proto) == 2:
            label, vector = proto
        else:
            label, vector = index, proto
        labels.append(label)
        rows.append(_unit(np.asarray(vector, dtype=np.float32)))
    return labels, np.vstack(rows)


def _context_key(app: str | None, domain: str | None, title: str | None) -> tuple[str, str, str]:
    return (
        (app or "").strip().lower(),
        (domain or "").strip().lower(),
        (title or "").strip().lower(),
    )


def _tokenize(title: str | None) -> list[str]:
    if not title:
        return []
    return [tok for tok in _TOKEN_RE.findall(title.lower()) if len(tok) >= 2]


def _as_history_entry(item: Any) -> ContextHistoryEntry:
    if isinstance(item, ContextHistoryEntry):
        return ContextHistoryEntry(ts=_as_utc(item.ts), app=item.app, title=item.title, domain=item.domain)
    if isinstance(item, SensingEvent):
        return ContextHistoryEntry(ts=item.ts, app=item.app, title=item.title, domain=item.domain)
    if isinstance(item, dict):
        return ContextHistoryEntry(
            ts=_as_utc(item["ts"]),
            app=item.get("app"),
            title=item.get("title"),
            domain=item.get("domain"),
        )
    # Duck-typed (e.g. a sensing Snapshot).
    context = getattr(item, "context", None)
    if context is not None:
        return ContextHistoryEntry(
            ts=_as_utc(item.ts), app=context.get("app"), title=context.get("title"), domain=context.get("domain")
        )
    return ContextHistoryEntry(
        ts=_as_utc(item.ts), app=getattr(item, "app", None),
        title=getattr(item, "title", None), domain=getattr(item, "domain", None),
    )


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return vector
    return vector / norm


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
