"""Scheduled adaptation job.

Runs the maintenance pipeline in a fixed order, gated on the host being idle and on AC power:

    1. label construction   (adaptation.label_construction)      -> Phase 9
    2. prototype learning    (adaptation.prototype_learning)      -> Phase 10
    3. Model 1 retrain       (models.ImportanceModel.train)       -> Phase 8
    4. drift check           (adaptation.drift_detection)         -> Phase 11
    5. validation gate       (adaptation.deployment)              -> Phase 12
    6. screen memory         (adaptation.screen_memory)           -> optional, independent of 1-5 (--screen-memory)
    7. summaries             (adaptation.summarizer)              -> optional, after 6, local language model (--summarize)
    8. session + day notes   (adaptation.rollups)                 -> optional, after 7, same model (--summarize)

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

from app_paths import default_run_log_path
from models import registry
from models.challengers import with_embedding
from models.lightgbm_model import DEFAULT_VERSIONS_DIR, ImportanceModel
from models.rule_based import RuleBasedImportanceScorer

from .deployment import DEFAULT_PRIMARY_METRIC, PRIMARY_METRICS, validate_and_maybe_deploy
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
    # 'v3' (came back within the horizon, the default) or 'kw' (Keep-Worthiness, adaptation/kw_labels.py).
    label_rule: str = "v3"
    session_gap: timedelta = DEFAULT_SESSION_GAP
    retrieval_fn: RetrievalFn | None = None
    prototype_k: int = 8
    min_train_rows: int = 20
    holdout_fraction: float = 0.2
    num_boost_round: int = 100
    primary_metric: str = DEFAULT_PRIMARY_METRIC
    threshold: float = 0.5
    drift_delta: float = 0.002
    versions_dir: Path = field(default_factory=lambda: DEFAULT_VERSIONS_DIR)
    idle_seconds: float = 300.0
    require_ac: bool = True
    # Skip the retrain when nothing was labelled since the latest candidate was trained.
    require_new_labels: bool = True
    # Optional stage 6: turn buffered screen text into memory entries (adaptation.screen_memory.ScreenMemoryJob).
    screen_memory: Any = None
    # Optional stage 7: a small local language model writes a sentence for each important memory (adaptation.summarizer).
    summarizer: Any = None
    # Optional stage 8: a title, tag and TLDR per work session and a recap per day (adaptation.rollups), from stage 7's sentences.
    rollups: Any = None
    # Stage 5b: train the other models too (models.registry.CHALLENGERS), for shadow mode and the benchmark page.
    challengers: bool = True


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

    summary = run_cycle(conn, config, now=now, base_summary=summary, load_incumbent_fn=load_incumbent_fn)
    if config.screen_memory is not None:
        _run_screen_memory_stage(config, summary, now)
    if config.summarizer is not None or config.rollups is not None:
        busy = None if force else _llm_load_check(config)
        if busy is not None:
            logger.info("memory model not started: %s", busy)
            summary.stages["summaries"] = {"skipped": busy}
        else:
            try:
                if config.summarizer is not None:
                    _run_summarizer_stage(config, summary)
                if config.rollups is not None:
                    _run_rollups_stage(config, summary)
            finally:
                _close_model(config)  # nothing stays in RAM or VRAM between runs
    return summary


def _llm_model(config: CycleConfig) -> Any:
    stage = config.summarizer or config.rollups
    return getattr(stage, "model_factory", None)


def _llm_load_check(config: CycleConfig) -> str | None:
    """Why the PC is too busy for the local model right now, or None. A failed measurement does not block the run."""
    from .host_state import check_light_load

    model = _llm_model(config)
    try:
        return check_light_load(getattr(model, "size_bytes", 0))
    except (OSError, NotImplementedError):
        return None


def _close_model(config: CycleConfig) -> None:
    close = getattr(_llm_model(config), "close", None)
    if close is not None:
        close()


def _run_rollups_stage(config: CycleConfig, summary: RunSummary) -> None:
    """Stage 8. Runs after stage 7 so sentences written in this run are used. Its failure changes nothing else."""
    from .rollups import run_rollups

    try:
        report = run_rollups(config.rollups)
        summary.stages["rollups"] = report
        logger.info("rollups: %d session notes, %d day recaps (%d sessions seen)",
                    report["session_notes"], report["day_notes"], report["sessions_seen"])
    except Exception as exc:
        logger.exception("rollup stage failed")
        summary.stages["rollups"] = {"error": f"{type(exc).__name__}: {exc}"}


def _run_summarizer_stage(config: CycleConfig, summary: RunSummary) -> None:
    """Stage 7. Runs after stage 6 so entries written in this run are summarised in this run. Its failure changes nothing else."""
    from .summarizer import run_summaries

    try:
        report = run_summaries(config.summarizer)
        summary.stages["summaries"] = report
        logger.info("summaries: %d written, %d discarded, %d failed (%d were waiting)",
                    report["summarized"], report["discarded"], report["failed"], report["waiting"])
    except Exception as exc:
        logger.exception("summary stage failed")
        summary.stages["summaries"] = {"error": f"{type(exc).__name__}: {exc}"}


def _run_screen_memory_stage(config: CycleConfig, summary: RunSummary, now: datetime | None) -> None:
    """Stage 6. Independent of the learning stages: its failure never changes their outcome, and theirs never skips it."""
    from .screen_memory import run_screen_memory

    try:
        report = run_screen_memory(config.screen_memory, now=now)
        summary.stages["screen_memory"] = report
        logger.info(
            "screen memory: %d entries from %d captures (tiers %s), %d episode(s) still open",
            report["entries_written"], report["captures_read"], report["tiers"], report["episodes_left_open"],
        )
    except Exception as exc:
        logger.exception("screen memory stage failed")
        summary.stages["screen_memory"] = {"error": f"{type(exc).__name__}: {exc}"}


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

    from features import surfaces
    from features.feature_assembly import refresh_revisit_features

    wall_start = datetime.now(timezone.utc)
    summary = base_summary or RunSummary(started_at=wall_start)
    logical_now = now or summary.started_at
    summary.ran = True
    candidate_version = f"lightgbm-{logical_now.strftime('%Y%m%d-%H%M%S')}"
    summary.candidate_version = candidate_version

    try:
        # --- Stage 1: label construction ------------------------------------------------------
        constructor_class = LabelConstructor
        constructor_args = {"threshold": config.threshold}
        if config.label_rule == "kw":
            from adaptation.kw_labels import DEFAULT_KW_PARAMS, KWLabelConstructor

            constructor_class = KWLabelConstructor
            constructor_args = {"threshold": DEFAULT_KW_PARAMS.threshold}  # KW has its own scale
        constructor = constructor_class(
            label_delay=config.label_delay, label_horizon=config.label_horizon,
            session_gap=config.session_gap, retrieval_fn=config.retrieval_fn, **constructor_args,
        )
        labelled = label_pending_records(conn, constructor, now=logical_now, write=True)
        newly_labelled_ids = [rid for rid, _ in labelled]
        summary.contexts_labelled = len(labelled)

        # Older records include shell surfaces and this app's own window, which the worker no longer stores, and
        # revisit features from the old 200-snapshot history. Train on what the worker serves now.
        records = [r for r in list_context_records(conn) if not surfaces.is_non_context(r.app, r.title)]
        refresh_revisit_features(records)
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
        # The embedding rides along in memory for the models that use it (models.challengers.EMBEDDING_KEY).
        holdout = [(with_embedding(r.features, r.embedding), r.label) for r in holdout_records]
        # The gate only upgrades the champion within its own family: a champion chosen on the dashboard is never
        # replaced by another kind of model automatically.
        champion_algorithm = _champion_algorithm(conn)
        decision = validate_and_maybe_deploy(
            candidate, current, holdout, conn=conn, candidate_version=candidate_version,
            primary_metric=config.primary_metric, threshold=config.threshold, algorithm="lightgbm",
            notes=f"scheduled cycle {logical_now.isoformat()}",
            deploy_allowed=champion_algorithm == "lightgbm",
            not_allowed_reason=f"challenger: the champion is {champion_algorithm}",
        )
        summary.stages["validation"] = {
            "deployed": decision.deployed, "reason": decision.reason,
            "primary_metric": decision.primary_metric, "compared_against": current.name,
            "candidate_score": decision.candidate_score, "current_score": decision.current_score,
        }
        summary.decision = "deploy" if decision.deployed else "keep"

        # --- Stage 5b: challengers (the other models, for shadow mode and the benchmark) ----------
        if config.challengers:
            deployed_challenger = _train_challengers(
                conn, config, summary, train_records, holdout, current, proto_result,
                stamp=candidate_version.removeprefix("lightgbm-"), champion_algorithm=champion_algorithm,
                notes=f"scheduled cycle {logical_now.isoformat()}",
            )
            if deployed_challenger:
                summary.decision = "deploy"

    except Exception as exc:  # a scheduled job must not crash the scheduler/loop; record and move on
        logger.exception("adaptation cycle failed")
        summary.error = f"{type(exc).__name__}: {exc}"
        summary.decision = "error"

    return summary.finalize(datetime.now(timezone.utc))


def _champion_algorithm(conn) -> str:
    """The champion's family: the active version's algorithm, or LightGBM at cold start (the gate then measures
    LightGBM against the rule-based baseline, as before challengers)."""
    from storage.context_store import get_active_model_version

    active = get_active_model_version(conn)
    return registry.algorithm_of(active.algorithm) if active is not None else registry.LIGHTGBM


def _train_challengers(conn, config: CycleConfig, summary: RunSummary, train_records, holdout, current, proto_result,
                       *, stamp: str, champion_algorithm: str, notes: str) -> bool:
    """Train, save and grade every challenger on the same split as LightGBM. Each becomes a version (inactive unless it
    is the champion's family and strictly better than the champion). River continues from its newest version with
    only the labels it has not seen. One failing challenger never stops the others or the cycle. Returns whether one
    was deployed."""
    import pandas as pd

    from storage.context_store import latest_versions_by_algorithm

    frame = pd.DataFrame({
        "features": [r.features for r in train_records], "embedding": [r.embedding for r in train_records],
        "label": [r.label for r in train_records], "ts": [r.ts for r in train_records],
    })
    report: dict[str, Any] = {}
    deployed = False
    for algorithm in registry.CHALLENGERS:
        started = time.perf_counter()
        version = f"{algorithm}-{stamp}"
        try:
            if algorithm == "river_arf":
                previous = latest_versions_by_algorithm(conn).get(algorithm)
                scorer = (registry.load_scorer(algorithm, previous.version, config.versions_dir)
                          if previous is not None else registry.new_scorer(algorithm))
                learned = scorer.learn(frame)
                info = {"continued_from": previous.version if previous else None, "newly_learned": learned}
            else:
                scorer, info = registry.train_scorer(algorithm, frame)
            registry.save_scorer(scorer, algorithm, version, config.versions_dir)
            if proto_result is not None:  # any model can become champion, so each version carries the prototypes
                save_prototypes(proto_result, version, versions_dir=config.versions_dir, metadata={"source_model": version})
            decision = validate_and_maybe_deploy(
                scorer, current, holdout, conn=conn, candidate_version=version, primary_metric=config.primary_metric,
                threshold=config.threshold, algorithm=algorithm, notes=notes,
                deploy_allowed=algorithm == champion_algorithm,
                not_allowed_reason=f"challenger: the champion is {champion_algorithm}",
            )
            deployed = deployed or decision.deployed
            report[algorithm] = {
                "version": version, "deployed": decision.deployed, "score": decision.candidate_score,
                "seconds": round(time.perf_counter() - started, 1),
                **{k: v for k, v in info.items() if k in ("n_train", "continued_from", "newly_learned")},
            }
        except Exception as exc:
            logger.exception("challenger %s failed", algorithm)
            report[algorithm] = {"error": f"{type(exc).__name__}: {exc}"}
    summary.stages["challengers"] = report
    logger.info("challengers: %s", ", ".join(
        f"{a} {r['score']:.3f}" if r.get("score") is not None else f"{a} {r.get('error', 'no score')}" for a, r in report.items()))
    return deployed


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
    # Default: load the saved artifact of the active version, whatever its algorithm, if one exists.
    try:
        return registry.load_scorer(active.algorithm, active.version, config.versions_dir)
    except (FileNotFoundError, ValueError, ImportError):
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
    summarizer, rollups = _build_llm_stages(args)
    return CycleConfig(
        label_delay=timedelta(hours=args.label_delay_hours),
        label_horizon=timedelta(hours=args.label_horizon_hours),
        label_rule=args.label_rule,
        prototype_k=args.prototype_k,
        min_train_rows=args.min_train_rows,
        holdout_fraction=args.holdout_fraction,
        primary_metric=args.primary_metric,
        idle_seconds=args.idle_seconds,
        require_ac=not args.no_require_ac,
        versions_dir=Path(args.versions_dir) if args.versions_dir else DEFAULT_VERSIONS_DIR,
        challengers=not args.no_challengers,
        require_new_labels=not args.retrain_without_new_labels,
        screen_memory=_build_screen_memory(args),
        summarizer=summarizer,
        rollups=rollups,
    )


def _build_llm_stages(args: argparse.Namespace):
    """(summary stage, rollup stage); both None unless --summarize and a model file exists. They share one loaded model."""
    if not getattr(args, "summarize", False):
        return None, None
    model_path = Path(args.llm_model) if args.llm_model else None
    if model_path is None or not (model_path.is_file() or any(model_path.glob("*.gguf"))):
        logger.warning("--summarize given but the language model file was not found (%s): summaries are skipped", model_path)
        return None, None
    from storage.memory_store import MemoryStore, memory_path_for

    from .llm_runtime import LocalModel
    from .rollups import RollupConfig, RollupJob
    from .summarizer import SummarizeConfig, SummarizeJob

    memory = MemoryStore(args.memory_db or memory_path_for(args.db))
    # Started on first use on a dedicated GPU or the CPU, paused if the user comes back, stopped after each run.
    shared_model = LocalModel(model_path, background=not args.force)

    return (SummarizeJob(memory=memory, model_factory=shared_model, config=SummarizeConfig(max_entries=args.summary_max_entries)),
            RollupJob(memory=memory, model_factory=shared_model, config=RollupConfig()))


def _build_screen_memory(args: argparse.Namespace):
    """The optional screen-memory stage, or None. Files sit beside ``--db`` unless given."""
    if not args.screen_memory:
        return None
    from storage.memory_store import MemoryStore, memory_path_for
    from storage.raw_buffer import RawBuffer, raw_buffer_path_for

    from .privacy import PassThroughFilter
    from .screen_memory import ScreenMemoryConfig, ScreenMemoryJob

    privacy_filter = None
    if getattr(args, "privacy_url", None):
        # CAPRLM, running on this PC. If it is not up, no text is kept at all (metadata-only entries): never unfiltered.
        from .caprlm_client import CaprlmPrivacyFilter

        client = CaprlmPrivacyFilter(args.privacy_url)
        if client.available():
            privacy_filter = client
            if args.dev_passthrough_filter:
                logger.warning("--privacy-url given: the CAPRLM filter is used and --dev-passthrough-filter is ignored")
        else:
            logger.warning("privacy service at %s is not reachable: no screen text will be kept in this run", args.privacy_url)
    elif args.dev_passthrough_filter:
        logger.warning("DEV ONLY: the pass-through privacy filter stores screen text unredacted; use it on your own data only")
        privacy_filter = PassThroughFilter()
    return ScreenMemoryJob(
        raw=RawBuffer(args.raw_db or raw_buffer_path_for(args.db)),
        memory=MemoryStore(args.memory_db or memory_path_for(args.db)),
        config=ScreenMemoryConfig(
            high_threshold=args.sm_high_threshold, medium_threshold=args.sm_medium_threshold, privacy_filter=privacy_filter,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="adapt.exe memory-llm --memory-db ... --llm-model ...: only write memory sentences and notes (memory_llm.py). "
               "adapt.exe champion --db ... set <version>: make a saved model the champion (adaptation/champion.py)",
    )
    parser.add_argument("--db", required=True, help="path to the SQLite context store")
    parser.add_argument("--summary-log", default=default_run_log_path(), help="JSONL file to append run summaries to")
    parser.add_argument("--force", action="store_true", help="run even if the host is not idle / on battery")
    parser.add_argument("--loop", action="store_true", help="poll until idle and keep running (daemon mode)")
    parser.add_argument("--poll-seconds", type=float, default=300.0, help="poll interval in --loop mode")
    parser.add_argument("--idle-seconds", type=float, default=300.0, help="required idle time before running")
    parser.add_argument("--no-require-ac", action="store_true", help="do not require AC power")
    parser.add_argument("--label-delay-hours", type=float, default=48.0,
                        help="wall-clock age before a record is eligible for labelling")
    parser.add_argument("--label-horizon-hours", type=float, default=48.0,
                        help="how far forward revisit/continuation may look; also the train/holdout embargo")
    parser.add_argument("--label-rule", choices=["v3", "kw"], default="v3",
                        help="v3: came back within the horizon (default). kw: Keep-Worthiness label")
    parser.add_argument("--prototype-k", type=int, default=8)
    parser.add_argument("--min-train-rows", type=int, default=20)
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    parser.add_argument("--primary-metric", default=DEFAULT_PRIMARY_METRIC, choices=list(PRIMARY_METRICS))
    parser.add_argument("--versions-dir", default=None)
    parser.add_argument("--no-challengers", action="store_true",
                        help="train LightGBM only (by default every challenger model is trained too, for shadow mode)")
    parser.add_argument("--retrain-without-new-labels", action="store_true",
                        help="retrain even if nothing was labelled since the last candidate")
    parser.add_argument("--screen-memory", action="store_true",
                        help="also turn buffered screen text into memory entries (needs the collector's screenContent on)")
    parser.add_argument("--raw-db", default=None, help="raw screen-text buffer (default: raw_buffer.db beside --db)")
    parser.add_argument("--memory-db", default=None, help="memory store (default: memory.db beside --db)")
    parser.add_argument("--privacy-url", default=None,
                        help="CAPRLM's local service (e.g. http://127.0.0.1:8787) used as the privacy filter; loopback only")
    parser.add_argument("--dev-passthrough-filter", action="store_true",
                        help="DEV ONLY: use no redaction. Without a privacy filter no screen text is stored")
    parser.add_argument("--summarize", action="store_true",
                        help="also write a sentence for each important memory with a local language model (needs --llm-model)")
    parser.add_argument("--llm-model", default=None,
                        help="a GGUF chat model, or a folder of them (the GPU gets the largest that fits, the CPU the smallest)")
    parser.add_argument("--summary-max-entries", type=int, default=25, help="most memories to summarise per run")
    parser.add_argument("--sm-high-threshold", type=float, default=0.5, help="episode importance for the high tier")
    parser.add_argument("--sm-medium-threshold", type=float, default=0.2, help="episode importance for the medium tier")
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
