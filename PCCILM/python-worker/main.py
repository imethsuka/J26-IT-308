"""Entry point for the Python worker.

Starts the IPC listener and routes each received snapshot through the full pipeline: feature
assembly (with MobileCLIP embeddings, unless disabled) -> importance scoring (the active deployed
model, or the rule-based baseline before one exists) -> storage in the SQLite context store, ready
for the adaptation cycle to label and retrain from later.

Usage:
    python main.py --db context.db [--transport namedpipe|tcp] [--pipe-name NAME] [--tcp-port PORT]
                   [--no-embedding] [--decision-threshold P]
                   [--raw-db PATH] [--raw-ttl-days D] [--raw-max-mb MB] [--no-screen-text]
                   [--screenshots] [--shots-db PATH] [--shots-ttl-days D] [--shots-max-mb MB] [--shots-quality Q]

Defaults match ``csharp-collector/src/CsharpCollector/collector.config.json``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from app_paths import default_db_path
from models.rule_based import RuleBasedImportanceScorer
from pipeline import (
    DEFAULT_DECISION_THRESHOLD, DEFAULT_REDUNDANCY_SIMILARITY, DEFAULT_REDUNDANCY_WINDOW_S,
    SnapshotPipeline, active_decision_threshold, active_keep_rate, derive_budget_threshold, load_active_scorer,
    load_shadows,
)
from sensing.listener import create_listener
from storage.context_store import get_active_model_version
from storage.database import connect, migrate
from storage.raw_buffer import DEFAULT_MAX_TEXT_MB, DEFAULT_TTL_DAYS, RawBuffer, raw_buffer_path_for
from storage.screenshot_store import (
    DEFAULT_MAX_MB, DEFAULT_TTL_DAYS as SHOTS_TTL_DAYS, JPEG_QUALITY, ScreenshotStore, screenshots_path_for,
)


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
        "--decision-threshold", type=float, default=None,
        help="fixed predict_proba threshold for the stored save/discard decision (default: a deployed model keeps "
             "the top keep-rate share of recent live scores; the rule-based baseline uses "
             f"{DEFAULT_DECISION_THRESHOLD})",
    )
    parser.add_argument(
        "--redundancy-window", type=float, default=DEFAULT_REDUNDANCY_WINDOW_S,
        help="seconds after a save during which the same screen is not saved again (0 = off)",
    )
    parser.add_argument(
        "--redundancy-similarity", type=float, default=DEFAULT_REDUNDANCY_SIMILARITY,
        help="cosine similarity of two frames above which they count as the same screen",
    )
    parser.add_argument(
        "--no-shadows", action="store_true",
        help="score with the champion only (by default the newest version of every other model scores each screen "
             "too, and what each would have saved is recorded in model_decisions)",
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
    parser.add_argument(
        "--screenshots", action="store_true",
        help="keep the frame behind each 'save' decision, encrypted, for the history slider (off by default)",
    )
    parser.add_argument("--shots-db", default=None, help="screenshot store (default: screenshots.db beside --db)")
    parser.add_argument("--shots-ttl-days", type=float, default=SHOTS_TTL_DAYS, help="delete screenshots older than this")
    parser.add_argument("--shots-max-mb", type=float, default=DEFAULT_MAX_MB, help="past this size the oldest screenshots go first")
    parser.add_argument("--shots-quality", type=int, default=JPEG_QUALITY, help="JPEG quality of kept screenshots, 1 to 95")
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
    threshold = args.decision_threshold
    if threshold is None:
        threshold = active_decision_threshold(conn)
    if threshold is None and not isinstance(scorer, RuleBasedImportanceScorer):
        threshold = derive_budget_threshold(conn, scorer)  # a model deployed before thresholds were stored
    if threshold is None:
        threshold = DEFAULT_DECISION_THRESHOLD
    # A deployed model keeps the top share of recent live scores; a cutoff given on the command line stays fixed.
    keep_rate = None
    if args.decision_threshold is None and not isinstance(scorer, RuleBasedImportanceScorer):
        keep_rate = active_keep_rate(conn)
    active = get_active_model_version(conn)
    is_rules = isinstance(scorer, RuleBasedImportanceScorer)
    model_version_id = active.id if active is not None and not is_rules else None
    champion_algorithm = "rules" if is_rules else (active.algorithm or "lightgbm")
    shadows = [] if args.no_shadows else load_shadows(
        conn, champion_version=None if is_rules else active.version, include_rules=not is_rules)
    logger.info("shadow models: %s", ", ".join(s.model for s in shadows) or "none")
    logger.info("save threshold %.4f%s, redundancy window %.0fs", threshold,
                f" until the rolling top {keep_rate:.0%} takes over" if keep_rate else "", args.redundancy_window)
    raw_buffer = None if args.no_screen_text else RawBuffer(
        args.raw_db or raw_buffer_path_for(args.db), ttl_days=args.raw_ttl_days, max_text_mb=args.raw_max_mb,
    )
    screenshots = ScreenshotStore(
        args.shots_db or screenshots_path_for(args.db), ttl_days=args.shots_ttl_days, max_mb=args.shots_max_mb,
        quality=args.shots_quality,
    ) if args.screenshots else None
    if screenshots is not None:
        logger.info("screenshots on: saved frames are kept in %s", screenshots.path)
    pipeline = SnapshotPipeline(
        conn, scorer, prototypes=prototypes, embed=not args.no_embedding,
        decision_threshold=threshold, raw_buffer=raw_buffer, screenshots=screenshots,
        redundancy_window_s=args.redundancy_window, redundancy_similarity=args.redundancy_similarity,
        keep_rate=keep_rate, model_version_id=model_version_id,
        shadows=shadows, champion_algorithm=champion_algorithm,
    )
    pipeline.restore_state()
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
        if screenshots is not None:
            screenshots.close()
        conn.close()
    if not interrupted:
        logger.error("listener stopped unexpectedly (see the error above)")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
