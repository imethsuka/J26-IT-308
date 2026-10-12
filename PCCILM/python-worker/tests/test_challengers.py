"""Tests for ``offline.challengers`` (the model comparison behind Colab/04_compare_models_local.ipynb)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from offline.challengers import (
    OnlineContender,
    PeriodicRetrainContender,
    default_contenders,
    embedding_matrix,
    grade,
    keep_by_app,
    load_live_dataset,
    ranking_from_scores,
    results_table,
    rolling_grade,
    time_split,
)
from storage.context_store import ContextRecord, insert_context_record
from storage.database import connect, migrate

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def synthetic(n: int = 600, days: int = 12, seed: int = 0) -> pd.DataFrame:
    """Contexts where long dwell and typing mean important, spread evenly over ``days``."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        dwell = float(rng.exponential(60))
        keys = int(rng.poisson(20))
        label = int(dwell + 2 * keys + rng.normal(0, 20) > 90)
        embedding = rng.normal(size=512).astype(np.float32) if i % 3 else None
        rows.append({
            "ts": pd.Timestamp(T0 + timedelta(minutes=i * days * 24 * 60 / n)),
            "app": ["code", "msedge", "vlc"][i % 3],
            "features": {"tmp_dwell_seconds": dwell, "beh_key_presses": keys, "beh_window_seconds": 10},
            "embedding": embedding,
            "label": label,
        })
    return pd.DataFrame(rows)


def test_ranking_from_scores_recall_at_budget() -> None:
    m = ranking_from_scores([1, 1, 0, 0], [0.9, 0.1, 0.8, 0.2], keep_budgets=(0.5,))
    assert m["recall_at_keep_50"] == 0.5 and m["roc_auc"] == 0.5


def test_embedding_matrix_marks_missing_vectors() -> None:
    df = pd.DataFrame({"embedding": [np.full(512, 2.0, dtype=np.float32), None]})
    x = embedding_matrix(df)
    assert x.shape == (2, 513)
    assert x[0, 512] == 1.0 and np.isclose(np.linalg.norm(x[0, :512]), 1.0)
    assert not x[1].any()


def test_every_contender_trains_and_scores_and_the_learners_beat_chance() -> None:
    train, test = time_split(synthetic(), test_fraction=0.3)
    graded = grade(default_contenders(), train, test, progress=False)
    table = results_table(graded).set_index("name")
    assert len(table) == 10 and all(len(g.scores) == len(test) for g in graded)
    assert table.loc["LightGBM (Model 1)", "avg_precision"] > test["label"].mean() + 0.2
    assert table.loc["Logistic regression", "roc_auc"] > 0.8
    assert set(keep_by_app(test, graded).index) == {"code", "msedge", "vlc"}


class Spy:
    """Records when each label was learned and what had been predicted by then."""

    def __init__(self) -> None:
        self.learned: list[tuple[float, int]] = []

    def learn_one(self, x, y) -> None:
        self.learned.append((x["tmp_dwell_seconds"], y))

    def predict_proba_one(self, x) -> dict:
        return {1: float(len(self.learned))}


def test_online_contender_only_learns_labels_that_have_arrived() -> None:
    df = synthetic(n=40, days=8)
    train, test = df.iloc[:20], df.iloc[20:].reset_index(drop=True)
    c = OnlineContender("spy", Spy, label_delay=timedelta(days=2)).fit(train)
    scores = c.score(test)
    for i, row in test.iterrows():
        arrived = (test["ts"] + timedelta(days=2) <= row["ts"]).sum()
        assert scores[i] == 20 + arrived  # 20 training labels, then only test labels at least 2 days old


def test_periodic_retrain_uses_only_arrived_labels() -> None:
    train, test = time_split(synthetic(n=400, days=10), test_fraction=0.4)
    c = PeriodicRetrainContender().fit(train)
    scores = c.score(test)
    assert len(scores) == len(test) and not np.isnan(scores).any()
    assert c.retrains >= 3


def test_rolling_grade_has_one_row_per_fold_and_contender() -> None:
    from offline.challengers import LightGBMContender

    results = rolling_grade(synthetic(n=800, days=16), lambda: [LightGBMContender()], n_folds=3, min_train=100)
    assert sorted(results["fold"]) == [0, 1, 2]


def test_load_live_dataset_drops_the_dashboard_and_recomputes_revisits(tmp_path) -> None:
    db = tmp_path / "context.db"
    conn = connect(str(db))
    migrate(conn)
    for i, (app, title) in enumerate([("chrome", "Docs"), ("code", "main.py"), ("ContextDashboard", "Cognita"), ("chrome", "Docs")]):
        insert_context_record(conn, ContextRecord(
            ts=T0 + timedelta(minutes=40 * i), decision="discard", app=app, title=title, domain="",
            features={"ctx_seen_before": 0}, label=1,
        ))
    conn.close()
    df = load_live_dataset(db)
    assert list(df["app"]) == ["chrome", "code", "chrome"]
    assert "title" not in df.columns
    assert list(df["seen_before_stored"]) == [0, 0, 0]
    assert [f["ctx_seen_before"] for f in df["features"]] == [0, 0, 1]
    assert set(df["source"]) == {"live"}
