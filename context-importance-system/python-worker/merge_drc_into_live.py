"""Merge DesktopResearchCollector (DRC) history into the live ``context.db``, without double-counting.

Live capture and DRC recorded the same desktop at the same time, so DRC captures that fall inside the live
database's time span are DROPPED. Only DRC captures older than the first live record are kept:

    DRC:   |=============================================|
    live:                         |=========================>
    kept:  |=====================|   (older than the live data)

The kept captures are replayed exactly as ``import_research_collector.py`` does (this project's exclusions
applied, features and the rule-based decision from ``offline.training.build_dataset``), embedded with MobileCLIP,
and inserted into the live database. A one-row table ``record_sources`` remembers the cutoff, so the dashboard
can tell DRC history from live capture (rows with ``ts < ts_before``) and a re-run keeps the same cutoff.
Labels stay NULL: ``adaptation.scheduled_job`` labels them from what followed.

Usage (from python-worker/; nothing is written unless --apply):
    python merge_drc_into_live.py --source D:\\DesktopResearchCollector\\collector.db --live %LOCALAPPDATA%\\context-importance\\context.db
    python merge_drc_into_live.py ... --apply [--screenshots-dir DIR] [--backup-dir DIR] [--embedding-cache FILE] [--no-embedding]

``--apply`` first copies the live database with SQLite's backup API (safe while the worker runs) and refuses to
continue without that copy. The source DRC database is only ever opened read-only.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from storage.database import connect, migrate, to_epoch_us

SOURCE = "drc"

_DDL = """
CREATE TABLE IF NOT EXISTS record_sources (
    source       TEXT PRIMARY KEY,
    ts_before    INTEGER NOT NULL,   -- records of this source have ts < ts_before (epoch microseconds, UTC)
    record_count INTEGER NOT NULL,
    imported_at  INTEGER NOT NULL,
    note         TEXT
)
"""


def ensure_source_table(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(_DDL)


def cutoff_us(conn: sqlite3.Connection) -> int | None:
    """Start of the live data: the stored cutoff of an earlier merge, else the oldest record. None if empty."""
    ensure_source_table(conn)
    row = conn.execute("SELECT ts_before FROM record_sources WHERE source = ?", (SOURCE,)).fetchone()
    if row is not None:
        return int(row[0])
    row = conn.execute("SELECT MIN(ts) FROM context_records").fetchone()
    return None if row[0] is None else int(row[0])


def split_captures(captures: Sequence[Any], cutoff: int) -> tuple[list[Any], list[Any]]:
    """(older than the cutoff -> kept, at or after it -> dropped as overlapping live data)."""
    kept = [c for c in captures if to_epoch_us(c.ts) < cutoff]
    dropped = [c for c in captures if to_epoch_us(c.ts) >= cutoff]
    return kept, dropped


def existing_keys(conn: sqlite3.Connection) -> set[tuple[int, str, str]]:
    return {(int(r[0]), r[1] or "", r[2] or "") for r in conn.execute("SELECT ts, app, title FROM context_records")}


def drop_already_present(captures: Iterable[Any], present: set[tuple[int, str, str]]) -> list[Any]:
    """Makes a re-run (or a run that was interrupted halfway) add only what is missing."""
    return [c for c in captures if (to_epoch_us(c.ts), c.app or "", c.title or "") not in present]


def register_source(conn: sqlite3.Connection, cutoff: int, count_added: int) -> None:
    ensure_source_table(conn)
    now = to_epoch_us(datetime.now(timezone.utc))
    with conn:
        conn.execute(
            "INSERT INTO record_sources (source, ts_before, record_count, imported_at, note) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(source) DO UPDATE SET record_count = record_count + excluded.record_count, imported_at = excluded.imported_at",
            (SOURCE, cutoff, count_added, now, "DesktopResearchCollector history older than the live capture"),
        )


def backup(live: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"pre-drc-merge-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def _fmt(us: int) -> str:
    return datetime.fromtimestamp(us / 1_000_000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="DRC collector.db (opened read-only)")
    parser.add_argument("--screenshots-dir", help="folder holding <screenshot_id>.jpg when the source's own paths are not valid (e.g. exported from Neon)")
    parser.add_argument("--live", required=True, help="the live context.db to merge into")
    parser.add_argument("--apply", action="store_true", help="really write (default: only show the plan)")
    parser.add_argument("--backup-dir", help="where the pre-merge copy goes (default: <live folder>\\backups)")
    parser.add_argument("--embedding-cache", help="npz cache of embeddings (default: drc_merge_embeddings.npz beside the live db)")
    parser.add_argument("--checkpoint", help="mobileclip_s0.pt, only if the ONNX model is not available")
    parser.add_argument("--no-embedding", action="store_true", help="skip MobileCLIP (visual features stay neutral)")
    args = parser.parse_args()

    live = Path(args.live)
    if not live.exists():
        print(f"live database not found: {live}", file=sys.stderr)
        return 2

    from offline.drc_replay import replay_drc

    captures, stats = replay_drc(args.source, screenshots_dir=args.screenshots_dir)
    print(stats.summary())

    probe = connect(live, read_only=not args.apply)
    try:
        cutoff = cutoff_us(probe) if args.apply else _read_only_cutoff(probe)
        if cutoff is None:
            print("The live database is empty, so there is nothing to avoid overlapping: use import_research_collector.py instead.")
            return 2
        kept, dropped = split_captures(captures, cutoff)
        kept = drop_already_present(kept, existing_keys(probe))
    finally:
        probe.close()

    print(f"\nlive data starts:           {_fmt(cutoff)}")
    print(f"DRC captures replayed:      {len(captures)}")
    print(f"dropped (overlap with live): {len(dropped)}")
    print(f"to add (older, new):        {len(kept)}")
    if kept:
        print(f"added span:                 {_fmt(to_epoch_us(kept[0].ts))} -> {_fmt(to_epoch_us(kept[-1].ts))}")
    if not args.apply:
        print("\nDry run: nothing was written. Re-run with --apply to merge.")
        return 0
    if not kept:
        print("\nNothing to add.")
        return 0

    saved = backup(live, Path(args.backup_dir) if args.backup_dir else live.parent / "backups")
    print(f"\nBacked up the live database to {saved}")

    from offline.training import build_dataset, embed_captures
    from storage.context_store import ContextRecord, insert_context_record

    embeddings = None
    if not args.no_embedding:
        cache = args.embedding_cache or str(live.parent / "drc_merge_embeddings.npz")
        from features.mobileclip_encoder import default_encoder   # ONNX when found (as the live worker), else torch

        embeddings = embed_captures(kept, cache, encoder=default_encoder(), checkpoint=args.checkpoint)
    df = build_dataset(kept, embeddings)

    conn = connect(live)
    try:
        migrate(conn)
        for row in df.itertuples():
            insert_context_record(conn, ContextRecord(
                ts=row.ts.to_pydatetime(), decision=row.rule_decision, features=row.features,
                embedding=row.embedding, app=row.app, domain=row.domain, title=row.title,
            ))
        register_source(conn, cutoff, len(df))
    finally:
        conn.close()

    with_embedding = sum(e is not None for e in df["embedding"])
    print(f"Inserted {len(df)} DRC records ({with_embedding} with embeddings) older than {_fmt(cutoff)}.")
    return 0


def _read_only_cutoff(conn: sqlite3.Connection) -> int | None:
    """Dry run: same answer as cutoff_us, without creating the source table."""
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='record_sources'").fetchone()
    if has:
        row = conn.execute("SELECT ts_before FROM record_sources WHERE source = ?", (SOURCE,)).fetchone()
        if row is not None:
            return int(row[0])
    row = conn.execute("SELECT MIN(ts) FROM context_records").fetchone()
    return None if row[0] is None else int(row[0])


if __name__ == "__main__":
    raise SystemExit(main())
