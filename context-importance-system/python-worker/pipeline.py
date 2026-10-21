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
from collections import deque
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import numpy as np

from features.feature_assembly import ContextHistoryEntry, SavedEmbeddingRing, SensingEvent, assemble_features
from models.base import ImportanceScorer
from models.drc_rules import LiveDrcObserver
from models.rule_based import RuleBasedImportanceScorer
from sensing.protocol import Snapshot
from storage.raw_buffer import RawBuffer
from storage.context_store import ContextRecord, get_active_model_version, insert_context_record

logger = logging.getLogger("worker.pipeline")

# Windows shell surfaces that are not something the user was working in: the compositor, the lock screen,
# search/start popups and the shell host. Measured on the author's captures, they dominate the "returns most
# often" ranking only because they pop up constantly, and they would pollute labels and the user profile.
# Like the DRC replay (which drops the lock screen), they are skipped before anything is embedded or stored.
# Real apps that merely host other windows (explorer, ApplicationFrameHost) are NOT listed.
NON_CONTEXT_APPS = frozenset({
    "dwm", "lockapp", "logonui", "searchhost", "searchapp", "shellhost", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost",
})

DEFAULT_HISTORY_SIZE = 200
DEFAULT_SAVE_RING_CAPACITY = 32
# predict() decision threshold for the stored 'save'/'discard' label -- distinct from the ground
# -truth label that label_construction fills in ~48h later.
DEFAULT_DECISION_THRESHOLD = 0.5


def _app_key(app: str | None) -> str:
    name = (app or "").strip().lower()
    return name[:-4] if name.endswith(".exe") else name


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
    from models.lightgbm_model import ImportanceModel

    model_dir = Path(versions_dir) if versions_dir else MODEL_VERSIONS_DIR
    proto_dir = Path(versions_dir) if versions_dir else PROTO_VERSIONS_DIR
    try:
        model = ImportanceModel.load(active.version, versions_dir=model_dir)
        logger.info("loaded active model version %r", active.version)
    except (FileNotFoundError, ValueError):
        logger.warning("active model version %r has no saved artifact; falling back to rule-based", active.version)
        return RuleBasedImportanceScorer(), None

    try:
        prototypes = load_prototypes(active.version, versions_dir=proto_dir)
    except FileNotFoundError:
        prototypes = None
    return model, prototypes


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
    ) -> None:
        self._conn = conn
        self._scorer = scorer
        self._prototypes = prototypes
        self._embed = embed
        self._threshold = decision_threshold
        self._history: deque[ContextHistoryEntry] = deque(maxlen=history_size)
        self._ring = SavedEmbeddingRing(capacity=save_ring_capacity)
        self._drc = drc_observer if drc_observer is not None else LiveDrcObserver()
        self._raw_buffer = raw_buffer
        self._encoder: Any = None
        self._lock = threading.Lock()
        self._closed = False
        self._n_seen = 0
        self._n_stored = 0
        self._n_saved = 0

    @property
    def embedding_enabled(self) -> bool:
        return self._embed

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

        if _app_key(snapshot.context.get("app")) in NON_CONTEXT_APPS:
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
        )

        try:
            proba = self._scorer.predict_proba(features)
        except Exception:  # a scorer bug must not take the listener down mid-run
            logger.exception("seq=%d scoring failed; treating as discard", snapshot.seq)
            proba = 0.0
        decision = "save" if proba >= self._threshold else "discard"

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
        )
        expected_dim = None if embedding is None else int(embedding.shape[0])
        record_id = insert_context_record(
            self._conn, record, expected_dim=expected_dim if expected_dim else 512
        )
        self._n_stored += 1
        self._buffer_text(snapshot, event.ts, decision, proba, record_id)

        if decision == "save":
            self._n_saved += 1
            if embedding is not None:
                self._ring.add(embedding, event.ts)
            else:
                self._ring.mark_saved(event.ts)  # keeps time-since-last-save right without a frame

        self._history.append(
            ContextHistoryEntry(ts=event.ts, app=event.app, title=event.title, domain=event.domain)
        )

        logger.info(
            "seq=%d id=%d %r decision=%s p=%.3f (seen=%d stored=%d saved=%d)",
            snapshot.seq, record_id, event.app, decision, proba,
            self._n_seen, self._n_stored, self._n_saved,
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
