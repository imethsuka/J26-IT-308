"""Apply pending database migrations.

Usage:
    python -m storage.migrate --db context.db
    python -m storage.migrate --db context.db --status   # report version without applying
"""

from __future__ import annotations

import argparse

from .database import connect, current_schema_version, migrate
from .schema import SCHEMA_VERSION


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="path to the SQLite database (created if absent)")
    parser.add_argument("--status", action="store_true", help="print current vs latest version and exit")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        current = current_schema_version(conn)
        if args.status:
            print(f"database : {args.db}")
            print(f"current  : v{current}")
            print(f"latest   : v{SCHEMA_VERSION}")
            print("status   : " + ("up to date" if current >= SCHEMA_VERSION else "migration pending"))
            return 0

        applied = migrate(conn)
        if applied:
            print(f"applied migrations: {', '.join(f'v{v}' for v in applied)}  (now at v{SCHEMA_VERSION})")
        else:
            print(f"already up to date at v{current}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
