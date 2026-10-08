"""Connection handling, migration runner, and low-level encoders for the context store."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

import numpy as np

from .schema import MIGRATIONS, SCHEMA_MIGRATIONS_DDL

logger = logging.getLogger("worker.storage")

EMBEDDING_DTYPE = np.float32
DEFAULT_EMBEDDING_DIM = 512

PathLike = Union[str, Path]


def connect(db_path: PathLike, *, read_only: bool = False, check_same_thread: bool = True) -> sqlite3.Connection:
    """
    Open (or create) the database with sensible pragmas and row access by name.

    WAL improves read/write concurrency between the worker (writing records) and the
    label-construction / reporting tools (reading them). ``:memory:`` is accepted for tests.

    ``check_same_thread=False`` lets the connection be used from a thread other than the one that
    opened it (the IPC listener's threads). The caller must then serialize all use of it.
    """
    path = str(db_path)
    if read_only and path != ":memory:":
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, check_same_thread=check_same_thread)
    else:
        conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations in order. Returns the versions applied this call. A database that already holds
    data is backed up first (:func:`backup_before_migration`), so a migration can never lose it."""
    conn.execute(SCHEMA_MIGRATIONS_DDL)
    applied_rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    applied = {row["version"] for row in applied_rows}
    pending = [m[0] for m in MIGRATIONS if m[0] not in applied]
    if applied and pending:
        backup_before_migration(conn, max(applied), max(pending))

    newly_applied: list[int] = []
    for version, description, sql in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version in applied:
            continue
        # Each migration is atomic: its DDL and the bookkeeping row commit together, or not at all.
        # executescript() runs in autocommit mode, so the script opens the transaction itself and
        # leaves it open for the bookkeeping insert; `with conn` alone would commit each DDL statement.
        try:
            conn.executescript("BEGIN;\n" + sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?, ?, ?)",
                (version, description, now_epoch_us()),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        newly_applied.append(version)
    return newly_applied


def backup_before_migration(conn: sqlite3.Connection, from_version: int, to_version: int) -> Path | None:
    """Copy the database to ``backups/<name>.bak-before-v<to>-<time>.db`` beside it (SQLite backup API, consistent
    while open). None for an in-memory database. Raises if the copy fails, so the migration does not run."""
    row = conn.execute("PRAGMA database_list").fetchone()  # (seq, name, file) of "main"
    path = row[2] if row is not None else ""
    if not path:
        return None
    source = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = source.parent / "backups" / f"{source.stem}.bak-before-v{to_version}-{stamp}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    copy = sqlite3.connect(target)
    try:
        conn.backup(copy)
    finally:
        copy.close()
    logger.info("backed up %s (schema v%d) to %s before migrating to v%d", source, from_version, target, to_version)
    return target


def current_schema_version(conn: sqlite3.Connection) -> int:
    """Highest applied migration version, or 0 if the DB is empty/unmigrated."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return row["v"] or 0


# --------------------------------------------------------------------------- encoders

def encode_embedding(embedding: np.ndarray | None, *, expected_dim: int = DEFAULT_EMBEDDING_DIM) -> bytes | None:
    """Pack a 1-D float32 embedding into little-endian bytes, or None passes through."""
    if embedding is None:
        return None
    array = np.ascontiguousarray(embedding, dtype=EMBEDDING_DTYPE)
    if array.ndim != 1:
        raise ValueError(f"embedding must be 1-D, got shape {array.shape}")
    if expected_dim is not None and array.shape[0] != expected_dim:
        raise ValueError(f"embedding must have {expected_dim} elements, got {array.shape[0]}")
    return array.astype("<f4", copy=False).tobytes()


def decode_embedding(blob: bytes | None) -> np.ndarray | None:
    """Unpack bytes written by :func:`encode_embedding` back into a float32 array."""
    if blob is None:
        return None
    return np.frombuffer(blob, dtype="<f4").astype(EMBEDDING_DTYPE)


# --------------------------------------------------------------------------- time helpers

def to_epoch_us(ts: datetime) -> int:
    """Datetime -> integer microseconds since the Unix epoch (UTC)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.astimezone(timezone.utc).timestamp() * 1_000_000)


def from_epoch_us(value: int) -> datetime:
    """Integer microseconds since the Unix epoch -> aware UTC datetime."""
    return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)


def now_epoch_us() -> int:
    return to_epoch_us(datetime.now(timezone.utc))
