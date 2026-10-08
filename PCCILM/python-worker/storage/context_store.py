"""Insert and query functions for context records and model versions.

These wrap the SQL so callers work with dataclasses and datetimes, not rows and epoch integers.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from .database import (
    DEFAULT_EMBEDDING_DIM,
    decode_embedding,
    encode_embedding,
    from_epoch_us,
    now_epoch_us,
    to_epoch_us,
)

Decision = str  # 'save' or 'discard'


@dataclass
class ContextRecord:
    ts: datetime
    decision: Decision
    features: dict[str, Any]
    embedding: np.ndarray | None = None
    app: str | None = None
    domain: str | None = None
    title: str | None = None
    label: int | None = None
    labeled_at: datetime | None = None
    label_version: str | None = None
    model_version_id: int | None = None
    id: int | None = None
    created_at: datetime | None = None
    # The frozen DRC rules' decision for this snapshot (models.drc_rules). None = not recorded.
    drc_would_capture: bool | None = None
    drc_trigger: str | None = None


@dataclass
class ModelVersion:
    version: str
    algorithm: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    is_active: bool = False
    id: int | None = None
    created_at: datetime | None = None


# --------------------------------------------------------------------------- context records

def insert_context_record(conn: sqlite3.Connection, record: ContextRecord, *, expected_dim: int = DEFAULT_EMBEDDING_DIM) -> int:
    """Insert one context record, returning its new id. Commits."""
    embedding_blob = encode_embedding(record.embedding, expected_dim=expected_dim)
    embedding_dim = None if record.embedding is None else int(np.asarray(record.embedding).shape[0])
    created_at = to_epoch_us(record.created_at) if record.created_at else now_epoch_us()

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO context_records
                (ts, app, domain, title, decision, features_json, embedding, embedding_dim,
                 label, labeled_at, label_version, model_version_id, created_at,
                 drc_would_capture, drc_trigger)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                to_epoch_us(record.ts),
                record.app,
                record.domain,
                record.title,
                record.decision,
                json.dumps(record.features, separators=(",", ":")),
                embedding_blob,
                embedding_dim,
                record.label,
                to_epoch_us(record.labeled_at) if record.labeled_at else None,
                record.label_version,
                record.model_version_id,
                created_at,
                None if record.drc_would_capture is None else int(record.drc_would_capture),
                record.drc_trigger,
            ),
        )
    return int(cursor.lastrowid)


def get_context_record(conn: sqlite3.Connection, record_id: int) -> ContextRecord | None:
    row = conn.execute("SELECT * FROM context_records WHERE id = ?", (record_id,)).fetchone()
    return _row_to_record(row) if row else None


def query_unlabelled_older_than(
    conn: sqlite3.Connection,
    hours: float,
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> list[ContextRecord]:
    """
    The label-construction phase's query: unlabelled records whose capture time is older than
    ``hours`` hours before ``now`` (default: current UTC), oldest first. Served by the partial
    index ``ix_context_unlabelled``.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = to_epoch_us(reference - timedelta(hours=hours))
    sql = "SELECT * FROM context_records WHERE label IS NULL AND ts < ? ORDER BY ts ASC"
    params: list[Any] = [cutoff]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_row_to_record(row) for row in conn.execute(sql, params).fetchall()]


def set_label(
    conn: sqlite3.Connection,
    record_id: int,
    label: int,
    *,
    labeled_at: datetime | None = None,
    label_version: str | None = None,
) -> None:
    """Assign a ground-truth label, moving the row out of the unlabelled index. Commits.

    ``label_version`` identifies the labelling rule + horizon that produced ``label`` (see
    ``adaptation.label_construction.LabelConstructor.label_version``).
    """
    stamp = to_epoch_us(labeled_at) if labeled_at else now_epoch_us()
    with conn:
        conn.execute(
            "UPDATE context_records SET label = ?, labeled_at = ?, label_version = ? WHERE id = ?",
            (label, stamp, label_version, record_id),
        )


