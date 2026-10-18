r"""Start the written part of the memories over, keeping what cannot be rebuilt.

    python -m adaptation.memory_reset --memory-db %LOCALAPPDATA%\context-importance\memory.db [--backup-dir DIR]
    adapt.exe memory-reset --memory-db ...                                       (the installed app)

The pages (merged, filtered text) and the episodes are kept: they came from raw screen text that is deleted after 3 days,
so they could never be built again. Everything a model wrote from them is cleared, so the next "Write summaries" (or the
background job) writes it again with the current prompts, rules and model:

* task switching and glances are hidden (``adaptation/memory_tidy.py``), not deleted;
* every sentence is cleared, and every high-importance entry that kept text waits for one again;
* every session note and day recap is deleted.

A copy of memory.db is made first (SQLite backup API), next to it in ``backups\`` unless ``--backup-dir`` says otherwise.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from storage.memory_store import MemoryStore

from .memory_tidy import tidy_memories


def backup(db: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"memory-before-reset-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as source, sqlite3.connect(target) as copy:
        source.backup(copy)
    return target


def reset_memories(memory: MemoryStore) -> dict[str, Any]:
    tidy = tidy_memories(memory)
    conn = memory._connection(create=False)
    if conn is None:
        return {"tidy": tidy}
    with memory._lock, conn:
        cleared = conn.execute("UPDATE memory_entries SET summary = NULL, summary_model = NULL, "
                               "needs_summary = CASE WHEN tier = 'high' AND text_persisted = 1 AND hidden IS NULL THEN 1 ELSE 0 END"
                               ).rowcount
        sessions = conn.execute("DELETE FROM session_notes").rowcount
        days = conn.execute("DELETE FROM day_notes").rowcount
        waiting = conn.execute("SELECT COUNT(*) FROM memory_entries WHERE needs_summary = 1").fetchone()[0]
    return {"tidy": tidy, "entries_cleared": cleared, "session_notes_deleted": sessions, "day_notes_deleted": days,
            "waiting_for_a_sentence": waiting}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--memory-db", required=True)
    parser.add_argument("--backup-dir", default=None, help="where the copy goes (default: backups\\ beside memory.db)")
    args = parser.parse_args(argv)
    db = Path(args.memory_db)
    if not db.exists():
        print(f"{db} not found", file=sys.stderr)
        return 2
    copy = backup(db, Path(args.backup_dir) if args.backup_dir else db.parent / "backups")
    print(f"backup: {copy}")
    memory = MemoryStore(db)
    try:
        print(json.dumps(reset_memories(memory)))
    finally:
        memory.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
