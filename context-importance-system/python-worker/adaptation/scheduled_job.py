"""Scheduled adaptation job.

Runs the maintenance pipeline in a fixed order, gated on the host being idle and on AC power:

    1. label construction   (adaptation.label_construction)      -> Phase 9
    2. prototype learning    (adaptation.prototype_learning)      -> Phase 10
    3. Model 1 retrain       (models.ImportanceModel.train)       -> Phase 8
    4. drift check           (adaptation.drift_detection)         -> Phase 11
    5. validation gate       (adaptation.deployment)              -> Phase 12

It can be run one-shot from Windows Task Scheduler, or as a daemon with ``--loop`` that polls until
the host is idle. Every run emits a structured JSON summary (duration, contexts labelled, drift,
deploy/keep decision) appended to a log for later reporting.

Retraining is skipped when no record has been labelled since the last candidate was trained (the
data would be identical), so ``--loop`` does not pile up a new model version every poll while the
host sits idle. At cold start (no deployed model) the candidate is validated against the rule-based
baseline, which is what the worker serves until a model is deployed.

Note on ordering: retraining uses each context's features exactly as they were captured (the visual
similarity/novelty scalars are already baked into ``features_json``). Newly-learned prototypes are
bundled with the candidate version for *future* captures; they do not retroactively change this
run's training features. Recomputing visual features from stored embeddings against fresh prototypes
is a possible future refinement.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from models.lightgbm_model import DEFAULT_VERSIONS_DIR, ImportanceModel
from models.rule_based import RuleBasedImportanceScorer

from .deployment import validate_and_maybe_deploy
from .drift_detection import ErrorDriftDetector
from .host_state import HostReadiness, check_host_ready
from .label_construction import (
    DEFAULT_LABEL_DELAY,
    DEFAULT_LABEL_HORIZON,
    DEFAULT_SESSION_GAP,
    LabelConstructor,
    RetrievalFn,
    label_pending_records,
)
from .prototype_learning import fit_prototypes, save_prototypes

logger = logging.getLogger("worker.adaptation.job")

LoadIncumbentFn = Callable[[str], "Any | None"]


@dataclass
class CycleConfig:
    label_delay: timedelta = DEFAULT_LABEL_DELAY
    label_horizon: timedelta = DEFAULT_LABEL_HORIZON
    session_gap: timedelta = DEFAULT_SESSION_GAP
    retrieval_fn: RetrievalFn | None = None
    prototype_k: int = 8
    min_train_rows: int = 20
    holdout_fraction: float = 0.2
    num_boost_round: int = 100
    primary_metric: str = "f1"
    threshold: float = 0.5
    drift_delta: float = 0.002
    versions_dir: Path = field(default_factory=lambda: DEFAULT_VERSIONS_DIR)
    idle_seconds: float = 300.0
    require_ac: bool = True
    # Skip the retrain when nothing was labelled since the latest candidate was trained.
    require_new_labels: bool = True


@dataclass
class RunSummary:
    started_at: datetime
    ran: bool = False
    skip_reason: str | None = None
    decision: str = "skipped"                    # "deploy" | "keep" | "skipped"
    candidate_version: str | None = None
    contexts_labelled: int = 0
    host: HostReadiness | None = None
    stages: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None

    def finalize(self, now: datetime) -> "RunSummary":
        self.finished_at = now
        self.duration_seconds = round((now - self.started_at).total_seconds(), 3)
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        data["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        if self.host is not None:
            data["host"] = asdict(self.host)
        return data


def run_if_ready(
    conn,
    config: CycleConfig,
    *,
    now: datetime | None = None,
    force: bool = False,
    host_readiness: HostReadiness | None = None,
    load_incumbent_fn: LoadIncumbentFn | None = None,
) -> RunSummary:
    """Check host readiness (idle + AC) and run the cycle only if ready (or ``force``)."""
    wall_start = datetime.now(timezone.utc)
    readiness = host_readiness or check_host_ready(
        idle_threshold_seconds=config.idle_seconds, require_ac=config.require_ac
    )
    summary = RunSummary(started_at=wall_start, host=readiness)

    if not force and not readiness.ready:
        summary.skip_reason = readiness.reason
        return summary.finalize(datetime.now(timezone.utc))

    return run_cycle(conn, config, now=now, base_summary=summary, load_incumbent_fn=load_incumbent_fn)


def run_cycle(
    conn,
    config: CycleConfig,
    *,
    now: datetime | None = None,
    base_summary: RunSummary | None = None,
    load_incumbent_fn: LoadIncumbentFn | None = None,
) -> RunSummary:
    """
    Run the five stages in order and return a structured summary. Errors are captured, not raised.

    ``now`` is the *logical* time used for the label-age cutoff and the candidate version stamp;
    it defaults to the wall-clock start. Duration is always measured against the real wall clock, so
    it stays correct even when ``now`` is a synthetic time (as in tests).
    """
    import numpy as np

    from storage.context_store import list_context_records, list_model_versions

    wall_start = datetime.now(timezone.utc)
    summary = base_summary or RunSummary(started_at=wall_start)
    logical_now = now or summary.started_at
    summary.ran = True
    candidate_version = f"lightgbm-{logical_now.strftime('%Y%m%d-%H%M%S')}"
    summary.candidate_version = candidate_version

    try:
        # --- Stage 1: label construction ------------------------------------------------------
        constructor = LabelConstructor(
            label_delay=config.label_delay, label_horizon=config.label_horizon,
            session_gap=config.session_gap,
            retrieval_fn=config.retrieval_fn, threshold=config.threshold,
        )
        labelled = label_pending_records(conn, constructor, now=logical_now, write=True)
        newly_labelled_ids = [rid for rid, _ in labelled]
        summary.contexts_labelled = len(labelled)

        records = list_context_records(conn)
        by_id = {r.id: r for r in records}
        labelled_records = [r for r in records if r.label is not None]

        # Due by the clock, but their horizon is not fully observed yet, so they stay unlabelled
        # until a later run has the data. Censored rows are never labelled, so counting them after
        # the write gives the same answer as before it.
        censored = constructor.count_censored(
            records, now=logical_now, labelled_ids={r.id for r in labelled_records}
        )
        summary.stages["label_construction"] = {
            "contexts_labelled": len(labelled),
            "important": sum(1 for _, r in labelled if r.label == 1),
            "censored": censored,
            "data_span_hours": round(_span_hours(records), 2),
            "label_version": constructor.label_version,
        }

        # --- Stage 2: prototype learning ------------------------------------------------------
        important_embeddings = [
            r.embedding for r in labelled_records if r.label == 1 and r.embedding is not None
        ]
        proto_result = None
        if len(important_embeddings) >= config.prototype_k:
            proto_result = fit_prototypes(np.vstack(important_embeddings), k=config.prototype_k)
            summary.stages["prototype_learning"] = {
                "learned": True, "k": config.prototype_k,
                "n_important_embeddings": len(important_embeddings),
                "inertia": round(proto_result.inertia, 4),
            }
        else:
            summary.stages["prototype_learning"] = {
                "learned": False, "n_important_embeddings": len(important_embeddings),
                "reason": f"need >= {config.prototype_k} important embeddings",
            }

        # --- Stage 3: Model 1 retrain ---------------------------------------------------------
        labels_present = {r.label for r in labelled_records}
        if len(labelled_records) < config.min_train_rows or labels_present != {0, 1}:
            reason = (f"need >= {config.min_train_rows} labelled rows across both classes; "
                      f"have {len(labelled_records)} rows, classes {sorted(labels_present)}")
            if censored:
                reason += (f"; {censored} due record(s) are waiting for a full "
                           f"{_hours(config.label_horizon)}h of later data (the store spans "
                           f"{_span_hours(records):.1f}h): collect more data, or lower "
                           f"--label-horizon-hours")
            summary.stages["retrain"] = {"trained": False, "reason": reason}
            summary.decision = "skipped"
            return summary.finalize(datetime.now(timezone.utc))

        latest_version = (list_model_versions(conn) or [None])[-1]
        if config.require_new_labels and latest_version is not None and not any(
            # >= rather than >: coarse clocks can stamp a label and a version row identically.
            r.labeled_at is not None and r.labeled_at >= latest_version.created_at
            for r in labelled_records
        ):
            summary.stages["retrain"] = {
                "trained": False,
                "reason": f"no records labelled since candidate {latest_version.version!r} was "
                          f"trained; retraining would reuse the same data",
            }
            summary.decision = "skipped"
            return summary.finalize(datetime.now(timezone.utc))

        train_records, holdout_records = _temporal_split(
            labelled_records, config.holdout_fraction, horizon=config.label_horizon
        )
        n_embargoed = len(labelled_records) - len(train_records) - len(holdout_records)
        if not train_records:
            summary.stages["retrain"] = {
                "trained": False, "n_embargoed": n_embargoed,
                "reason": f"no training rows survive the {config.label_horizon} label-horizon embargo "
                          f"({n_embargoed} dropped); the labelled range is too dense for a clean split",
            }
            summary.decision = "skipped"
            return summary.finalize(datetime.now(timezone.utc))
        if {r.label for r in train_records} != {0, 1}:
            summary.stages["retrain"] = {
                "trained": False, "n_embargoed": n_embargoed,
                "reason": "training split is single-class after holdout",
            }
            summary.decision = "skipped"
            return summary.finalize(datetime.now(timezone.utc))

        import pandas as pd

        train_df = pd.DataFrame([{"features": r.features, "label": r.label} for r in train_records])
        candidate = ImportanceModel(num_boost_round=config.num_boost_round, versions_dir=config.versions_dir)
        train_info = candidate.train(train_df)
        candidate.save(candidate_version)
        if proto_result is not None:
            save_prototypes(proto_result, candidate_version, versions_dir=config.versions_dir,
                            metadata={"source_model": candidate_version})
            summary.stages["prototype_learning"]["saved_to_version"] = candidate_version
        summary.stages["retrain"] = {
            "trained": True, "candidate_version": candidate_version,
            "n_train": train_info["n_train"], "class_counts": train_info["class_counts"],
            "n_holdout": len(holdout_records), "n_embargoed": n_embargoed,
            "top_features": list(candidate.feature_importances("gain").items())[:5],
        }

        # --- Stage 4: drift check (concept drift on the incumbent's error over newly labelled) -
        incumbent = _resolve_incumbent(conn, config, load_incumbent_fn)
        newly_labelled_records = sorted(
            (by_id[rid] for rid in newly_labelled_ids if rid in by_id), key=lambda r: r.ts
        )
        if incumbent is not None and newly_labelled_records:
            detector = ErrorDriftDetector(delta=config.drift_delta)
            drift = False
            for r in newly_labelled_records:
                drift = detector.observe_prediction(
                    r.label, incumbent.predict_proba(r.features), threshold=config.threshold
                ) or drift
            summary.stages["drift_check"] = {
                "error_drift_detected": drift, "n_drifts": detector.n_drifts,
                "estimated_error": round(detector.estimated_error, 4), "n_evaluated": len(newly_labelled_records),
            }
        else:
            summary.stages["drift_check"] = {
                "error_drift_detected": None,
                "reason": "no deployed model yet" if incumbent is None else "no newly-labelled records",
            }

        # --- Stage 5: validation gate ---------------------------------------------------------
        # With no deployed model the worker serves the rule-based baseline (see
        # pipeline.load_active_scorer), so that is what the candidate has to beat.
        current = incumbent if incumbent is not None else RuleBasedImportanceScorer()
        holdout = [(r.features, r.label) for r in holdout_records]
        decision = validate_and_maybe_deploy(
            candidate, current, holdout, conn=conn, candidate_version=candidate_version,
            primary_metric=config.primary_metric, threshold=config.threshold, algorithm="lightgbm",
            notes=f"scheduled cycle {logical_now.isoformat()}",
        )
        summary.stages["validation"] = {
            "deployed": decision.deployed, "reason": decision.reason,
            "primary_metric": decision.primary_metric, "compared_against": current.name,
            "candidate_score": decision.candidate_score, "current_score": decision.current_score,
        }
        summary.decision = "deploy" if decision.deployed else "keep"

    except Exception as exc:  # a scheduled job must not crash the scheduler/loop; record and move on
        logger.exception("adaptation cycle failed")
        summary.error = f"{type(exc).__name__}: {exc}"
        summary.decision = "error"

    return summary.finalize(datetime.now(timezone.utc))


def _temporal_split(records, holdout_fraction: float, *, horizon: timedelta = DEFAULT_LABEL_HORIZON):
    """Oldest rows train, most-recent ``holdout_fraction`` validate (realistic for temporal drift).

    Training rows are then embargoed: a row's label was built from events in ``(ts, ts + horizon]``,
    so if that window overlaps the holdout period the row's label encodes holdout-period behaviour.
    Those rows are dropped, leaving ``ts + horizon <= holdout_start`` for every training row.
    """
    ordered = sorted(records, key=lambda r: r.ts)
    n_holdout = max(1, int(round(len(ordered) * holdout_fraction))) if holdout_fraction > 0 else 0
    n_holdout = min(n_holdout, len(ordered) - 1)  # always leave at least one training row
    if n_holdout <= 0:
        return ordered, []

    train, holdout = ordered[:-n_holdout], ordered[-n_holdout:]
    holdout_start = holdout[0].ts
    return [r for r in train if r.ts + horizon <= holdout_start], holdout


def _span_hours(records) -> float:
    """Hours between the oldest and newest record (0 for fewer than two)."""
    if len(records) < 2:
        return 0.0
    stamps = [r.ts for r in records]
    return (max(stamps) - min(stamps)).total_seconds() / 3600.0


def _hours(span: timedelta) -> str:
    return f"{span.total_seconds() / 3600.0:g}"


def _resolve_incumbent(conn, config: CycleConfig, load_incumbent_fn: LoadIncumbentFn | None):
    """The currently-deployed model, or None (cold start, or its artifact is missing)."""
    from storage.context_store import get_active_model_version

    active = get_active_model_version(conn)
    if active is None:
        return None
    if load_incumbent_fn is not None:
        return load_incumbent_fn(active.version)
    # Default: load a saved LightGBM artifact for the active version, if one exists.
    try:
        return ImportanceModel.load(active.version, versions_dir=config.versions_dir)
    except (FileNotFoundError, ValueError):
        return None


def log_summary(summary: RunSummary, log_path: Path | str) -> None:
    """Append the run summary as one JSON line for later reporting."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary.to_dict(), separators=(",", ":")) + "\n")


