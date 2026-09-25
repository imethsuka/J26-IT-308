"""Export ONE participant's DesktopResearchCollector rows from Neon PostgreSQL into a DRC-shaped SQLite file.

The output has the same layout as DRC's own ``collector.db``, so ``merge_drc_into_live.py`` (and the replay code
behind it) reads it unchanged. Read-only on Neon: the session is opened read-only, the query filters on one
``profile_id``, and only the columns the replay uses are fetched (no URLs, hashes, metadata or display names).

The connection string is read from the environment, never from a file in the repo or the command line:

    $env:NEON_DATABASE_URL = "postgresql://user:password@host/neondb?sslmode=require"
    python export_neon_drc.py --profile-id <id> --out %LOCALAPPDATA%\\context-importance\\drc-training\\neon-drc.db

Needs ``sqlalchemy`` and ``psycopg2-binary`` (not part of the worker's requirements: this is an occasional offline tool).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile-id", required=True, help="the participant whose rows to export (others are never read)")
    parser.add_argument("--out", required=True, help="SQLite file to write (replaced if it exists)")
    parser.add_argument("--schema", default="drc")
    parser.add_argument("--from-date", help="optional, YYYY-MM-DD (UTC, inclusive)")
    parser.add_argument("--to-date", help="optional, YYYY-MM-DD (UTC, inclusive)")
    args = parser.parse_args()

    url = os.environ.get("NEON_DATABASE_URL")
    if not url:
        print("Set NEON_DATABASE_URL (a postgresql://... connection string) first.", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine

    from offline.cloud_sources import clean_database_url, fetch_profile_tables, write_drc_sqlite

    engine = create_engine(clean_database_url(url), connect_args={"options": "-c default_transaction_read_only=on"})
    tables = fetch_profile_tables(engine, args.profile_id, schema=args.schema,
                                  from_date=args.from_date, to_date=args.to_date)
    for name, df in tables.items():
        span = f"{df['timestamp'].min():%Y-%m-%d %H:%M} -> {df['timestamp'].max():%Y-%m-%d %H:%M}" if len(df) else "empty"
        print(f"{name:20s} {len(df):>8}  {span}")
    out = write_drc_sqlite(tables, Path(args.out))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
