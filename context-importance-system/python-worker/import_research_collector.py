"""Import a DesktopResearchCollector (DRC) ``collector.db`` into this project's ``context.db`` schema,
so the scheduled adaptation job and ``eval.model_comparison`` can run on real historical data.

The replay itself lives in :mod:`offline.drc_replay` (one record per DRC screenshot, the context as
the live collector would have reported it, this project's exclusions applied, source opened
read-only); features and the rule-based decision come from :func:`offline.training.build_dataset`,
the same code the training notebooks use. Labels are left NULL for ``adaptation.scheduled_job``.

Usage (from python-worker/):
    python import_research_collector.py --source D:\\DesktopResearchCollector\\collector.db --db context_drc.db \\
        [--checkpoint path\\to\\mobileclip_s0.pt] [--embedding-cache drc_embeddings.npz] [--no-embedding]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from offline.drc_replay import replay_drc
from offline.training import build_dataset, embed_captures
from storage.context_store import ContextRecord, count_context_records, insert_context_record
from storage.database import connect, migrate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="path to the DRC collector.db (opened read-only)")
    parser.add_argument("--db", default="context_drc.db", help="new context.db-schema database to write")
    parser.add_argument("--checkpoint", help="mobileclip_s0.pt (default: MOBILECLIP_S0_CHECKPOINT)")
    parser.add_argument("--embedding-cache", help="npz cache of embeddings (reused by the notebooks)")
    parser.add_argument("--no-embedding", action="store_true", help="skip MobileCLIP (visual features stay neutral)")
    args = parser.parse_args()

    captures, stats = replay_drc(args.source)
    print(stats.summary())

    embeddings = None
    if not args.no_embedding:
        cache = args.embedding_cache or str(Path(args.db).with_suffix(".embeddings.npz"))
        embeddings = embed_captures(captures, cache, checkpoint=args.checkpoint)

    df = build_dataset(captures, embeddings)

    conn = connect(args.db)
    try:
        migrate(conn)
        if count_context_records(conn):
            raise SystemExit(f"{args.db} already has records; import into a new database")
        for row in df.itertuples():
            insert_context_record(conn, ContextRecord(
                ts=row.ts.to_pydatetime(), decision=row.rule_decision, features=row.features,
                embedding=row.embedding, app=row.app, domain=row.domain, title=row.title,
            ))
    finally:
        conn.close()

    with_embedding = sum(e is not None for e in df["embedding"])
    print(f"\nInserted {len(df)} records into {args.db} ({with_embedding} with embeddings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
