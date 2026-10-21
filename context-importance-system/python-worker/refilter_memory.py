r"""Put the page text already stored in memory.db through the CAPRLM filter (see adaptation/refilter.py).

    python refilter_memory.py --memory-db %LOCALAPPDATA%\context-importance\memory.db --privacy-url http://127.0.0.1:8787
    python refilter_memory.py ... --apply

Without --apply nothing is written and the report says what would change. With --apply a copy of memory.db is made first
(SQLite backup API, safe while other programs have it open) and the run stops if that copy cannot be made.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--memory-db", required=True)
    parser.add_argument("--privacy-url", default="http://127.0.0.1:8787", help="CAPRLM service (this PC only)")
    parser.add_argument("--apply", action="store_true", help="really rewrite the pages (default: report only)")
    parser.add_argument("--limit", type=int, default=None, help="only the newest N pages (for a trial)")
    parser.add_argument("--backup-dir", default=None, help="default: <memory.db folder>\backups")
    args = parser.parse_args()

    from adaptation.caprlm_client import CaprlmPrivacyFilter
    from adaptation.refilter import refilter_memory
    from storage.memory_store import MemoryStore

    path = Path(args.memory_db)
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 2
    client = CaprlmPrivacyFilter(args.privacy_url)
    if not client.available():
        print(f"The privacy service is not reachable at {args.privacy_url}. Start the dashboard (it starts it) and retry.", file=sys.stderr)
        return 2

    memory = MemoryStore(path)
    todo = memory.documents_to_refilter()
    print(f"pages holding text stored without a real filter: {len(todo)}")
    if args.apply:
        backup_dir = Path(args.backup_dir) if args.backup_dir else path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"pre-refilter-memory-{datetime.now():%Y%m%d-%H%M%S}.db"
        src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        print(f"backed up memory.db to {target}")

    report = refilter_memory(memory, client, apply=args.apply, limit=args.limit)
    print(json.dumps(report.to_dict(), indent=2))
    print("done." if args.apply else "dry run: nothing was written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
