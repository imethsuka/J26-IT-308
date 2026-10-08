"""The raw buffer: a separate, short-lived SQLite file holding the screen text the collector read.

It is deliberately **not** ``context.db``. The text is sensitive, bulky and temporary, so it lives in its own
file (``raw_buffer.db`` beside ``context.db``): deleting it, capping it or losing it cannot touch the labelled
records, and nothing that reads ``context.db`` (the dashboard, reports, training) can see it.

    * Encrypted at rest: each capture's text (and image alt texts) is compressed with zlib, then protected with
      Windows DPAPI in the current-user scope (``DpapiProtector``, ctypes, no extra dependency). Only code
      running as the same Windows user can decrypt it. Compression comes first, as encrypted bytes don't compress.
    * Time limit: rows older than ``ttl_days`` (default 7; the dashboard passes the user's setting) are deleted
      whether processed or not.
    * Size limit: when the stored text passes ``max_text_mb``, the text of the *lowest-importance* rows is dropped
      first; their metadata row stays so the timeline is still known.
    * Fail closed: if the text cannot be encrypted, it is not stored (nothing is ever written in plain text).

The metadata columns (app, domain, title, activity counts) are the same things ``context.db`` already stores in
the clear. The file is created on the first capture that has text, so a user who never enables screen text never
gets one.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import threading
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from features.titles import normalize_title
from sensing.protocol import ScreenText
from storage.database import from_epoch_us, now_epoch_us, to_epoch_us

logger = logging.getLogger("worker.raw_buffer")

DEFAULT_TTL_DAYS = 7.0
DEFAULT_MAX_TEXT_MB = 200.0
RAW_SCHEMA_VERSION = 1
ENCODING = "dpapi+zlib/v1"
_MAINTENANCE_EVERY = 100  # inserts between TTL / size-cap passes
_KEY_SEP = "\x1f"
_ENTROPY = b"context-importance/raw-buffer/v1"  # extra DPAPI secret so a bare CryptUnprotectData can't open it


class Protector(Protocol):
    """Encrypts bytes at rest. The real one is DPAPI; tests inject a reversible fake."""

    name: str

    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, data: bytes) -> bytes: ...


class DpapiProtector:
    """Windows Data Protection API, current-user scope: only this Windows user can decrypt."""

    name = "dpapi"

    def __init__(self, entropy: bytes = _ENTROPY) -> None:
        if sys.platform != "win32":
            raise RuntimeError("DPAPI is only available on Windows")
        self._entropy = entropy

    def _call(self, function_name: str, data: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        function = getattr(crypt32, function_name)
        function.restype = wintypes.BOOL

        def blob(raw: bytes) -> tuple[Blob, Any]:
            buffer = ctypes.create_string_buffer(raw, len(raw))  # keep a reference alive for the call
            return Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

        source, keep_source = blob(data)
        entropy, keep_entropy = blob(self._entropy)
        out = Blob()
        # CryptProtectData / CryptUnprotectData(in, descr, entropy, reserved, prompt, flags, out): flags 0 =
        # current-user scope, no UI.
        ok = function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out))
        del keep_source, keep_entropy
        if not ok:
            raise OSError(f"{function_name} failed (error {ctypes.get_last_error()})")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call("CryptProtectData", data)

    def unprotect(self, data: bytes) -> bytes:
        return self._call("CryptUnprotectData", data)


def default_protector() -> Protector:
    return DpapiProtector()


def make_context_key(app: str | None, domain: str | None, title: str | None) -> str:
    """The context key used to group captures into episodes: ``(app, domain, normalized title)``,
    the same identity the v4 labels use (``features.titles.normalize_title``)."""
    name = (app or "").strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return _KEY_SEP.join((name, (domain or "").strip().lower(), normalize_title(title) or ""))


@dataclass(frozen=True)
class RawCapture:
    """One buffered capture, with its text decrypted."""

    id: int
    ts: datetime
    app: str | None
    domain: str | None
    title: str | None
    context_key: str
    context_record_id: int | None
    decision: str | None
    score: float | None
    is_idle: bool
    key_presses: int
    mouse_clicks: int
    mouse_scrolls: int
    mouse_moves: int
    source: str
    chars: int
    truncated: bool
    text: str | None  # None once the size cap dropped it
    images: tuple[dict[str, Any], ...]


_DDL = """
CREATE TABLE IF NOT EXISTS raw_captures (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                INTEGER NOT NULL,              -- epoch microseconds, UTC (same as context_records.ts)
    app               TEXT,
    domain            TEXT,
    title             TEXT,
    context_key       TEXT    NOT NULL,
    context_record_id INTEGER,                       -- context.db context_records.id (not a foreign key: other file)
    decision          TEXT,
    score             REAL,                          -- Model 1 probability; the size cap drops low scores first
    is_idle           INTEGER NOT NULL DEFAULT 0,
    key_presses       INTEGER NOT NULL DEFAULT 0,
    mouse_clicks      INTEGER NOT NULL DEFAULT 0,
    mouse_scrolls     INTEGER NOT NULL DEFAULT 0,
    mouse_moves       INTEGER NOT NULL DEFAULT 0,
    source            TEXT    NOT NULL,
    chars             INTEGER NOT NULL,
    truncated         INTEGER NOT NULL DEFAULT 0,
    encoding          TEXT    NOT NULL,
    text_blob         BLOB,                          -- protected(zlib(json{text, images})); NULL once dropped
    processed         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_raw_ts ON raw_captures(ts);
CREATE INDEX IF NOT EXISTS ix_raw_key_ts ON raw_captures(context_key, ts);
CREATE INDEX IF NOT EXISTS ix_raw_pending ON raw_captures(processed, ts);
"""


class RawBuffer:
    """Thread-safe store for raw screen captures. Opens (and creates) its file on the first write."""

    def __init__(
        self,
        path: str | Path,
        *,
        protector: Protector | None = None,
        ttl_days: float = DEFAULT_TTL_DAYS,
        max_text_mb: float = DEFAULT_MAX_TEXT_MB,
    ) -> None:
        self.path = Path(path)
        self._protector = protector
        self.ttl_days = ttl_days
        self.max_text_bytes = int(max_text_mb * 1024 * 1024)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._since_maintenance = _MAINTENANCE_EVERY  # run once on the first write

    # -- connection --------------------------------------------------------------------------------

    def _connection(self, *, create: bool) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        if not create and not self.path.exists():
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {RAW_SCHEMA_VERSION}")
        conn.commit()
        self._conn = conn
        return conn

    def _protector_or_default(self) -> Protector:
        if self._protector is None:
            self._protector = default_protector()
        return self._protector

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- write -------------------------------------------------------------------------------------

    def add(
        self,
        text: ScreenText,
        *,
        ts: datetime,
        app: str | None,
        domain: str | None,
        title: str | None,
        decision: str | None = None,
        score: float | None = None,
        activity: dict[str, Any] | None = None,
        is_idle: bool = False,
        context_record_id: int | None = None,
    ) -> int | None:
        """Buffer one capture's text. Returns its row id, or ``None`` if there was nothing to keep.

        Raises if the text cannot be encrypted: it is never stored unprotected.
        """
        if not text.content.strip() and not text.images:
            return None
        payload = json.dumps(
            {"text": text.content, "images": [
                {"x": i.x, "y": i.y, "width": i.width, "height": i.height, "alt": i.alt} for i in text.images
            ]},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        blob = self._protector_or_default().protect(zlib.compress(payload, 6))
        activity = activity or {}

        with self._lock:
            conn = self._connection(create=True)
            assert conn is not None
            cursor = conn.execute(
                """INSERT INTO raw_captures(ts, app, domain, title, context_key, context_record_id, decision, score,
                       is_idle, key_presses, mouse_clicks, mouse_scrolls, mouse_moves, source, chars, truncated,
                       encoding, text_blob)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    to_epoch_us(ts), app, domain, title, make_context_key(app, domain, title), context_record_id,
                    decision, score, int(is_idle),
                    int(activity.get("key_presses", 0)), int(activity.get("mouse_clicks", 0)),
                    int(activity.get("mouse_scrolls", 0)), int(activity.get("mouse_moves", 0)),
                    text.source, text.chars, int(text.truncated), ENCODING, blob,
                ),
            )
            conn.commit()
            self._since_maintenance += 1
            if self._since_maintenance >= _MAINTENANCE_EVERY:
                self._since_maintenance = 0
                self._maintain(conn)
            return int(cursor.lastrowid)

    # -- limits ------------------------------------------------------------------------------------

    def _maintain(self, conn: sqlite3.Connection) -> None:
        expired = self._purge_expired(conn)
        dropped = self._enforce_size_cap(conn)
        if expired or dropped:
            logger.info("raw buffer maintenance: %d expired row(s) deleted, text dropped from %d row(s)", expired, dropped)

    def purge_expired(self, now: datetime | None = None) -> int:
        """Delete rows older than ``ttl_days``. Returns how many."""
        with self._lock:
            conn = self._connection(create=False)
            return 0 if conn is None else self._purge_expired(conn, now)

    def _purge_expired(self, conn: sqlite3.Connection, now: datetime | None = None) -> int:
        now_us = to_epoch_us(now) if now is not None else now_epoch_us()
        cutoff = now_us - int(self.ttl_days * 86_400 * 1_000_000)
        cursor = conn.execute("DELETE FROM raw_captures WHERE ts < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount

    def enforce_size_cap(self) -> int:
        """Drop the text of the lowest-importance rows until the stored text fits. Returns rows changed."""
        with self._lock:
            conn = self._connection(create=False)
            return 0 if conn is None else self._enforce_size_cap(conn)

    def _enforce_size_cap(self, conn: sqlite3.Connection) -> int:
        total = conn.execute("SELECT COALESCE(SUM(LENGTH(text_blob)), 0) FROM raw_captures").fetchone()[0]
        if total <= self.max_text_bytes:
            return 0
        changed = 0
        # Lowest score first (unscored rows count as lowest), oldest first among equals.
        rows = conn.execute(
            "SELECT id, LENGTH(text_blob) AS n FROM raw_captures WHERE text_blob IS NOT NULL "
            "ORDER BY COALESCE(score, -1.0) ASC, ts ASC"
        ).fetchall()
        for row in rows:
            if total <= self.max_text_bytes:
                break
            conn.execute("UPDATE raw_captures SET text_blob = NULL WHERE id = ?", (row["id"],))
            total -= row["n"]
            changed += 1
        conn.commit()
        return changed

    # -- read --------------------------------------------------------------------------------------

    def _decode(self, row: sqlite3.Row) -> RawCapture:
        text: str | None = None
        images: tuple[dict[str, Any], ...] = ()
        if row["text_blob"] is not None:
            payload = json.loads(zlib.decompress(self._protector_or_default().unprotect(bytes(row["text_blob"]))))
            text = payload["text"]
            images = tuple(payload.get("images") or ())
        return RawCapture(
            id=row["id"], ts=from_epoch_us(row["ts"]), app=row["app"], domain=row["domain"], title=row["title"],
            context_key=row["context_key"], context_record_id=row["context_record_id"], decision=row["decision"],
            score=row["score"], is_idle=bool(row["is_idle"]), key_presses=row["key_presses"],
            mouse_clicks=row["mouse_clicks"], mouse_scrolls=row["mouse_scrolls"], mouse_moves=row["mouse_moves"],
            source=row["source"], chars=row["chars"], truncated=bool(row["truncated"]), text=text, images=images,
        )

    def iter_unprocessed(self, *, limit: int | None = None) -> Iterator[RawCapture]:
        """Captures not yet processed by the deferred job, oldest first, with their text decrypted."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return iter(())
            sql = "SELECT * FROM raw_captures WHERE processed = 0 ORDER BY ts ASC, id ASC"
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            return iter([self._decode(row) for row in conn.execute(sql).fetchall()])

    def mark_processed(self, ids: list[int]) -> None:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None or not ids:
                return
            conn.executemany("UPDATE raw_captures SET processed = 1 WHERE id = ?", [(i,) for i in ids])
            conn.commit()

    def delete(self, ids: list[int]) -> int:
        """Delete processed rows ("the deferred job deletes rows as soon as it has processed them")."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None or not ids:
                return 0
            conn.executemany("DELETE FROM raw_captures WHERE id = ?", [(i,) for i in ids])
            conn.commit()
            return len(ids)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return {"rows": 0, "with_text": 0, "unprocessed": 0, "text_bytes": 0}
            row = conn.execute(
                "SELECT COUNT(*) AS rows, COUNT(text_blob) AS with_text, "
                "COALESCE(SUM(processed = 0), 0) AS unprocessed, COALESCE(SUM(LENGTH(text_blob)), 0) AS text_bytes "
                "FROM raw_captures"
            ).fetchone()
            return dict(row)


def raw_buffer_path_for(db_path: str | Path) -> Path:
    """``raw_buffer.db`` beside the given ``context.db``."""
    return Path(db_path).resolve().parent / "raw_buffer.db"
