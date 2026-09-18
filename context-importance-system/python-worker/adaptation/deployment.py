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

PRIMARY_METRICS = ("f1", "precision", "recall", "accuracy")

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

    @classmethod
    def from_confusion(cls, tp: int, fp: int, fn: int, tn: int, *, threshold: float) -> "ClassificationMetrics":
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
        )

    def value(self, metric: str) -> float:
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
    candidate_score: float
    current_score: float | None
    candidate_metrics: ClassificationMetrics
    current_metrics: ClassificationMetrics | None
    candidate_version: str
    current_version: str | None
    logged_version_row_id: int | None


def evaluate_model(model: ImportanceScorer, held_out: LabelledData, threshold: float = 0.5) -> ClassificationMetrics:
    """Precision/recall/F1/accuracy of ``model`` on the held-out labelled set at ``threshold``."""
    tp = fp = fn = tn = 0
    for features, label in _iter_labelled(held_out):
        predicted = 1 if model.predict_proba(features) >= threshold else 0
        actual = int(label)
        if predicted == 1 and actual == 1:
            tp += 1
        elif predicted == 1 and actual == 0:
            fp += 1
        elif predicted == 0 and actual == 1:
            fn += 1
        else:
            tn += 1
    return ClassificationMetrics.from_confusion(tp, fp, fn, tn, threshold=threshold)


def decide_deployment(
    candidate_metrics: ClassificationMetrics,
    current_metrics: ClassificationMetrics | None,
    *,
    primary_metric: str = "f1",
) -> tuple[bool, str]:
    """
    Pure decision: deploy iff the candidate is *strictly better* on ``primary_metric``.

    A tie keeps the current model (avoid churning the deployment on no real improvement). If there
    is no current model (cold start), the candidate is deployed.
    """
    candidate_score = candidate_metrics.value(primary_metric)
    if current_metrics is None:
        return True, f"no incumbent model; deploying candidate ({primary_metric}={candidate_score:.4f})"

    current_score = current_metrics.value(primary_metric)
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
    primary_metric: str = "f1",
    threshold: float = 0.5,
    algorithm: str | None = None,
    notes: str | None = None,
    metrics_fn: MetricsFn | None = None,
) -> DeploymentDecision:
    """
    Score both models on ``held_out_labelled_data``, deploy the candidate only if strictly better on
    ``primary_metric``, and log the comparison to ``model_versions``.

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

    incumbent = get_active_model_version(conn)
    current_version = incumbent.version if incumbent else None

    comparison = {
        "primary_metric": primary_metric,
        "threshold": threshold,
        "n_held_out": len(materialized),
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
    from storage.context_store import get_model_version, set_active_model_version

    target = get_model_version(conn, version)
    if target is None:
        raise KeyError(f"model version {version!r} not found; cannot roll back to it")
    set_active_model_version(conn, target.id)
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
