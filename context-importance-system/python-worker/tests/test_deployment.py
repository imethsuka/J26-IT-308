"""Tests for candidate validation, gated deployment, and rollback.

The deploy / keep-current branches are exercised with mocked metrics (an injected ``metrics_fn``),
so they do not depend on any real model or on the metric maths. Separate tests cover the metric
computation, the pure decision function, the version-history logging, and rollback.
"""

from __future__ import annotations

import pytest

from adaptation.deployment import (
    DEFAULT_PRIMARY_METRIC,
    RANKING_METRICS,
    ClassificationMetrics,
    budget_threshold,
    decide_deployment,
    evaluate_model,
    ranking_scores,
    rollback_to,
    validate_and_maybe_deploy,
)
from models.base import ImportanceScorer
from storage.context_store import (
    ModelVersion,
    get_active_model_version,
    get_model_version,
    insert_model_version,
)
from storage.database import connect, migrate


@pytest.fixture
def conn():
    c = connect(":memory:")
    migrate(c)
    yield c
    c.close()


class ConstantModel(ImportanceScorer):
    """A stand-in scorer returning a fixed probability, for evaluate_model tests."""

    def __init__(self, proba: float) -> None:
        self._p = proba

    def predict_proba(self, features):
        return self._p


def metrics(f1: float, *, precision: float = 0.5, recall: float = 0.5, accuracy: float = 0.5) -> ClassificationMetrics:
    """Hand-built metrics for mocking (confusion counts are irrelevant to the decision).

    The ranking metrics follow ``f1`` so the default (threshold-free) gate sees the same ordering.
    """
    return ClassificationMetrics(
        precision=precision, recall=recall, f1=f1, accuracy=accuracy,
        support=100, tp=0, fp=0, fn=0, tn=0, threshold=0.5,
        avg_precision=f1, roc_auc=f1, recall_at_keep_30=f1,
    )


def mock_metrics_fn(candidate_f1: float, current_f1: float):
    """metrics_fn that returns a chosen F1 for the candidate vs the current model."""

    candidate_marker = object()

    def fn(model, held_out, threshold):
        # First call in validate_and_maybe_deploy is the candidate, second is current; distinguish
        # by identity of the model object passed.
        return metrics(candidate_f1) if model is fn.candidate else metrics(current_f1)

    fn.candidate = candidate_marker
    return fn


# --------------------------------------------------------------------------- pure decision

class TestDecideDeployment:
    def test_deploys_when_strictly_better(self) -> None:
        deployed, reason = decide_deployment(metrics(0.80), metrics(0.70))
        assert deployed is True
        assert "0.8000" in reason and "0.7000" in reason and ">" in reason

    def test_keeps_current_on_tie(self) -> None:
        deployed, reason = decide_deployment(metrics(0.75), metrics(0.75))
        assert deployed is False
        assert "not strictly better" in reason

    def test_keeps_current_when_worse(self) -> None:
        deployed, _ = decide_deployment(metrics(0.60), metrics(0.75))
        assert deployed is False

    def test_cold_start_deploys(self) -> None:
        deployed, reason = decide_deployment(metrics(0.10), None)
        assert deployed is True
        assert "no incumbent" in reason

    def test_primary_metric_is_configurable(self) -> None:
        cand = metrics(0.9, precision=0.4)
        curr = metrics(0.5, precision=0.6)
        assert decide_deployment(cand, curr, primary_metric="f1")[0] is True       # better F1
        assert decide_deployment(cand, curr, primary_metric="precision")[0] is False  # worse precision

    def test_unknown_metric_rejected(self) -> None:
        with pytest.raises(ValueError):
            decide_deployment(metrics(0.9), metrics(0.5), primary_metric="auc")

    def test_default_gate_is_threshold_free(self) -> None:
        assert DEFAULT_PRIMARY_METRIC in RANKING_METRICS

    def test_ranking_better_deploys_even_when_f1_is_worse(self) -> None:
        # The case that kept every LightGBM candidate out: rules win F1 at 0.5 by keeping nearly everything,
        # the candidate orders contexts better.
        cand = ClassificationMetrics(0.86, 0.78, 0.8188, 0.74, 100, 0, 0, 0, 0, 0.5, avg_precision=0.85, roc_auc=0.8, recall_at_keep_30=0.46)
        rules = ClassificationMetrics(0.84, 0.80, 0.8192, 0.73, 100, 0, 0, 0, 0, 0.5, avg_precision=0.75, roc_auc=0.7, recall_at_keep_30=0.40)
        assert decide_deployment(cand, rules, primary_metric="f1")[0] is False
        deployed, reason = decide_deployment(cand, rules)
        assert deployed is True and "avg_precision" in reason

    def test_undefined_ranking_metric_keeps_current(self) -> None:
        one_class = ClassificationMetrics(1.0, 1.0, 1.0, 1.0, 10, 10, 0, 0, 0, 0.5)  # no ranking metrics
        deployed, reason = decide_deployment(one_class, metrics(0.5))
        assert deployed is False and "undefined" in reason


