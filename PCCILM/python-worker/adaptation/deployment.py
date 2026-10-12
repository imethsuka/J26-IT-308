"""Candidate-model validation, gated deployment, and rollback.

A retrained candidate is only promoted if it is *strictly better* than the currently deployed model
on an agreed primary metric, measured on a held-out labelled set. Every comparison is written to the
``model_versions`` history table (Phase 6), and any prior version can be re-activated by id.

"Deployed" here means the active row in ``model_versions`` (``is_active = 1``). This module records
which version is active and the evidence for the choice; loading the actual model artifact for
serving (e.g. ``models.ImportanceModel.load``) is the caller's job.

The metric computation, the deploy/keep decision, and the persistence are separate functions so the
decision logic can be unit-tested with mocked metrics, independently of any model or database.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable

from models.base import Features, ImportanceScorer

# The first four depend on the 0.5 threshold. The last three only use the *order* of the scores, so a
# model and the rule-based baseline can be compared fairly even though their scores are calibrated
# differently (the rules keep ~85% of contexts at 0.5, which flatters them on F1).
THRESHOLD_METRICS = ("f1", "precision", "recall", "accuracy")
RANKING_METRICS = ("avg_precision", "roc_auc", "recall_at_keep_30")
PRIMARY_METRICS = THRESHOLD_METRICS + RANKING_METRICS
DEFAULT_PRIMARY_METRIC = "avg_precision"
KEEP_BUDGET = 0.30
# Share of contexts the deployed model may save. The save/discard threshold is the held-out score at this
# quantile, not a fixed 0.5 (a model whose scores sit high would otherwise keep nearly everything).
DEFAULT_KEEP_RATE = 0.30

# (features, label) pairs, or a pandas DataFrame with 'features' and 'label' columns.
LabelledData = Any
MetricsFn = Callable[[ImportanceScorer, Iterable, float], "ClassificationMetrics"]


@dataclass(frozen=True)
class ClassificationMetrics:
    precision: float
    recall: float
    f1: float
    accuracy: float
    support: int
    tp: int
    fp: int
    fn: int
    tn: int
    threshold: float
    # Threshold-free ranking metrics; None when not computed or undefined (e.g. one class in the set).
    avg_precision: float | None = None
    roc_auc: float | None = None
    recall_at_keep_30: float | None = None

    @classmethod
    def from_confusion(cls, tp: int, fp: int, fn: int, tn: int, *, threshold: float, **ranking: float | None) -> "ClassificationMetrics":
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        total = tp + fp + fn + tn
        accuracy = (tp + tn) / total if total else 0.0
        return cls(
            precision=round(precision, 6),
            recall=round(recall, 6),
            f1=round(f1, 6),
            accuracy=round(accuracy, 6),
            support=total,
            tp=tp, fp=fp, fn=fn, tn=tn,
            threshold=threshold,
            **ranking,
        )

    def value(self, metric: str) -> float | None:
        if metric not in PRIMARY_METRICS:
            raise ValueError(f"unknown metric {metric!r}; choose from {PRIMARY_METRICS}")
        return getattr(self, metric)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeploymentDecision:
    deployed: bool
    reason: str
    primary_metric: str
    candidate_score: float | None
    current_score: float | None
    candidate_metrics: ClassificationMetrics
    current_metrics: ClassificationMetrics | None
    candidate_version: str
    current_version: str | None
    logged_version_row_id: int | None


def ranking_scores(labels, scores, keep_budget: float = KEEP_BUDGET) -> dict[str, float | None]:
    """Average precision, ROC AUC and recall when only the top ``keep_budget`` share may be kept.

    AP / AUC are None when the set has one class only (undefined), so the gate cannot decide on them.
    """
    import numpy as np
    from sklearn.metrics import average_precision_score, roc_auc_score

    y = np.asarray(labels, dtype=int)
    s = np.asarray(scores, dtype=float)
    if len(y) == 0 or y.min() == y.max():
        return {"avg_precision": None, "roc_auc": None, "recall_at_keep_30": None}
    order = np.argsort(-s, kind="stable")
    kept = order[: int(round(keep_budget * len(y)))]
    return {
        "avg_precision": round(float(average_precision_score(y, s)), 6),
        "roc_auc": round(float(roc_auc_score(y, s)), 6),
        "recall_at_keep_30": round(float(y[kept].sum() / max(1, y.sum())), 6),
    }


def budget_threshold(scores, keep_rate: float = DEFAULT_KEEP_RATE) -> float | None:
    """The score such that about ``keep_rate`` of ``scores`` are at or above it (None if there are none)."""
    import numpy as np

    values = np.asarray(list(scores), dtype=float)
    if values.size == 0 or not 0 < keep_rate <= 1:
        return None
    return round(float(np.quantile(values, 1.0 - keep_rate)), 6)


def model_scores(model: ImportanceScorer, features: list[Features]) -> list[float]:
    """Scores of many contexts, in one batch when the model can (a forest scored one row at a time is slow)."""
    batch = getattr(model, "predict_proba_batch", None)
    if batch is not None and features:
        return [float(v) for v in batch(features)]
    return [float(model.predict_proba(f)) for f in features]


def evaluate_model(model: ImportanceScorer, held_out: LabelledData, threshold: float = 0.5) -> ClassificationMetrics:
    """Threshold metrics at ``threshold`` plus the threshold-free ranking metrics of ``model`` on the held-out set."""
    tp = fp = fn = tn = 0
    pairs = list(_iter_labelled(held_out))
    labels: list[int] = [int(label) for _, label in pairs]
    scores: list[float] = model_scores(model, [features for features, _ in pairs])
    for score, actual in zip(scores, labels):
        predicted = 1 if score >= threshold else 0
        if predicted == 1 and actual == 1:
            tp += 1
        elif predicted == 1 and actual == 0:
            fp += 1
        elif predicted == 0 and actual == 1:
            fn += 1
        else:
            tn += 1
    return ClassificationMetrics.from_confusion(tp, fp, fn, tn, threshold=threshold, **ranking_scores(labels, scores))


def decide_deployment(
    candidate_metrics: ClassificationMetrics,
    current_metrics: ClassificationMetrics | None,
    *,
    primary_metric: str = DEFAULT_PRIMARY_METRIC,
) -> tuple[bool, str]:
    """
    Pure decision: deploy iff the candidate is *strictly better* on ``primary_metric``.

    A tie keeps the current model (avoid churning the deployment on no real improvement). If there
    is no current model (cold start), the candidate is deployed. If the metric is undefined on this
    held-out set (a ranking metric with one class only), nothing is changed.
    """
    candidate_score = candidate_metrics.value(primary_metric)
    if candidate_score is None:
        return False, f"{primary_metric} is undefined on this held-out set (needs both classes); keeping current"
    if current_metrics is None:
        return True, f"no incumbent model; deploying candidate ({primary_metric}={candidate_score:.4f})"

    current_score = current_metrics.value(primary_metric)
    if current_score is None:
        return False, f"{primary_metric} is undefined for the current model on this held-out set; keeping current"
    if candidate_score > current_score:
        return True, f"candidate {primary_metric} {candidate_score:.4f} > current {current_score:.4f}"
    return False, f"candidate {primary_metric} {candidate_score:.4f} not strictly better than current {current_score:.4f}"


def validate_and_maybe_deploy(
    candidate_model: ImportanceScorer,
    current_model: ImportanceScorer | None,
    held_out_labelled_data: LabelledData,
    *,
    conn,
    candidate_version: str,
    primary_metric: str = DEFAULT_PRIMARY_METRIC,
    threshold: float = 0.5,
    algorithm: str | None = None,
    notes: str | None = None,
    metrics_fn: MetricsFn | None = None,
    keep_rate: float | None = DEFAULT_KEEP_RATE,
    deploy_allowed: bool = True,
    not_allowed_reason: str = "",
) -> DeploymentDecision:
    """
    Score both models on ``held_out_labelled_data``, deploy the candidate only if strictly better on
    ``primary_metric``, and log the comparison to ``model_versions``. With ``deploy_allowed`` False the candidate is
    graded and recorded the same way but never deployed (a challenger whose family is not the champion's).

    The candidate is always recorded (as the active version if deployed, otherwise inactive), with
    its metrics and the full comparison in the row's ``metrics`` JSON. ``metrics_fn`` can be injected
    to supply metrics directly (used by tests to mock them); it defaults to :func:`evaluate_model`.
    """
    if primary_metric not in PRIMARY_METRICS:
        raise ValueError(f"unknown primary_metric {primary_metric!r}; choose from {PRIMARY_METRICS}")

    from storage.context_store import ModelVersion, get_active_model_version, insert_model_version

    evaluate = metrics_fn or evaluate_model
    materialized = list(_iter_labelled(held_out_labelled_data))

    candidate_metrics = evaluate(candidate_model, materialized, threshold)
    current_metrics = evaluate(current_model, materialized, threshold) if current_model is not None else None

    deployed, reason = decide_deployment(candidate_metrics, current_metrics, primary_metric=primary_metric)
    if not deploy_allowed:
        reason = f"{not_allowed_reason or 'challenger, not deployed'}; {reason}"
        deployed = False
    decision_threshold = (
        budget_threshold(model_scores(candidate_model, [f for f, _ in materialized]), keep_rate) if keep_rate else None
    )

    incumbent = get_active_model_version(conn)
    current_version = incumbent.version if incumbent else None

    comparison = {
        "primary_metric": primary_metric,
        "threshold": threshold,
        "n_held_out": len(materialized),
        # What the worker saves with when this version is active (None: the worker's default threshold).
        "decision_threshold": decision_threshold,
        "keep_rate": keep_rate if decision_threshold is not None else None,
        "deployed": deployed,
        "reason": reason,
        "candidate": candidate_metrics.to_dict(),
        "current": current_metrics.to_dict() if current_metrics else None,
        "current_version": current_version,
        # Which scorer "current" measured: the deployed version, or the cold-start baseline.
        "current_model": getattr(current_model, "name", None) if current_model is not None else None,
    }

    row_id = insert_model_version(
        conn,
        ModelVersion(
            version=candidate_version,
            algorithm=algorithm,
            metrics=comparison,
            notes=notes,
            is_active=deployed,  # deploying makes this the sole active version
        ),
    )
    if deployed:
        from storage.context_store import record_champion_event

        record_champion_event(conn, from_version=current_version, to_version=candidate_version, algorithm=algorithm,
                              reason=f"deploy gate: {reason}")

    return DeploymentDecision(
        deployed=deployed,
        reason=reason,
        primary_metric=primary_metric,
        candidate_score=candidate_metrics.value(primary_metric),
        current_score=current_metrics.value(primary_metric) if current_metrics else None,
        candidate_metrics=candidate_metrics,
        current_metrics=current_metrics,
        candidate_version=candidate_version,
        current_version=current_version,
        logged_version_row_id=row_id,
    )


def rollback_to(conn, version: str):
    """
    Re-activate a prior model version by its version name. Raises ``KeyError`` if it does not exist
    (so we never end up with no active version). Returns the now-active ``ModelVersion``.
    """
    from storage.context_store import get_active_model_version, get_model_version, record_champion_event, set_active_model_version

    target = get_model_version(conn, version)
    if target is None:
        raise KeyError(f"model version {version!r} not found; cannot roll back to it")
    previous = get_active_model_version(conn)
    set_active_model_version(conn, target.id)
    record_champion_event(conn, from_version=previous.version if previous else None, to_version=version,
                          algorithm=target.algorithm, reason="rollback")
    return get_model_version(conn, version)


def _iter_labelled(held_out: LabelledData):
    """Yield (features, label) from a DataFrame ('features'/'label' columns) or an iterable of pairs."""
    columns = getattr(held_out, "columns", None)
    if columns is not None and "features" in columns and "label" in columns:
        for _, row in held_out.iterrows():
            yield row["features"], row["label"]
        return
    for item in held_out:
        features, label = item
        yield features, label
