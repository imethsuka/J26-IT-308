"""The screenshot store: the frames Model 1 decided to keep, for the dashboard's history slider.

This is the "Capture to SSD" box of the architecture diagram. Until now a ``save`` decision was only recorded; with this
store switched on (``main.py --screenshots``, off by default, the dashboard asks for consent first) the frame behind each
``save`` is kept as well. Discarded frames are never written.

    * Its own file, ``screenshots.db`` beside ``context.db``: deleting it or capping it never touches the records, and
      nothing that reads ``context.db`` (training, reports) sees an image.
    * Small: scaled down to ``max_width`` and stored as JPEG (about 50-150 KB per frame at 1280 px).
    * Encrypted at rest with Windows DPAPI, current-user scope, with its own entropy (not the screen-text one). If the
      image cannot be encrypted it is not stored.
    * Time limit: frames older than ``ttl_days`` are deleted. Size limit: past ``max_mb`` the oldest go first.

What it does **not** do: the privacy filter works on text only, so a screenshot shows whatever was on screen (the whole
primary monitor, including windows behind the active one). Exclusions still apply: the collector never captures while an
excluded app or site is in the foreground, and excluded snapshots never reach this store.
"""

from __future__ import annotations

import io
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from storage.database import now_epoch_us, to_epoch_us
from storage.raw_buffer import DpapiProtector, Protector

logger = logging.getLogger("worker.screenshots")

DEFAULT_TTL_DAYS = 30.0
DEFAULT_MAX_MB = 5000.0
DEFAULT_MAX_WIDTH = 1280
# JPEG quality 1-95 (the dashboard's Settings, 50 by default; 60 before 2026-10-24).
JPEG_QUALITY = 50
SCHEMA_VERSION = 1
ENCODING = "dpapi/v1"
ENTROPY = b"context-importance/screenshots/v1"  # the dashboard uses the same value to decrypt
_MAINTENANCE_EVERY = 50

_DDL = """
CREATE TABLE IF NOT EXISTS screenshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                INTEGER NOT NULL,              -- epoch microseconds, UTC (same as context_records.ts)
    context_record_id INTEGER,                       -- context.db context_records.id (other file: not a foreign key)
    width             INTEGER NOT NULL,              -- of the stored image, after scaling
    height            INTEGER NOT NULL,
    format            TEXT    NOT NULL,              -- 'jpeg'
    encoding          TEXT    NOT NULL,
    bytes             INTEGER NOT NULL,              -- size of image_blob
    image_blob        BLOB    NOT NULL               -- protected(jpeg)
);
CREATE INDEX IF NOT EXISTS ix_shots_ts ON screenshots(ts);
CREATE INDEX IF NOT EXISTS ix_shots_record ON screenshots(context_record_id);
"""


def encode_frame(pixels: bytes, width: int, height: int, stride: int, *, max_width: int = DEFAULT_MAX_WIDTH,
                 quality: int = JPEG_QUALITY) -> tuple[bytes, int, int]:
    """BGRA32 frame bytes -> ``(jpeg bytes, width, height)``, scaled down to at most ``max_width`` pixels wide."""
    from PIL import Image

    image = Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", stride, 1)
    if width > max_width:
        image = image.resize((max_width, max(1, round(height * max_width / width))), Image.BILINEAR)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=max(1, min(95, int(quality))))
    return out.getvalue(), image.width, image.height


def screenshots_path_for(db_path: str | Path) -> Path:
    """``screenshots.db`` beside the given ``context.db``."""
    return Path(db_path).resolve().parent / "screenshots.db"


class ScreenshotStore:
    """Thread-safe, append-mostly store of encrypted frames. Creates its file on the first write."""

    def __init__(
        self,
        path: str | Path,
        *,
        protector: Protector | None = None,
        ttl_days: float = DEFAULT_TTL_DAYS,
        max_mb: float = DEFAULT_MAX_MB,
        max_width: int = DEFAULT_MAX_WIDTH,
        quality: int = JPEG_QUALITY,
    ) -> None:
        self.path = Path(path)
        self._protector = protector
        self.ttl_days = ttl_days
        self.max_bytes = int(max_mb * 1024 * 1024)
        self.max_width = max_width
        self.quality = quality
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._since_maintenance = _MAINTENANCE_EVERY  # run once on the first write

    def _connection(self, *, create: bool) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        if not create and not self.path.exists():
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")  # only takes effect on a new file: deleted frames free disk space
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        self._conn = conn
        return conn

    def _protect(self) -> Protector:
        if self._protector is None:
            self._protector = DpapiProtector(ENTROPY)
        return self._protector

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def add(
        self, pixels: bytes, *, width: int, height: int, stride: int, ts: datetime, context_record_id: int | None,
    ) -> int:
        """Encode, encrypt and store one frame. Returns its row id. Raises if it cannot be encrypted."""
        jpeg, out_w, out_h = encode_frame(pixels, width, height, stride, max_width=self.max_width, quality=self.quality)
        blob = self._protect().protect(jpeg)
        with self._lock:
            conn = self._connection(create=True)
            assert conn is not None
            cursor = conn.execute(
                "INSERT INTO screenshots(ts, context_record_id, width, height, format, encoding, bytes, image_blob) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (to_epoch_us(ts), context_record_id, out_w, out_h, "jpeg", ENCODING, len(blob), blob),
            )
            conn.commit()
            self._since_maintenance += 1
            if self._since_maintenance >= _MAINTENANCE_EVERY:
                self._since_maintenance = 0
                self._maintain(conn)
            return int(cursor.lastrowid)

    def load(self, shot_id: int) -> bytes | None:
        """The decrypted JPEG of one stored frame, or ``None``."""
        with self._lock:
            conn = self._connection(create=False)
            row = None if conn is None else conn.execute(
                "SELECT image_blob FROM screenshots WHERE id = ?", (int(shot_id),)).fetchone()
        return None if row is None else self._protect().unprotect(bytes(row["image_blob"]))

    # -- limits ------------------------------------------------------------------------------------

    def _maintain(self, conn: sqlite3.Connection, now: datetime | None = None) -> None:
        now_us = to_epoch_us(now) if now is not None else now_epoch_us()
        expired = conn.execute(
            "DELETE FROM screenshots WHERE ts < ?", (now_us - int(self.ttl_days * 86_400 * 1_000_000),)).rowcount
        total = conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM screenshots").fetchone()[0]
        dropped = 0
        if total > self.max_bytes:
            for row in conn.execute("SELECT id, bytes FROM screenshots ORDER BY ts ASC, id ASC").fetchall():
                if total <= self.max_bytes:
                    break
                conn.execute("DELETE FROM screenshots WHERE id = ?", (row["id"],))
                total -= row["bytes"]
                dropped += 1
        conn.commit()
        if expired or dropped:
            conn.execute("PRAGMA incremental_vacuum")
            logger.info("screenshots: %d expired and %d over the size cap deleted", expired, dropped)

    def maintain(self, now: datetime | None = None) -> None:
        with self._lock:
            conn = self._connection(create=False)
            if conn is not None:
                self._maintain(conn, now)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return {"shots": 0, "bytes": 0}
            row = conn.execute("SELECT COUNT(*) AS shots, COALESCE(SUM(bytes), 0) AS bytes FROM screenshots").fetchone()
            return dict(row)
