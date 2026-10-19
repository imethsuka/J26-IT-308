"""Wires a received snapshot through features -> model -> storage.

This is the piece ``main.py`` previously deferred ("routing through features -> models ->
adaptation -> storage comes later"): :class:`SnapshotPipeline` is a ``SnapshotHandler``
(``sensing.listener.SnapshotHandler``) that, for each decoded :class:`~sensing.protocol.Snapshot`:

    1. Skips excluded contexts untouched (nothing is embedded, scored, or stored for them).
    2. Embeds the frame with MobileCLIP (``features.mobileclip_encoder``), unless embedding is
       disabled (``--no-embedding``). If the encoder cannot be loaded (no checkpoint configured,
       torch/mobileclip missing), embedding is switched off for the rest of the run after one
       warning, instead of failing again on every frame.
    3. Assembles features (``features.feature_assembly.assemble_features``) against the current
       importance prototypes and the novelty ring of previously *saved* embeddings.
    4. Scores the features with an ``ImportanceScorer`` -- the currently active/deployed model if
       one exists (``storage.context_store.get_active_model_version`` + a saved LightGBM
       artifact), otherwise the interpretable rule-based baseline, so the system is usable before
       any model has ever been trained.
    5. Inserts a ``ContextRecord`` (features, embedding, decision, label left NULL for the
       adaptation cycle to fill in later).

Cold start: prototypes are loaded once at startup from the active model version (empty if there
is none yet, or none were saved with it), matching ``assemble_features``'s documented cold-start
behaviour. They are not hot-reloaded mid-run; restart the worker after a deploy to pick up new
prototypes for future captures (this mirrors the note in ``adaptation/scheduled_job.py`` that a
retrain's prototypes only affect *future* captures, never past ones).

Every non-excluded snapshot is stored, whatever the decision: label construction needs the full
timeline, and training needs the discarded contexts too. The decision is recorded, not enforced, so
storage savings reported by ``eval.model_comparison`` are projected, not realized.

Thread safety: the TCP listener calls the handler from per-connection threads, so :meth:`handle`
serializes on a lock. The SQLite connection must be opened with ``check_same_thread=False``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Sequence

import numpy as np

from features import surfaces
from features.feature_assembly import (
    REVISIT_HORIZON, ContextHistoryEntry, SavedEmbeddingRing, SensingEvent, VisitLog, assemble_features,
)
from models import registry
from models.base import ImportanceScorer
from models.drc_rules import LiveDrcObserver
from models.rule_based import RuleBasedImportanceScorer
from sensing.protocol import Snapshot
from storage.raw_buffer import RawBuffer
from storage.screenshot_store import ScreenshotStore
from storage.context_store import (
    ContextRecord, ModelDecision, get_active_model_version, insert_context_record, insert_model_decisions,
    latest_saves, latest_versions_by_algorithm, list_timeline, recent_scores, scoring_inputs_by_id,
)

logger = logging.getLogger("worker.pipeline")

# Windows shell surfaces (the compositor, the lock screen, the Alt+Tab switcher, ...) are not contexts: they are skipped
# before anything is embedded or stored. The list and the rule live in features/surfaces.py.
NON_CONTEXT_APPS = surfaces.NON_CONTEXT_APPS

DEFAULT_HISTORY_SIZE = 200
DEFAULT_SAVE_RING_CAPACITY = 32
# predict() decision threshold for the stored 'save'/'discard' label -- distinct from the ground
# -truth label that label_construction fills in ~48h later.
DEFAULT_DECISION_THRESHOLD = 0.5
# Rolling cutoff (a deployed model with a keep rate): save a snapshot when its score is in the top ``keep_rate`` of the
# last DEFAULT_SCORE_WINDOW live scores. A fixed cutoff tuned on the held-out set kept 30% there and 1-4% live, because
# live scores sit lower than held-out ones. Below DEFAULT_MIN_WINDOW scores the stored cutoff is used.
DEFAULT_SCORE_WINDOW = 3000
DEFAULT_MIN_WINDOW = 200
# Redundancy gate: after the model says "save", a snapshot is turned into "discard" when the last save was
# less than this many seconds ago AND it is the same app+title or looks (cosine) almost the same. The model
# scores each screen alone and its labels reward staying on a page, so without this it re-saves one screen
# many times a minute. 0 turns the gate off. The record is still stored (as "discard"), as labelling needs it.
DEFAULT_REDUNDANCY_WINDOW_S = 60.0
DEFAULT_REDUNDANCY_SIMILARITY = 0.95


_app_key = surfaces.app_key


def load_active_scorer(conn: Connection, *, versions_dir: Path | str | None = None) -> tuple[ImportanceScorer, np.ndarray | None]:
    """The currently deployed Model 1 + its prototypes, or the rule-based baseline at cold start.

    Returns ``(scorer, prototypes)``; ``prototypes`` is ``None`` when there is no active model or
    it was saved without any (feeds straight into ``assemble_features(prototypes=...)``).
    """
    active = get_active_model_version(conn)
    if active is None:
        logger.info("no active model version yet; scoring with the rule-based baseline")
        return RuleBasedImportanceScorer(), None

    from adaptation.prototype_learning import DEFAULT_VERSIONS_DIR as PROTO_VERSIONS_DIR
    from adaptation.prototype_learning import load_prototypes
    from models.lightgbm_model import DEFAULT_VERSIONS_DIR as MODEL_VERSIONS_DIR

    model_dir = Path(versions_dir) if versions_dir else MODEL_VERSIONS_DIR
    proto_dir = Path(versions_dir) if versions_dir else PROTO_VERSIONS_DIR
    try:
        model = registry.load_scorer(active.algorithm, active.version, model_dir)
        logger.info("loaded active model version %r (%s)", active.version, registry.algorithm_of(active.algorithm))
    except (FileNotFoundError, ValueError, ImportError):
        logger.warning("active model version %r has no saved artifact; falling back to rule-based", active.version)
        return RuleBasedImportanceScorer(), None

    try:
        prototypes = load_prototypes(active.version, versions_dir=proto_dir)
    except FileNotFoundError:
        prototypes = None
    return model, prototypes


def active_decision_threshold(conn: Connection) -> float | None:
    """The save/discard threshold chosen when the active model was deployed (from its keep-rate budget),
    or None when there is no active model or it predates the budget (the caller then uses its default)."""
    active = get_active_model_version(conn)
    value = (active.metrics or {}).get("decision_threshold") if active is not None else None
    return float(value) if isinstance(value, (int, float)) else None


def active_keep_rate(conn: Connection) -> float:
    """The share of snapshots the active model was deployed to keep (DEFAULT_KEEP_RATE when it predates storing it)."""
    from adaptation.deployment import DEFAULT_KEEP_RATE

    active = get_active_model_version(conn)
    value = (active.metrics or {}).get("keep_rate") if active is not None else None
    return float(value) if isinstance(value, (int, float)) and 0 < value <= 1 else DEFAULT_KEEP_RATE


def _default_keep_rate() -> float:
    from adaptation.deployment import DEFAULT_KEEP_RATE

    return DEFAULT_KEEP_RATE


def derive_budget_threshold(conn: Connection, scorer: ImportanceScorer, *, keep_rate: float = 0.30, max_rows: int = 3000) -> float | None:
    """Fallback for a model deployed before thresholds were stored: the score that lets ``keep_rate`` of the
    stored records through, from this model's own scores on those records (evenly sampled to ``max_rows``).
    None when there are too few records to say."""
    from adaptation.deployment import budget_threshold
    from storage.context_store import list_context_records

    records = list_context_records(conn)
    if len(records) < 200:
        return None
    step = max(1, len(records) // max_rows)
    return budget_threshold((scorer.predict_proba(r.features) for r in records[::step]), keep_rate)


@dataclass
class ShadowModel:
    """A model that scores every snapshot next to the champion. Its verdict is recorded in ``model_decisions``, never
    enforced. ``fallback_cutoff`` is used until its rolling window has DEFAULT_MIN_WINDOW scores."""

    scorer: ImportanceScorer
    model: str
    algorithm: str
    model_version_id: int | None = None
    fallback_cutoff: float = DEFAULT_DECISION_THRESHOLD
    scores: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_SCORE_WINDOW))
    failed: bool = False

    def cutoff(self, keep_rate: float) -> float:
        if len(self.scores) < DEFAULT_MIN_WINDOW:
            return self.fallback_cutoff
        return float(np.quantile(np.fromiter(self.scores, dtype=float), 1.0 - keep_rate))


def load_shadows(conn: Connection, *, champion_version: str | None, versions_dir: Path | str | None = None,
                 include_rules: bool = True) -> list[ShadowModel]:
    """The newest saved version of every algorithm except the champion itself, plus the rule-based baseline (unless
    the rules are the champion). A version that fails to load is skipped with a warning."""
    from models.lightgbm_model import DEFAULT_VERSIONS_DIR as MODEL_VERSIONS_DIR

    model_dir = Path(versions_dir) if versions_dir else MODEL_VERSIONS_DIR
    shadows = [ShadowModel(RuleBasedImportanceScorer(), registry.RULES_NAME, registry.RULES)] if include_rules else []
    for algorithm, version in sorted(latest_versions_by_algorithm(conn).items()):
        if version.version == champion_version:
            continue
        try:
            scorer = registry.load_scorer(algorithm, version.version, model_dir)
        except Exception as exc:  # a broken or missing shadow must not stop capture
            logger.warning("shadow model %s %r not loaded (%s: %s)", algorithm, version.version, type(exc).__name__, exc)
            continue
        fallback = (version.metrics or {}).get("decision_threshold")
        shadows.append(ShadowModel(scorer, registry.model_name(algorithm, version.version), algorithm, version.id,
                                   float(fallback) if isinstance(fallback, (int, float)) else DEFAULT_DECISION_THRESHOLD))
    return shadows


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0


class SnapshotPipeline:
    """Stateful per-run handler: turns snapshots into stored, scored context records."""

    def __init__(
        self,
        conn: Connection,
        scorer: ImportanceScorer,
        *,
        prototypes: np.ndarray | None = None,
        embed: bool = True,
        decision_threshold: float = DEFAULT_DECISION_THRESHOLD,
        history_size: int = DEFAULT_HISTORY_SIZE,
        save_ring_capacity: int = DEFAULT_SAVE_RING_CAPACITY,
        drc_observer: LiveDrcObserver | None = None,
        raw_buffer: RawBuffer | None = None,
        screenshots: ScreenshotStore | None = None,
        redundancy_window_s: float = DEFAULT_REDUNDANCY_WINDOW_S,
        redundancy_similarity: float = DEFAULT_REDUNDANCY_SIMILARITY,
        keep_rate: float | None = None,
        score_window: int = DEFAULT_SCORE_WINDOW,
        model_version_id: int | None = None,
        shadows: Sequence[ShadowModel] = (),
        champion_algorithm: str | None = None,
    ) -> None:
        self._conn = conn
        self._scorer = scorer
        self._prototypes = prototypes
        self._embed = embed
        self._threshold = decision_threshold
        # None = always use decision_threshold (the rule-based baseline, or a cutoff given on the command line).
        self._keep_rate = keep_rate
        self._scores: deque[float] = deque(maxlen=score_window)
        self._model_version_id = model_version_id
        # Shadow models score every snapshot too; each keeps its own rolling cutoff at the champion's keep rate (or the
        # default budget while the champion's cutoff is fixed), so their would-save verdicts are comparable.
        self._shadows = list(shadows)
        self._champion_algorithm = champion_algorithm or (
            registry.RULES if isinstance(scorer, RuleBasedImportanceScorer) else registry.LIGHTGBM)
        self._history: deque[ContextHistoryEntry] = deque(maxlen=history_size)
        self._visits = VisitLog()
        self._ring = SavedEmbeddingRing(capacity=save_ring_capacity)
        self._drc = drc_observer if drc_observer is not None else LiveDrcObserver()
        self._raw_buffer = raw_buffer
        self._screenshots = screenshots
        self._redundancy_window_s = redundancy_window_s
        self._redundancy_similarity = redundancy_similarity
        self._last_saved: tuple[Any, str, str, np.ndarray | None] | None = None  # (ts, app, title, embedding)
        self._n_redundant = 0
        self._encoder: Any = None
        self._lock = threading.Lock()
        self._closed = False
        self._n_seen = 0
        self._n_stored = 0
        self._n_saved = 0

    @property
    def embedding_enabled(self) -> bool:
        return self._embed

    @property
    def current_threshold(self) -> float:
        """The cutoff the next snapshot is judged against."""
        if self._keep_rate is None or len(self._scores) < DEFAULT_MIN_WINDOW:
            return self._threshold
        return float(np.quantile(np.fromiter(self._scores, dtype=float), 1.0 - self._keep_rate))

    def restore_state(self, *, now: datetime | None = None) -> dict[str, int]:
        """Rebuild what a restart would otherwise forget, from the store: the visit log (last REVISIT_HORIZON), the
        app-switch history, the novelty ring of saved frames, and the rolling score window (the newest records
        rescored with this run's model). Without it every context looks new after a restart and nothing looks like
        a saved frame, which pushes every score down. Call once, before the first snapshot. Returns counts."""
        now = now or datetime.now(timezone.utc)
        timeline = [e for e in list_timeline(self._conn, since=now - REVISIT_HORIZON)
                    if not surfaces.is_non_context(e.app, e.title)]
        # Each model's window refills from the scores it recorded live; models without enough history (new versions,
        # the first run after migration v4) rescore the newest stored records instead.
        need_rescore = [s for s in self._shadows if self._refill(s.scores, s.model)]
        champion_rescore = self._keep_rate is not None and self._refill(self._scores, getattr(self._scorer, "name", ""))
        window_ids = {e.id for e in timeline[-self._scores.maxlen:]} if (champion_rescore or need_rescore) else set()
        stored = scoring_inputs_by_id(self._conn, sorted(window_ids)) if window_ids else {}
        with self._lock:
            rescored: list[tuple[dict[str, Any], Any]] = []
            for entry in timeline:
                if entry.id in stored:
                    features, embedding = stored[entry.id]
                    features.update(self._visits.revisit_features(entry.ts, entry.app, entry.domain, entry.title))
                    rescored.append((features, embedding))
                self._visits.observe(entry.ts, entry.app, entry.domain, entry.title)
            for entry in timeline[-self._history.maxlen:]:
                self._history.append(ContextHistoryEntry(ts=entry.ts, app=entry.app, title=entry.title, domain=entry.domain))
            for ts, embedding in latest_saves(self._conn, self._ring.capacity):
                if embedding is not None:
                    self._ring.add(embedding, ts)
                else:
                    self._ring.mark_saved(ts)
            features_list = [f for f, _ in rescored]
            embeddings = [e for _, e in rescored]
            targets = ([(self._scorer, self._scores, "champion")] if champion_rescore else []) + [
                (s.scorer, s.scores, s.model) for s in need_rescore]
            for scorer, window, label in targets:
                try:
                    window.extend(registry.score_many(scorer, features_list, embeddings))
                except Exception:
                    logger.exception("rescoring stored records with %s failed; its window starts empty", label)
            restored = {"visits": len(self._visits), "history": len(self._history), "saved_frames": len(self._ring),
                        "scores": len(self._scores), "shadows": len(self._shadows)}
        logger.info("restored %s; save cutoff now %.4f", restored, self.current_threshold)
        return restored

    def _refill(self, window: deque, model: str) -> bool:
        """Fill ``window`` with ``model``'s recorded scores; True when there were too few and it needs rescoring."""
        try:
            window.extend(recent_scores(self._conn, model, window.maxlen))
        except Exception:  # no model_decisions table (a database not migrated to v4): rescore instead
            return True
        if len(window) < DEFAULT_MIN_WINDOW:
            window.clear()
            return True
        return False

    def __call__(self, snapshot: Snapshot) -> None:
        self.handle(snapshot)

    def load_encoder(self) -> bool:
        """Load the MobileCLIP encoder now (e.g. at startup) rather than on the first frame.

        Returns whether embedding is enabled afterwards. A load failure disables embedding for the
        rest of the run with a single warning.
        """
        with self._lock:
            return self._ensure_encoder() is not None

    def close(self) -> None:
        """Stop handling snapshots. Waits for one in progress, so the caller can then close the DB."""
        with self._lock:
            self._closed = True

    def handle(self, snapshot: Snapshot) -> None:
        with self._lock:
            if self._closed:
                logger.debug("seq=%d arrived after close; dropped", snapshot.seq)
                return
            self._handle(snapshot)

    def _handle(self, snapshot: Snapshot) -> None:
        self._n_seen += 1
        if snapshot.excluded:
            logger.debug("seq=%d excluded; skipping", snapshot.seq)
            return

        if surfaces.is_non_context(snapshot.context.get("app"), snapshot.context.get("title")):
            logger.debug("seq=%d %r is a shell surface, not a context; skipping", snapshot.seq, snapshot.context.get("app"))
            return

        event = SensingEvent.from_snapshot(snapshot)
        embedding = self._embed_frame(snapshot) if self._embed else None

        features = assemble_features(
            event,
            embedding,
            self._history,
            prototypes=self._prototypes,
            saved_embeddings=self._ring,
            visit_log=self._visits,
        )

        threshold = self.current_threshold
        started = time.perf_counter_ns()
        try:
            proba = registry.score(self._scorer, features, embedding)
            scored = True
        except Exception:  # a scorer bug must not take the listener down mid-run
            logger.exception("seq=%d scoring failed; treating as discard", snapshot.seq)
            proba, scored = 0.0, False
        champion_us = (time.perf_counter_ns() - started) // 1000
        if scored:
            self._scores.append(proba)  # a failure stays out of the window, or the cutoff would fall to 0
        would_save = scored and proba >= threshold
        decision = "save" if would_save else "discard"
        if decision == "save" and self._is_redundant(event.ts, event.app, event.title, embedding):
            decision = "discard"
            self._n_redundant += 1

        drc = self._drc.observe(event.ts, app=event.app, title=event.title, domain=event.domain, is_idle=event.is_idle)

        record = ContextRecord(
            ts=event.ts,
            decision=decision,
            features=features,
            embedding=embedding,
            app=event.app,
            domain=event.domain,
            title=event.title,
            drc_would_capture=drc.capture,
            drc_trigger=drc.trigger if drc.capture else None,
            model_version_id=self._model_version_id,
        )
        expected_dim = None if embedding is None else int(embedding.shape[0])
        record_id = insert_context_record(
            self._conn, record, expected_dim=expected_dim if expected_dim else 512
        )
        self._n_stored += 1
        self._record_decisions(record_id, event.ts, features, embedding, proba if scored else None, threshold,
                               would_save, champion_us)
        self._buffer_text(snapshot, event.ts, decision, proba, record_id)

        if decision == "save":
            self._save_screenshot(snapshot, event.ts, record_id)
            self._n_saved += 1
            self._last_saved = (event.ts, _app_key(event.app), (event.title or "").strip().casefold(), embedding)
            if embedding is not None:
                self._ring.add(embedding, event.ts)
            else:
                self._ring.mark_saved(event.ts)  # keeps time-since-last-save right without a frame

        self._history.append(
            ContextHistoryEntry(ts=event.ts, app=event.app, title=event.title, domain=event.domain)
        )
        self._visits.observe(event.ts, event.app, event.domain, event.title)

        logger.info(
            "seq=%d id=%d %r decision=%s p=%.3f cutoff=%.3f (seen=%d stored=%d saved=%d redundant=%d)",
            snapshot.seq, record_id, event.app, decision, proba, threshold,
            self._n_seen, self._n_stored, self._n_saved, self._n_redundant,
        )

    def _record_decisions(self, record_id: int, ts: Any, features: dict[str, Any], embedding: np.ndarray | None,
                          champion_score: float | None, champion_cutoff: float, champion_would_save: bool,
                          champion_us: int) -> None:
        """Write what the champion and every shadow model said about this context. would-save is before the
        redundancy gate, for all of them, so they compare fairly. Never raises: the record is already stored."""
        decisions = []
        if champion_score is not None:
            decisions.append(ModelDecision(
                record_id, getattr(self._scorer, "name", "champion"), self._champion_algorithm, "champion", ts,
                champion_score, champion_would_save, champion_cutoff, self._model_version_id, champion_us))
        keep_rate = self._keep_rate if self._keep_rate is not None else _default_keep_rate()
        for shadow in self._shadows:
            cutoff = shadow.cutoff(keep_rate)
            started = time.perf_counter_ns()
            try:
                value = registry.score(shadow.scorer, features, embedding)
            except Exception:
                if not shadow.failed:
                    logger.exception("shadow model %s failed to score; it is skipped until it works again", shadow.model)
                shadow.failed = True
                continue
            shadow.failed = False
            shadow.scores.append(value)
            decisions.append(ModelDecision(
                record_id, shadow.model, shadow.algorithm, "shadow", ts, value, value >= cutoff, cutoff,
                shadow.model_version_id, (time.perf_counter_ns() - started) // 1000))
        try:
            insert_model_decisions(self._conn, decisions)
        except Exception:
            logger.exception("record %d: model decisions not stored", record_id)

    def _is_redundant(self, ts: Any, app: str | None, title: str | None, embedding: np.ndarray | None) -> bool:
        """True when the last save was recent and this is the same screen again (same app+title, or a near-identical frame)."""
        if self._redundancy_window_s <= 0 or self._last_saved is None:
            return False
        last_ts, last_app, last_title, last_embedding = self._last_saved
        if not 0 <= (ts - last_ts).total_seconds() < self._redundancy_window_s:
            return False
        if _app_key(app) == last_app and (title or "").strip().casefold() == last_title:
            return True
        return (
            embedding is not None and last_embedding is not None
            and _cosine(embedding, last_embedding) >= self._redundancy_similarity
        )

    def _buffer_text(self, snapshot: Snapshot, ts: Any, decision: str, proba: float, record_id: int) -> None:
        """Put the screen text the collector read into the raw buffer (a separate, encrypted file).

        Never raises: losing a capture's text must not lose its record. Nothing is logged but the exception type,
        because the text is sensitive.
        """
        if self._raw_buffer is None or snapshot.text is None:
            return
        try:
            self._raw_buffer.add(
                snapshot.text, ts=ts, app=snapshot.context.get("app"), domain=snapshot.context.get("domain"),
                title=snapshot.context.get("title"), decision=decision, score=proba, activity=snapshot.activity,
                is_idle=bool(snapshot.idle.get("is_idle")), context_record_id=record_id,
            )
        except RuntimeError as exc:  # no DPAPI (not Windows): text can't be protected, so it is never kept
            logger.warning("screen text can't be protected (%s); raw buffering is off for this run", exc)
            self._raw_buffer = None
        except Exception as exc:
            logger.warning("seq=%d screen text not buffered (%s)", snapshot.seq, type(exc).__name__)

    def _save_screenshot(self, snapshot: Snapshot, ts: Any, record_id: int) -> None:
        """Keep the frame behind a ``save`` decision (only when the screenshot store is on). Never raises: losing a
        screenshot must not lose its record."""
        frame = snapshot.frame
        if self._screenshots is None or frame is None or not snapshot.payload:
            return
        if len(snapshot.payload) < frame.stride * frame.height:
            logger.warning("seq=%d frame payload too short for a screenshot; skipped", snapshot.seq)
            return
        try:
            self._screenshots.add(
                snapshot.payload, width=frame.width, height=frame.height, stride=frame.stride, ts=ts,
                context_record_id=record_id,
            )
        except RuntimeError as exc:  # no DPAPI (not Windows): a screenshot can't be protected, so none is kept
            logger.warning("screenshots can't be protected (%s); screenshots are off for this run", exc)
            self._screenshots = None
        except Exception as exc:
            logger.warning("seq=%d screenshot not stored (%s)", snapshot.seq, type(exc).__name__)

    def _ensure_encoder(self) -> Any:
        """The loaded encoder, loading it on first use; None once embedding is disabled."""
        if not self._embed:
            return None
        if self._encoder is None:
            try:
                from features.mobileclip_encoder import default_encoder

                self._encoder = default_encoder()
            except Exception as exc:
                self._embed = False
                logger.warning(
                    "MobileCLIP encoder unavailable (%s: %s); embedding disabled for this run, so "
                    "records are stored without embeddings. Provide mobileclip_s0_image.onnx "
                    "(MOBILECLIP_ONNX_MODEL) or set MOBILECLIP_S0_CHECKPOINT to mobileclip_s0.pt, "
                    "or pass --no-embedding to skip this.",
                    type(exc).__name__, exc,
                )
                return None
        return self._encoder

    def _embed_frame(self, snapshot: Snapshot) -> np.ndarray | None:
        frame = snapshot.frame
        if frame is None or not snapshot.payload:
            return None
        encoder = self._ensure_encoder()
        if encoder is None:
            return None

        array = np.frombuffer(snapshot.payload, dtype=np.uint8)
        expected = frame.height * frame.width * 4
        if array.size != expected:
            logger.warning(
                "seq=%d frame payload is %d bytes, expected %d (%dx%d bgra32); skipping embedding",
                snapshot.seq, array.size, expected, frame.width, frame.height,
            )
            return None
        image = array.reshape(frame.height, frame.width, 4)

        try:
            return encoder.embed_frame(image)
        except Exception:
            logger.exception("seq=%d embedding failed; storing without one", snapshot.seq)
            return None