def count_context_records(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM context_records").fetchone()["c"]


def list_context_records(
    conn: sqlite3.Connection,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[ContextRecord]:
    """All context records in ascending time order (optionally from ``since``). Used to build the
    activity timeline for label construction."""
    sql = "SELECT * FROM context_records"
    params: list[Any] = []
    if since is not None:
        sql += " WHERE ts >= ?"
        params.append(to_epoch_us(since))
    sql += " ORDER BY ts ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_row_to_record(row) for row in conn.execute(sql, params).fetchall()]


@dataclass
class TimelineEntry:
    id: int
    ts: datetime
    app: str | None
    domain: str | None
    title: str | None
    decision: Decision


def list_timeline(conn: sqlite3.Connection, *, since: datetime) -> list[TimelineEntry]:
    """Records from ``since`` in time order, without features or embeddings, so a week of them loads quickly.
    The worker rebuilds its visit log and app-switch history from it at startup."""
    rows = conn.execute(
        "SELECT id, ts, app, domain, title, decision FROM context_records WHERE ts >= ? ORDER BY ts ASC, id ASC",
        (to_epoch_us(since),),
    ).fetchall()
    return [TimelineEntry(r["id"], from_epoch_us(r["ts"]), r["app"], r["domain"], r["title"], r["decision"]) for r in rows]


def features_by_id(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict[str, Any]]:
    """The stored feature dicts of ``ids``."""
    found: dict[int, dict[str, Any]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(f"SELECT id, features_json FROM context_records WHERE id IN ({marks})", chunk):
            found[row["id"]] = json.loads(row["features_json"])
    return found


def scoring_inputs_by_id(conn: sqlite3.Connection, ids: list[int]) -> dict[int, tuple[dict[str, Any], np.ndarray | None]]:
    """``(features, embedding)`` of ``ids``, for rescoring stored records with a model that also uses the embedding."""
    found: dict[int, tuple[dict[str, Any], np.ndarray | None]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(f"SELECT id, features_json, embedding FROM context_records WHERE id IN ({marks})", chunk):
            found[row["id"]] = (json.loads(row["features_json"]), decode_embedding(row["embedding"]))
    return found


def latest_saves(conn: sqlite3.Connection, limit: int) -> list[tuple[datetime, np.ndarray | None]]:
    """The last ``limit`` saved records as ``(ts, embedding)``, oldest first (to refill the novelty ring)."""
    rows = conn.execute(
        "SELECT ts, embedding FROM context_records WHERE decision = 'save' ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [(from_epoch_us(r["ts"]), decode_embedding(r["embedding"])) for r in reversed(rows)]


# --------------------------------------------------------------------------- model decisions (champion + shadows)

@dataclass
class ModelDecision:
    """What one model said about one context (``model_decisions``, schema v4)."""
    record_id: int
    model: str
    algorithm: str
    role: str  # 'champion' or 'shadow'
    ts: datetime
    score: float
    would_save: bool
    cutoff: float | None = None
    model_version_id: int | None = None
    latency_us: int | None = None


def insert_model_decisions(conn: sqlite3.Connection, decisions: list[ModelDecision]) -> None:
    """Append decisions. Commits. A repeat of (record, model) is ignored: what was recorded first stands."""
    if not decisions:
        return
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO model_decisions (record_id, model, algorithm, model_version_id, role, ts, score, cutoff, "
            "would_save, latency_us) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(d.record_id, d.model, d.algorithm, d.model_version_id, d.role, to_epoch_us(d.ts), float(d.score),
              None if d.cutoff is None else float(d.cutoff), 1 if d.would_save else 0, d.latency_us) for d in decisions],
        )


def list_model_decisions(conn: sqlite3.Connection, record_id: int) -> list[ModelDecision]:
    rows = conn.execute("SELECT * FROM model_decisions WHERE record_id = ? ORDER BY role, model", (record_id,)).fetchall()
    return [ModelDecision(r["record_id"], r["model"], r["algorithm"], r["role"], from_epoch_us(r["ts"]), r["score"],
                          bool(r["would_save"]), r["cutoff"], r["model_version_id"], r["latency_us"]) for r in rows]


def recent_scores(conn: sqlite3.Connection, model: str, limit: int) -> list[float]:
    """The last ``limit`` scores ``model`` recorded, oldest first (to refill its rolling cutoff after a restart)."""
    rows = conn.execute(
        "SELECT score FROM model_decisions WHERE model = ? ORDER BY ts DESC LIMIT ?", (model, limit)).fetchall()
    return [float(r["score"]) for r in reversed(rows)]


# --------------------------------------------------------------------------- champion

def record_champion_event(conn: sqlite3.Connection, *, from_version: str | None, to_version: str,
                          algorithm: str | None, reason: str, ts: datetime | None = None) -> None:
    """Note a change of champion. Commits."""
    with conn:
        conn.execute(
            "INSERT INTO champion_events (ts, from_version, to_version, algorithm, reason) VALUES (?,?,?,?,?)",
            (to_epoch_us(ts) if ts else now_epoch_us(), from_version, to_version, algorithm, reason),
        )


def set_champion(conn: sqlite3.Connection, version: str, *, reason: str) -> ModelVersion:
    """Make ``version`` the champion (the active model) and record the change. Nothing is deleted: the previous
    champion stays a saved version and can be made champion again the same way."""
    target = get_model_version(conn, version)
    if target is None:
        raise ValueError(f"no model version {version!r}")
    previous = get_active_model_version(conn)
    set_active_model_version(conn, target.id)
    record_champion_event(conn, from_version=previous.version if previous else None, to_version=version,
                          algorithm=target.algorithm, reason=reason)
    return get_model_version(conn, version)


def latest_versions_by_algorithm(conn: sqlite3.Connection) -> dict[str, ModelVersion]:
    """The newest saved version of each algorithm (NULL algorithm = LightGBM, as in rows from before challengers)."""
    latest: dict[str, ModelVersion] = {}
    for version in list_model_versions(conn):  # oldest first, so later ones win
        latest[version.algorithm or "lightgbm"] = version
    return latest


# --------------------------------------------------------------------------- model versions

def insert_model_version(conn: sqlite3.Connection, version: ModelVersion) -> int:
    """Insert a model version, returning its id. If ``is_active``, it becomes the sole active one. Commits."""
    created_at = to_epoch_us(version.created_at) if version.created_at else now_epoch_us()
    with conn:
        if version.is_active:
            conn.execute("UPDATE model_versions SET is_active = 0 WHERE is_active = 1")
        cursor = conn.execute(
            """
            INSERT INTO model_versions (version, algorithm, created_at, is_active, metrics_json, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                version.version,
                version.algorithm,
                created_at,
                1 if version.is_active else 0,
                json.dumps(version.metrics, separators=(",", ":")) if version.metrics else None,
                version.notes,
            ),
        )
    return int(cursor.lastrowid)


def set_active_model_version(conn: sqlite3.Connection, version_id: int) -> None:
    """Make ``version_id`` the single active model version. Commits."""
    with conn:
        conn.execute("UPDATE model_versions SET is_active = 0 WHERE is_active = 1")
        conn.execute("UPDATE model_versions SET is_active = 1 WHERE id = ?", (version_id,))


def get_active_model_version(conn: sqlite3.Connection) -> ModelVersion | None:
    row = conn.execute("SELECT * FROM model_versions WHERE is_active = 1").fetchone()
    return _row_to_model_version(row) if row else None


def get_model_version(conn: sqlite3.Connection, version: str) -> ModelVersion | None:
    """Look up a model version by its (unique) version name."""
    row = conn.execute("SELECT * FROM model_versions WHERE version = ?", (version,)).fetchone()
    return _row_to_model_version(row) if row else None


def list_model_versions(conn: sqlite3.Connection) -> list[ModelVersion]:
    rows = conn.execute("SELECT * FROM model_versions ORDER BY created_at ASC, id ASC").fetchall()
    return [_row_to_model_version(row) for row in rows]


# --------------------------------------------------------------------------- row mappers

def _row_to_record(row: sqlite3.Row) -> ContextRecord:
    return ContextRecord(
        id=row["id"],
        ts=from_epoch_us(row["ts"]),
        app=row["app"],
        domain=row["domain"],
        title=row["title"],
        decision=row["decision"],
        features=json.loads(row["features_json"]),
        embedding=decode_embedding(row["embedding"]),
        label=row["label"],
        labeled_at=from_epoch_us(row["labeled_at"]) if row["labeled_at"] is not None else None,
        label_version=row["label_version"],
        model_version_id=row["model_version_id"],
        created_at=from_epoch_us(row["created_at"]),
        drc_would_capture=None if row["drc_would_capture"] is None else bool(row["drc_would_capture"]),
        drc_trigger=row["drc_trigger"],
    )


def _row_to_model_version(row: sqlite3.Row) -> ModelVersion:
    return ModelVersion(
        id=row["id"],
        version=row["version"],
        algorithm=row["algorithm"],
        metrics=json.loads(row["metrics_json"]) if row["metrics_json"] else {},
        notes=row["notes"],
        is_active=bool(row["is_active"]),
        created_at=from_epoch_us(row["created_at"]),
    )