class TestRankingScores:
    def test_perfect_order_scores_one(self) -> None:
        out = ranking_scores([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1])
        assert out["avg_precision"] == 1.0 and out["roc_auc"] == 1.0 and out["recall_at_keep_30"] == 0.5

    def test_scale_does_not_matter(self) -> None:
        labels = [1, 0, 1, 0, 1, 0]
        scores = [0.9, 0.5, 0.7, 0.4, 0.6, 0.1]
        assert ranking_scores(labels, scores) == ranking_scores(labels, [x / 10 + 0.85 for x in scores])

    def test_single_class_is_undefined(self) -> None:
        assert ranking_scores([1, 1, 1], [0.1, 0.2, 0.3])["avg_precision"] is None

    def test_evaluate_model_includes_ranking(self) -> None:
        m = evaluate_model(ConstantModel(0.7), [({}, 1), ({}, 0)], threshold=0.5)
        assert m.avg_precision is not None and m.roc_auc == 0.5


# --------------------------------------------------------------------------- deploy / keep branches (mocked metrics)

class TestValidateAndMaybeDeploy:
    def _seed_active_v1(self, conn) -> int:
        return insert_model_version(conn, ModelVersion(version="v1", algorithm="lightgbm", is_active=True))

    def test_deploy_branch(self, conn) -> None:
        self._seed_active_v1(conn)
        fn = mock_metrics_fn(candidate_f1=0.85, current_f1=0.70)
        candidate, current = _tagged_models(fn)

        decision = validate_and_maybe_deploy(
            candidate, current, held_out_labelled_data=[({}, 1)],
            conn=conn, candidate_version="v2", metrics_fn=fn, algorithm="lightgbm",
        )

        assert decision.deployed is True
        assert decision.candidate_score == 0.85 and decision.current_score == 0.70
        # v2 is now the active version; v1 was deactivated.
        active = get_active_model_version(conn)
        assert active.version == "v2"
        # Comparison logged in the new version's metrics JSON.
        assert active.metrics["deployed"] is True
        assert active.metrics["candidate"]["f1"] == 0.85
        assert active.metrics["current_version"] == "v1"

    def test_keep_current_branch(self, conn) -> None:
        self._seed_active_v1(conn)
        fn = mock_metrics_fn(candidate_f1=0.65, current_f1=0.70)
        candidate, current = _tagged_models(fn)

        decision = validate_and_maybe_deploy(
            candidate, current, held_out_labelled_data=[({}, 1)],
            conn=conn, candidate_version="v2", metrics_fn=fn,
        )

        assert decision.deployed is False
        # v1 remains active; v2 is recorded but inactive.
        assert get_active_model_version(conn).version == "v1"
        v2 = get_model_version(conn, "v2")
        assert v2 is not None and v2.is_active is False
        assert v2.metrics["deployed"] is False
        assert "not strictly better" in v2.metrics["reason"]

    def test_tie_keeps_current(self, conn) -> None:
        self._seed_active_v1(conn)
        fn = mock_metrics_fn(candidate_f1=0.70, current_f1=0.70)
        candidate, current = _tagged_models(fn)
        decision = validate_and_maybe_deploy(
            candidate, current, [({}, 1)], conn=conn, candidate_version="v2", metrics_fn=fn
        )
        assert decision.deployed is False
        assert get_active_model_version(conn).version == "v1"

    def test_cold_start_deploys_and_logs(self, conn) -> None:
        fn = mock_metrics_fn(candidate_f1=0.30, current_f1=0.0)
        candidate, _ = _tagged_models(fn)
        decision = validate_and_maybe_deploy(
            candidate, None, [({}, 1)], conn=conn, candidate_version="v1", metrics_fn=fn
        )
        assert decision.deployed is True
        assert decision.current_metrics is None
        assert get_active_model_version(conn).version == "v1"


# --------------------------------------------------------------------------- rollback

