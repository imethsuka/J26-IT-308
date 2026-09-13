"""Entry point for the Python worker.

Starts the IPC listener and routes each received snapshot through the full pipeline: feature
assembly (with MobileCLIP embeddings, unless disabled) -> importance scoring (the active deployed
model, or the rule-based baseline before one exists) -> storage in the SQLite context store, ready
for the adaptation cycle to label and retrain from later.

Usage:
    python main.py --db context.db [--transport namedpipe|tcp] [--pipe-name NAME] [--tcp-port PORT]
                   [--no-embedding] [--decision-threshold P]
                   [--raw-db PATH] [--raw-ttl-days D] [--raw-max-mb MB] [--no-screen-text]

Defaults match ``csharp-collector/src/CsharpCollector/collector.config.json``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from app_paths import default_db_path
from pipeline import SnapshotPipeline, load_active_scorer
from sensing.listener import create_listener
from storage.database import connect, migrate
from storage.raw_buffer import DEFAULT_MAX_TEXT_MB, DEFAULT_TTL_DAYS, RawBuffer, raw_buffer_path_for


def main() -> None:
    parser = argparse.ArgumentParser(description="Context-importance Python worker")
    parser.add_argument("--transport", choices=["namedpipe", "tcp"], default="namedpipe")
    parser.add_argument("--pipe-name", default="context-importance")
    parser.add_argument("--tcp-port", type=int, default=51789)
    parser.add_argument("--db", default=default_db_path(), help="path to the SQLite context store")
    parser.add_argument(
        "--no-embedding", action="store_true",
        help="skip MobileCLIP embedding (e.g. torch/mobileclip not installed); "
             "records are still scored and stored, with neutral visual features",
    )
    parser.add_argument(
        "--decision-threshold", type=float, default=0.5,
        help="predict_proba threshold for the stored save/discard decision",
    )
    parser.add_argument(
        "--raw-db", default=None,
        help="path to the separate, encrypted raw screen-text buffer (default: raw_buffer.db beside --db; "
             "created on the first capture with text)",
    )
    parser.add_argument("--raw-ttl-days", type=float, default=DEFAULT_TTL_DAYS, help="delete buffered text older than this")
    parser.add_argument(
        "--raw-max-mb", type=float, default=DEFAULT_MAX_TEXT_MB,
        help="when the buffered text passes this size, drop the text of the lowest-importance captures first",
    )
    parser.add_argument("--no-screen-text", action="store_true", help="ignore screen text sent by the collector")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("worker")

    # The listener calls the pipeline from its own thread(s); the pipeline serializes all DB use.
    Path(args.db).resolve().parent.mkdir(parents=True, exist_ok=True)  # first run of an installed app
    conn = connect(args.db, check_same_thread=False)
    migrate(conn)

    scorer, prototypes = load_active_scorer(conn)
    logger.info("scoring with %s", scorer.name)
    raw_buffer = None if args.no_screen_text else RawBuffer(
        args.raw_db or raw_buffer_path_for(args.db), ttl_days=args.raw_ttl_days, max_text_mb=args.raw_max_mb,
    )
    pipeline = SnapshotPipeline(
        conn, scorer, prototypes=prototypes, embed=not args.no_embedding,
        decision_threshold=args.decision_threshold, raw_buffer=raw_buffer,
    )
    if pipeline.embedding_enabled:
        logger.info("loading the MobileCLIP encoder")
        if pipeline.load_encoder():
            logger.info("MobileCLIP encoder loaded")

    listener = create_listener(
        args.transport, pipe_name=args.pipe_name, tcp_port=args.tcp_port, handler=pipeline,
    )
    # Serve on a background thread and wait here in short steps. Serving on the main thread would
    # block in ConnectNamedPipe/ReadFile, where Ctrl+C is not delivered until the collector sends.
    thread = listener.start_background()
    interrupted = False
    try:
        while thread.is_alive():
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        interrupted = True
        logger.info("shutting down")
    finally:
        listener.shutdown()
        pipeline.close()
        if raw_buffer is not None:
            raw_buffer.close()
        conn.close()
    if not interrupted:
        logger.error("listener stopped unexpectedly (see the error above)")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
