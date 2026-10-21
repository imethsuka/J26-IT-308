"""The memory store: a second separate SQLite file, ``memory.db``, for what the deferred job produces.

Where each kind of data lives (docs/SCREEN_TEXT_PLAN.md section 10):

    context.db     scored context records (metadata, no text)         -> training, dashboard
    raw_buffer.db  raw screen text, encrypted, deleted after 3 days    -> input of the deferred job only
    memory.db      **privacy-filtered** merged pages, episodes, entries -> Members 3 and 4 (search, consolidation)

Only text that has been through a ``PrivacyFilter`` is written here. The merged page text (``documents.fragments_blob``)
is also protected with the same DPAPI step as the raw buffer, as defence in depth: a filter can miss something. Entries
carry only metadata and, later, a summary; they hold no screen text. The file is created on the first write.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from features.text_merge import TextDocument
from storage.database import now_epoch_us, to_epoch_us
from storage.raw_buffer import Protector, default_protector

MEMORY_SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id           TEXT PRIMARY KEY,
    context_key      TEXT NOT NULL,
    app              TEXT,
    domain           TEXT,
    title            TEXT,
    visits           INTEGER NOT NULL DEFAULT 0,
    word_count       INTEGER NOT NULL DEFAULT 0,
    fragments_blob   BLOB,                   -- protected(zlib(json)) of the filtered merged text; NULL if none kept
    filter_version   TEXT,
    first_seen       INTEGER NOT NULL,
    last_seen        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id           TEXT NOT NULL REFERENCES documents(doc_id),
    start_ts         INTEGER NOT NULL,
    end_ts           INTEGER NOT NULL,
    visit_number     INTEGER NOT NULL,
    n_captures       INTEGER NOT NULL,
    active_seconds   REAL NOT NULL,
    key_presses      INTEGER NOT NULL,
    mouse_clicks     INTEGER NOT NULL,
    mouse_scrolls    INTEGER NOT NULL,
    mouse_moves      INTEGER NOT NULL,
    max_score        REAL,
    words_seen       INTEGER NOT NULL,
    words_added      INTEGER NOT NULL,
    truncated_captures INTEGER NOT NULL,
    tier             TEXT NOT NULL,          -- high | medium | low
    created_at       INTEGER NOT NULL,
    UNIQUE (doc_id, start_ts)                -- makes reprocessing the same episode a no-op
);
CREATE TABLE IF NOT EXISTS memory_entries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id       INTEGER NOT NULL UNIQUE REFERENCES episodes(id),
    doc_id           TEXT NOT NULL,
    created_at       INTEGER NOT NULL,
    tier             TEXT NOT NULL,
    importance       REAL,
    entry_text       TEXT NOT NULL,
    entry_json       TEXT NOT NULL,
    summary          TEXT,                   -- filled later by the local-LLM stage
    summary_model    TEXT,
    needs_summary    INTEGER NOT NULL DEFAULT 0,
    text_persisted   INTEGER NOT NULL DEFAULT 0,
    filter_version   TEXT
);
CREATE INDEX IF NOT EXISTS ix_episodes_doc ON episodes(doc_id, start_ts);
CREATE INDEX IF NOT EXISTS ix_entries_created ON memory_entries(created_at);
-- Written by the rollup stage (adaptation/rollups.py). `key` is a hash of the entry ids in the session, so a session that
-- grows gets a new key and a new note; the dashboard groups entries the same way and looks notes up by that key.
CREATE TABLE IF NOT EXISTS session_notes (
    key        TEXT PRIMARY KEY,
    day        TEXT NOT NULL,
    first_ts   INTEGER NOT NULL,
    last_ts    INTEGER NOT NULL,
    n_entries  INTEGER NOT NULL,
    title      TEXT,
    tag        TEXT,
    tldr       TEXT,
    model      TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS day_notes (
    day          TEXT PRIMARY KEY,
    sessions_key TEXT NOT NULL,                -- hash of the session keys the recap was written from
    tldr         TEXT,
    model        TEXT,
    created_at   INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class StoredDocument:
    doc_id: str
    visits: int
    document: TextDocument
    filter_version: str | None


class MemoryStore:
    """Thread-safe store for filtered documents, episodes and memory entries. Opens (creates) its file on first write."""

    def __init__(self, path: str | Path, *, protector: Protector | None = None) -> None:
        self.path = Path(path)
        self._protector = protector
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

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
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {MEMORY_SCHEMA_VERSION}")
        conn.commit()
        self._conn = conn
        return conn

    def _protect(self) -> Protector:
        if self._protector is None:
            self._protector = default_protector()
        return self._protector

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- read --------------------------------------------------------------------------------------

    def load_document(self, doc_id: str) -> StoredDocument | None:
        with self._lock:
            conn = self._connection(create=False)
            row = None if conn is None else conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
            if row is None:
                return None
            document = TextDocument()
            if row["fragments_blob"] is not None:
                data = json.loads(zlib.decompress(self._protect().unprotect(bytes(row["fragments_blob"]))))
                document = TextDocument.from_dict(data)
            return StoredDocument(doc_id, row["visits"], document, row["filter_version"])

    def episode_exists(self, doc_id: str, start: datetime) -> bool:
        with self._lock:
            conn = self._connection(create=False)
            return conn is not None and conn.execute(
                "SELECT 1 FROM episodes WHERE doc_id = ? AND start_ts = ?", (doc_id, to_epoch_us(start))
            ).fetchone() is not None

    def entries(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            sql = "SELECT * FROM memory_entries ORDER BY created_at ASC, id ASC"
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def pending_summaries(self, *, limit: int) -> list[dict[str, Any]]:
        """Entries that kept page text and are waiting for a summary, newest first: id, doc_id and the parsed entry_json.

        Entries whose text was stored by the unredacted developer filter are left out: the model must only read filtered text."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT e.id, e.doc_id, e.entry_json FROM memory_entries e WHERE e.needs_summary = 1 AND e.text_persisted = 1 "
                "AND NOT EXISTS (SELECT 1 FROM memory_entries x WHERE x.doc_id = e.doc_id "
                "AND COALESCE(x.filter_version, '') LIKE 'passthrough%') "  # never read a page that held unfiltered text
                "ORDER BY e.created_at DESC, e.id DESC LIMIT ?", (int(limit),)).fetchall()
            return [{"id": r["id"], "doc_id": r["doc_id"], "entry": json.loads(r["entry_json"])} for r in rows]

    def set_summary(self, entry_id: int, summary: str | None, model: str | None) -> None:
        """Store the summary and clear the waiting flag. ``summary=None`` gives up on this entry (it keeps its plain sentence)."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return
            with conn:
                conn.execute("UPDATE memory_entries SET summary = ?, summary_model = ?, needs_summary = 0 WHERE id = ?",
                             (summary, model if summary else None, int(entry_id)))

    # -- rollups: sessions and days ------------------------------------------------------------------------

    def rollup_entries(self, *, since_us: int) -> list[dict[str, Any]]:
        """High and medium entries from ``since_us`` on (low ones are noise for a narrative), oldest first."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT e.id, e.tier, e.importance, e.entry_json, e.summary, e.text_persisted FROM memory_entries e "
                "JOIN episodes p ON p.id = e.episode_id WHERE e.tier IN ('high', 'medium') AND p.start_ts >= ? "
                "ORDER BY p.start_ts ASC, e.id ASC", (int(since_us),)).fetchall()
            return [{"id": r["id"], "tier": r["tier"], "importance": r["importance"], "entry": json.loads(r["entry_json"]),
                     "summary": r["summary"], "text_persisted": bool(r["text_persisted"])} for r in rows]

    def site_pages(self, app: str | None, domain: str | None, *, limit: int = 60) -> list[list[str]]:
        """The lines of the newest stored pages of the same site (same domain, or same app when there is no domain), for finding
        text a whole site repeats. Decrypts up to ``limit`` pages."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            if domain and domain != "-":
                rows = conn.execute("SELECT doc_id FROM documents WHERE fragments_blob IS NOT NULL AND domain = ? "
                                    "ORDER BY last_seen DESC LIMIT ?", (domain, limit)).fetchall()
            else:
                rows = conn.execute("SELECT doc_id FROM documents WHERE fragments_blob IS NOT NULL AND app = ? "
                                    "AND (domain IS NULL OR domain = '' OR domain = '-') ORDER BY last_seen DESC LIMIT ?",
                                    (app, limit)).fetchall()
        pages = []
        for r in rows:
            stored = self.load_document(r["doc_id"])
            if stored is not None:
                pages.append(stored.document.text().splitlines())
        return pages

    def session_note_keys(self) -> set[str]:
        with self._lock:
            conn = self._connection(create=False)
            return set() if conn is None else {r[0] for r in conn.execute("SELECT key FROM session_notes")}

    def save_session_note(self, *, key: str, day: str, first_ts: int, last_ts: int, n_entries: int,
                          title: str | None, tag: str | None, tldr: str | None, model: str | None) -> None:
        with self._lock:
            conn = self._connection(create=True)
            assert conn is not None
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO session_notes(key, day, first_ts, last_ts, n_entries, title, tag, tldr, model, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", (key, day, first_ts, last_ts, n_entries, title, tag, tldr, model, now_epoch_us()))

    def get_session_notes(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        if not keys:
            return {}
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return {}
            marks = ",".join("?" * len(keys))
            rows = conn.execute(f"SELECT key, title, tag, tldr FROM session_notes WHERE key IN ({marks})", keys).fetchall()
            return {r["key"]: {"title": r["title"], "tag": r["tag"], "tldr": r["tldr"]} for r in rows}

    def day_note_key(self, day: str) -> str | None:
        with self._lock:
            conn = self._connection(create=False)
            row = None if conn is None else conn.execute("SELECT sessions_key FROM day_notes WHERE day = ?", (day,)).fetchone()
            return None if row is None else row[0]

    def save_day_note(self, *, day: str, sessions_key: str, tldr: str | None, model: str | None) -> None:
        with self._lock:
            conn = self._connection(create=True)
            assert conn is not None
            with conn:
                conn.execute("INSERT OR REPLACE INTO day_notes(day, sessions_key, tldr, model, created_at) VALUES (?,?,?,?,?)",
                             (day, sessions_key, tldr, model, now_epoch_us()))

    def prune_notes(self, *, before_us: int) -> None:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return
            with conn:
                conn.execute("DELETE FROM session_notes WHERE last_ts < ?", (int(before_us),))
                conn.execute("DELETE FROM day_notes WHERE created_at < ?", (int(before_us),))

    # -- one-time clean-up of text stored without a real filter -----------------------------------------------

    def documents_to_refilter(self) -> list[dict[str, Any]]:
        """Pages that hold text and were stored without a real filter (or that ever held unfiltered text)."""
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT d.doc_id, d.app, d.domain, d.title FROM documents d WHERE d.fragments_blob IS NOT NULL AND ("
                "d.filter_version IS NULL OR d.filter_version LIKE 'passthrough%' OR EXISTS (SELECT 1 FROM memory_entries x "
                "WHERE x.doc_id = d.doc_id AND COALESCE(x.filter_version, '') LIKE 'passthrough%')) ORDER BY d.last_seen DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def replace_document_text(self, doc_id: str, document: TextDocument | None, filter_version: str | None) -> None:
        """Swap a page's text for its filtered version in one transaction. ``None`` drops the text (the page was blocked):
        its entries then no longer claim to hold text and stop waiting for a summary."""
        blob = None
        words = 0
        if document is not None and document.word_count:
            words = document.word_count
            blob = self._protect().protect(zlib.compress(json.dumps(document.to_dict(), ensure_ascii=False).encode("utf-8"), 6))
        keep = blob is not None
        with self._lock:
            conn = self._connection(create=True)
            assert conn is not None
            with conn:
                conn.execute("UPDATE documents SET fragments_blob = ?, word_count = ?, filter_version = ? WHERE doc_id = ?",
                             (blob, words, filter_version if keep else None, doc_id))
                conn.execute(
                    "UPDATE memory_entries SET filter_version = ?, text_persisted = ?, "
                    "needs_summary = CASE WHEN ? = 1 AND tier = 'high' AND summary IS NULL THEN 1 ELSE 0 END "
                    "WHERE doc_id = ? AND text_persisted = 1",
                    (filter_version if keep else None, int(keep), int(keep), doc_id))

    def stats(self) -> dict[str, int]:
        with self._lock:
            conn = self._connection(create=False)
            if conn is None:
                return {"documents": 0, "episodes": 0, "entries": 0}
            return {
                "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "episodes": conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
                "entries": conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0],
            }

    # -- write -------------------------------------------------------------------------------------

    def save_episode(
        self,
        *,
        doc_id: str,
        context_key: str,
        app: str | None,
        domain: str | None,
        title: str | None,
        document: TextDocument | None,
        filter_version: str | None,
        episode: dict[str, Any],
        entry_text: str,
        entry_json: dict[str, Any],
        tier: str,
        needs_summary: bool,
        text_persisted: bool,
    ) -> int | None:
        """Write the document, the episode and its entry in one transaction. Returns the entry id, or ``None`` if this
        episode was already saved (so reprocessing after a crash adds nothing)."""
        blob = None
        word_count = 0
        if document is not None and document.word_count:
            word_count = document.word_count
            blob = self._protect().protect(zlib.compress(json.dumps(document.to_dict(), ensure_ascii=False).encode("utf-8"), 6))
        now_us = now_epoch_us()
        start_us, end_us = to_epoch_us(episode["start"]), to_epoch_us(episode["end"])

        with self._lock:
            conn = self._connection(create=True)
            assert conn is not None
            try:
                conn.execute("BEGIN")
                if conn.execute("SELECT 1 FROM episodes WHERE doc_id = ? AND start_ts = ?", (doc_id, start_us)).fetchone():
                    conn.execute("ROLLBACK")
                    return None
                existing = conn.execute("SELECT visits, first_seen FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
                visits = (existing["visits"] if existing else 0) + 1
                if existing is None:
                    conn.execute(
                        "INSERT INTO documents(doc_id, context_key, app, domain, title, visits, word_count, fragments_blob, "
                        "filter_version, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (doc_id, context_key, app, domain, title, visits, word_count, blob, filter_version, start_us, end_us),
                    )
                else:
                    # Keep the stored text when this run kept none (a low-importance revisit must not erase the page).
                    conn.execute(
                        "UPDATE documents SET visits = ?, title = ?, last_seen = ?, "
                        "word_count = CASE WHEN ? IS NULL THEN word_count ELSE ? END, "
                        "fragments_blob = COALESCE(?, fragments_blob), filter_version = COALESCE(?, filter_version) "
                        "WHERE doc_id = ?",
                        (visits, title, end_us, blob, word_count, blob, filter_version if blob else None, doc_id),
                    )
                cursor = conn.execute(
                    "INSERT INTO episodes(doc_id, start_ts, end_ts, visit_number, n_captures, active_seconds, key_presses, "
                    "mouse_clicks, mouse_scrolls, mouse_moves, max_score, words_seen, words_added, truncated_captures, "
                    "tier, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        doc_id, start_us, end_us, visits, episode["n_captures"], episode["active_seconds"],
                        episode["key_presses"], episode["mouse_clicks"], episode["mouse_scrolls"], episode["mouse_moves"],
                        episode["max_score"], episode["words_seen"], episode["words_added"], episode["truncated_captures"],
                        tier, now_us,
                    ),
                )
                episode_id = int(cursor.lastrowid)
                entry_json = {**entry_json, "visit_number": visits}
                cursor = conn.execute(
                    "INSERT INTO memory_entries(episode_id, doc_id, created_at, tier, importance, entry_text, entry_json, "
                    "needs_summary, text_persisted, filter_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        episode_id, doc_id, now_us, tier, episode["max_score"], entry_text,
                        json.dumps(entry_json, ensure_ascii=False), int(needs_summary), int(text_persisted), filter_version,
                    ),
                )
                conn.execute("COMMIT")
                return int(cursor.lastrowid)
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise


def memory_path_for(db_path: str | Path) -> Path:
    """``memory.db`` beside the given ``context.db``."""
    return Path(db_path).resolve().parent / "memory.db"
