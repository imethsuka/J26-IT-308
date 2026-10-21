"""Report database size and row counts, for the storage-overhead metric.

Prints on-disk size (main DB + WAL/SHM sidecars), logical page usage, per-table row counts, the
unlabelled/label/decision breakdown, and embedding-blob bytes. Optionally appends one flat row to a
CSV so runs accumulate for reporting.

Usage:
    python -m storage.report_storage --db context.db [--csv storage_overhead.csv] [--label run-1]
"""

from __future__ import annotations

import argparse
import csv
import platform
from datetime import datetime, timezone
from pathlib import Path

from .database import connect, current_schema_version

KB = 1024
MB = 1024 * 1024


def collect(db_path: str) -> dict:
    path = Path(db_path)
    conn = connect(db_path, read_only=True)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]

        context_rows = _scalar(conn, "SELECT COUNT(*) FROM context_records")
        model_rows = _scalar(conn, "SELECT COUNT(*) FROM model_versions")
        unlabelled = _scalar(conn, "SELECT COUNT(*) FROM context_records WHERE label IS NULL")
        saved = _scalar(conn, "SELECT COUNT(*) FROM context_records WHERE decision = 'save'")
        discarded = _scalar(conn, "SELECT COUNT(*) FROM context_records WHERE decision = 'discard'")
        with_embedding = _scalar(conn, "SELECT COUNT(*) FROM context_records WHERE embedding IS NOT NULL")
        embedding_bytes = _scalar(conn, "SELECT COALESCE(SUM(LENGTH(embedding)), 0) FROM context_records")
        features_bytes = _scalar(conn, "SELECT COALESCE(SUM(LENGTH(features_json)), 0) FROM context_records")

        schema_version = current_schema_version(conn)
    finally:
        conn.close()

    file_bytes = path.stat().st_size if path.exists() else 0
    wal_bytes = _sidecar_size(path, "-wal")
    shm_bytes = _sidecar_size(path, "-shm")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(path),
        "schema_version": schema_version,
        "file_bytes": file_bytes,
        "wal_bytes": wal_bytes,
        "shm_bytes": shm_bytes,
        "total_on_disk_bytes": file_bytes + wal_bytes + shm_bytes,
        "page_size": page_size,
        "page_count": page_count,
        "free_pages": freelist,
        "context_records": context_rows,
        "model_versions": model_rows,
        "unlabelled": unlabelled,
        "saved": saved,
        "discarded": discarded,
        "records_with_embedding": with_embedding,
        "embedding_bytes": embedding_bytes,
        "features_json_bytes": features_bytes,
        "bytes_per_record": round(file_bytes / context_rows, 1) if context_rows else 0.0,
    }


def _print_report(stats: dict) -> None:
    print("Context store — storage overhead")
    print("-" * 52)
    print(f"  database        : {stats['db_path']}  (schema v{stats['schema_version']})")
    print(f"  on disk         : {stats['total_on_disk_bytes'] / MB:.3f} MB")
    print(f"    main .db      : {stats['file_bytes'] / MB:.3f} MB")
    print(f"    -wal / -shm   : {stats['wal_bytes'] / KB:.1f} KB / {stats['shm_bytes'] / KB:.1f} KB")
    print(f"  pages           : {stats['page_count']} x {stats['page_size']} B "
          f"({stats['free_pages']} free)")
    print()
    print(f"  context records : {stats['context_records']}")
    print(f"    unlabelled    : {stats['unlabelled']}")
    print(f"    save/discard  : {stats['saved']} / {stats['discarded']}")
    print(f"    w/ embedding  : {stats['records_with_embedding']}")
    print(f"  model versions  : {stats['model_versions']}")
    print()
    print(f"  embedding blobs : {stats['embedding_bytes'] / MB:.3f} MB")
    print(f"  features JSON   : {stats['features_json_bytes'] / MB:.3f} MB")
    print(f"  bytes / record  : {stats['bytes_per_record']:.1f}")


def _append_csv(path: Path, stats: dict, label: str) -> None:
    row = {"label": label, "host": platform.node(), **stats}
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _scalar(conn, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def _sidecar_size(db_path: Path, suffix: str) -> int:
    sidecar = db_path.with_name(db_path.name + suffix)
    return sidecar.stat().st_size if sidecar.exists() else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    parser.add_argument("--csv", help="append one measurement row to this CSV")
    parser.add_argument("--label", default=platform.node(), help="label recorded in the CSV row")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"error: database not found: {args.db}")
        return 2

    stats = collect(args.db)
    _print_report(stats)
    if args.csv:
        csv_path = Path(args.csv)
        _append_csv(csv_path, stats, args.label)
        print(f"\nAppended 1 row to {csv_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