# =============================================================================================
# CLI
# =============================================================================================

def _build_config(args: argparse.Namespace) -> CycleConfig:
    return CycleConfig(
        label_delay=timedelta(hours=args.label_delay_hours),
        label_horizon=timedelta(hours=args.label_horizon_hours),
        prototype_k=args.prototype_k,
        min_train_rows=args.min_train_rows,
        holdout_fraction=args.holdout_fraction,
        primary_metric=args.primary_metric,
        idle_seconds=args.idle_seconds,
        require_ac=not args.no_require_ac,
        versions_dir=Path(args.versions_dir) if args.versions_dir else DEFAULT_VERSIONS_DIR,
        require_new_labels=not args.retrain_without_new_labels,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="path to the SQLite context store")
    parser.add_argument("--summary-log", default="adaptation_runs.jsonl", help="JSONL file to append run summaries to")
    parser.add_argument("--force", action="store_true", help="run even if the host is not idle / on battery")
    parser.add_argument("--loop", action="store_true", help="poll until idle and keep running (daemon mode)")
    parser.add_argument("--poll-seconds", type=float, default=300.0, help="poll interval in --loop mode")
    parser.add_argument("--idle-seconds", type=float, default=300.0, help="required idle time before running")
    parser.add_argument("--no-require-ac", action="store_true", help="do not require AC power")
    parser.add_argument("--label-delay-hours", type=float, default=48.0,
                        help="wall-clock age before a record is eligible for labelling")
    parser.add_argument("--label-horizon-hours", type=float, default=48.0,
                        help="how far forward revisit/continuation may look; also the train/holdout embargo")
    parser.add_argument("--prototype-k", type=int, default=8)
    parser.add_argument("--min-train-rows", type=int, default=20)
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    parser.add_argument("--primary-metric", default="f1", choices=["f1", "precision", "recall", "accuracy"])
    parser.add_argument("--versions-dir", default=None)
    parser.add_argument("--retrain-without-new-labels", action="store_true",
                        help="retrain even if nothing was labelled since the last candidate")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = _build_config(args)

    from storage.database import connect, migrate

    conn = connect(args.db)
    migrate(conn)
    try:
        if args.loop:
            return _run_loop(conn, config, args)
        summary = run_if_ready(conn, config, force=args.force)
        _emit(summary, args.summary_log)
        return 0
    finally:
        conn.close()


def _run_loop(conn, config: CycleConfig, args: argparse.Namespace) -> int:
    logger.info("adaptation loop started; polling every %.0fs until idle", args.poll_seconds)
    while True:
        summary = run_if_ready(conn, config, force=args.force)
        if summary.ran:
            _emit(summary, args.summary_log)
        else:
            logger.info("skipped: %s", summary.skip_reason)
        time.sleep(args.poll_seconds)


def _emit(summary: RunSummary, log_path: str) -> None:
    log_summary(summary, log_path)
    s = summary
    detail = s.error or s.skip_reason
    if detail is None and s.decision == "skipped":
        detail = s.stages.get("retrain", {}).get("reason")
    elif detail is None and "validation" in s.stages:
        detail = s.stages["validation"]["reason"]
    logger.info(
        "cycle %s in %.1fs: labelled=%d decision=%s%s",
        "ran" if s.ran else "skipped", s.duration_seconds or 0.0, s.contexts_labelled, s.decision,
        f" ({detail})" if detail else "",
    )


if __name__ == "__main__":
    raise SystemExit(main())