class TestRollback:
    def test_rollback_to_prior_version(self, conn) -> None:
        insert_model_version(conn, ModelVersion(version="v1", is_active=True))
        insert_model_version(conn, ModelVersion(version="v2", is_active=True))  # v2 now active
        assert get_active_model_version(conn).version == "v2"

        active = rollback_to(conn, "v1")
        assert active.version == "v1"
        assert get_active_model_version(conn).version == "v1"
        # Exactly one active version.
        assert conn.execute("SELECT COUNT(*) FROM model_versions WHERE is_active=1").fetchone()[0] == 1

    def test_rollback_to_unknown_version_raises_and_keeps_active(self, conn) -> None:
        insert_model_version(conn, ModelVersion(version="v1", is_active=True))
        with pytest.raises(KeyError):
            rollback_to(conn, "does-not-exist")
        # The active version is untouched (we never deactivated before validating).
        assert get_active_model_version(conn).version == "v1"


# --------------------------------------------------------------------------- metric computation

class TestEvaluateModel:
    def test_perfect_classifier(self) -> None:
        held_out = [({"x": 1}, 1), ({"x": 1}, 1), ({"x": 0}, 0)]
        # Model predicts high for label-1 rows, low for label-0 rows via the feature.
        class M(ImportanceScorer):
            def predict_proba(self, f):
                return 0.9 if f["x"] == 1 else 0.1
        m = evaluate_model(M(), held_out)
        assert (m.precision, m.recall, m.f1, m.accuracy) == (1.0, 1.0, 1.0, 1.0)
        assert (m.tp, m.fp, m.fn, m.tn) == (2, 0, 0, 1)

    def test_all_negative_predictions_give_zero_precision_recall(self) -> None:
        held_out = [({}, 1), ({}, 1), ({}, 0)]
        m = evaluate_model(ConstantModel(0.0), held_out)
        assert m.precision == 0.0 and m.recall == 0.0 and m.f1 == 0.0
        assert m.fn == 2 and m.tn == 1

    def test_threshold_is_applied(self) -> None:
        held_out = [({}, 1), ({}, 0)]
        # proba 0.6: predicted 1 at threshold 0.5, predicted 0 at threshold 0.7.
        assert evaluate_model(ConstantModel(0.6), held_out, threshold=0.5).tp == 1
        assert evaluate_model(ConstantModel(0.6), held_out, threshold=0.7).tp == 0

    def test_accepts_dataframe(self) -> None:
        import pandas as pd
        df = pd.DataFrame([{"features": {"x": 1}, "label": 1}, {"features": {"x": 0}, "label": 0}])
        class M(ImportanceScorer):
            def predict_proba(self, f):
                return 0.9 if f["x"] == 1 else 0.1
        m = evaluate_model(M(), df)
        assert m.accuracy == 1.0


def test_from_confusion_handles_empty() -> None:
    m = ClassificationMetrics.from_confusion(0, 0, 0, 0, threshold=0.5)
    assert m.precision == 0.0 and m.recall == 0.0 and m.f1 == 0.0 and m.accuracy == 0.0


def _tagged_models(fn):
    """Return (candidate, current) scorers; the candidate is the object the mock recognises."""

    class _M(ImportanceScorer):
        def predict_proba(self, features):
            return 0.5

    candidate = _M()
    current = _M()
    fn.candidate = candidate  # the mock returns candidate_f1 for this exact object
    return candidate, current


class TestBudgetThreshold:
    def test_keeps_about_the_budgeted_share(self) -> None:
        scores = [i / 100 for i in range(100)]
        t = budget_threshold(scores, 0.30)
        assert 0.28 <= sum(s >= t for s in scores) / 100 <= 0.32

    def test_empty_or_bad_budget_is_none(self) -> None:
        assert budget_threshold([], 0.3) is None
        assert budget_threshold([0.5], 0.0) is None

    def test_deployment_records_the_threshold_and_it_is_read_back(self, conn) -> None:
        from pipeline import active_decision_threshold

        class Ramp(ImportanceScorer):
            def predict_proba(self, features):
                return features["x"]

        held_out = [({"x": i / 10}, int(i >= 5)) for i in range(10)]
        validate_and_maybe_deploy(Ramp(), None, held_out, conn=conn, candidate_version="v1", keep_rate=0.3)
        t = active_decision_threshold(conn)
        assert sum(x / 10 >= t for x in range(10)) == 3  # the top 30% of the held-out scores

    def test_no_budget_means_no_stored_threshold(self, conn) -> None:
        from pipeline import active_decision_threshold

        validate_and_maybe_deploy(ConstantModel(0.9), None, [({}, 1), ({}, 0)], conn=conn, candidate_version="v1", keep_rate=None)
        assert active_decision_threshold(conn) is None
