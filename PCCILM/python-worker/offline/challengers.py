"""Model comparison for the charter (task 6): Model 1 (LightGBM) against rule-based, conventional ML, transfer
learning and continual learning, on the live database. Used by ``Colab/04_compare_models_local.ipynb``.

Every contender has the same two calls, ``fit(train_df)`` and ``score(df) -> np.ndarray``, so one evaluation grades
them all on the same split with the same threshold-free metrics (:func:`ranking_from_scores`). ``df`` is a frame from
:func:`load_live_dataset`: one row per stored context with ``ts``, ``app``, ``features`` (dict), ``embedding`` (512-d or
None) and ``label``.

| Family | Contender | What it shows |
|---|---|---|
| baseline | rules, dwell >= 30 s | what a hand-written policy gets |
| conventional ML | LightGBM (Model 1), logistic regression, random forest, small neural network | which learner fits best |
| transfer learning | frozen MobileCLIP vector + logistic regression | what a model pre-trained on images adds |
| continual learning | River adaptive random forest, LightGBM retrained daily | learning while in use, labels 48 h late |

Offline only: the transfer-learning contender needs the raw embedding, which the live worker keeps out of the
feature dict, and none of the new contenders can be deployed yet.
"""

from __future__ import annotations

import pickle
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from adaptation.label_construction import DEFAULT_LABEL_HORIZON
from features import surfaces
from features.feature_assembly import refresh_revisit_features
from models.vectorizer import FEATURE_COLUMNS, vectorize_many

EMBEDDING_DIM = 512


# =============================================================================================
# 1. Data
# =============================================================================================

def snapshot_database(live_db: Path | str, copy_path: Path | str) -> Path:
    """A consistent copy of the live database (SQLite backup API; the live file is opened read-only)."""
    copy_path = Path(copy_path)
    copy_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{Path(live_db).as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(copy_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return copy_path


def load_live_dataset(db_path: Path | str) -> pd.DataFrame:
    """Every stored context, the way the scheduled retrain sees it: shell surfaces and the dashboard's own window left
    out, revisit features recomputed (``refresh_revisit_features``). ``seen_before_stored`` keeps the value the worker
    stored, for comparison. Titles and websites are dropped from the frame, so tables never show them."""
    from storage.context_store import list_context_records
    from storage.database import connect

    conn = connect(str(db_path))
    try:
        records = [r for r in list_context_records(conn) if not surfaces.is_non_context(r.app, r.title)]
        has_sources = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'record_sources'").fetchone()
        drc_before = conn.execute(
            "SELECT MAX(ts_before) FROM record_sources WHERE source = 'drc'").fetchone()[0] if has_sources else None
    finally:
        conn.close()
    stored_seen = [r.features.get("ctx_seen_before") for r in records]
    refresh_revisit_features(records)
    from storage.database import from_epoch_us

    cutoff = from_epoch_us(drc_before) if drc_before is not None else None
    return pd.DataFrame({
        "id": [r.id for r in records],
        "ts": [r.ts for r in records],
        "source": ["drc" if cutoff is not None and r.ts < cutoff else "live" for r in records],
        "app": [r.app for r in records],
        "decision": [r.decision for r in records],
        "features": [r.features for r in records],
        "embedding": [r.embedding for r in records],
        "label": [r.label for r in records],
        "label_version": [r.label_version for r in records],
        "seen_before_stored": stored_seen,
    })


def time_split(df: pd.DataFrame, *, test_fraction: float = 0.2, embargo: timedelta = DEFAULT_LABEL_HORIZON) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Labelled rows, oldest train and newest ``test_fraction`` test, with the label-horizon embargo of the scheduled
    retrain (a training row's 48 h label window must end before the test period starts)."""
    from offline.training import temporal_split

    return temporal_split(df, test_fraction=test_fraction, embargo=embargo)


# =============================================================================================
# 2. Metrics
# =============================================================================================

def ranking_from_scores(labels: Sequence[int], scores: Sequence[float], keep_budgets: Sequence[float] = (0.3, 0.5)) -> dict[str, float]:
    """AUC, average precision, and recall when only the top X of contexts may be kept (a storage budget). None of
    them depends on a threshold, so models calibrated differently are compared fairly."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    y = np.asarray(labels, dtype=int)
    s = np.asarray(scores, dtype=float)
    out = {"roc_auc": float(roc_auc_score(y, s)), "avg_precision": float(average_precision_score(y, s))}
    order = np.argsort(-s, kind="stable")
    for budget in keep_budgets:
        kept = order[: int(round(budget * len(y)))]
        out[f"recall_at_keep_{int(round(budget * 100))}"] = float(y[kept].sum() / max(1, y.sum()))
    return out


# =============================================================================================
# 3. Contenders
# =============================================================================================

def feature_matrix(df: pd.DataFrame) -> np.ndarray:
    """The pinned Model 1 inputs (``FEATURE_COLUMNS``), one row per context."""
    return vectorize_many(list(df["features"]))


def embedding_matrix(df: pd.DataFrame) -> np.ndarray:
    """The 512 MobileCLIP numbers per context, unit length; zeros (and ``has_embedding`` 0) where there is none.
    The same transform the live challengers use (``models.challengers.embedding_rows``)."""
    from models.challengers import embedding_rows

    return embedding_rows(list(df["embedding"]))


class Contender:
    name = "contender"
    family = ""
    note = ""

    def fit(self, train_df: pd.DataFrame) -> "Contender":
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def size_bytes(self) -> int:
        try:
            return len(pickle.dumps(self))
        except Exception:
            return 0


class ScorerContender(Contender):
    """A fixed ``ImportanceScorer`` (the rules, a dwell threshold): nothing to train."""

    def __init__(self, name: str, family: str, scorer: Any, note: str = "") -> None:
        self.name, self.family, self.note, self.scorer = name, family, note, scorer

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return np.array([self.scorer.predict_proba(f) for f in df["features"]], dtype=float)


class LightGBMContender(Contender):
    """Model 1 as the scheduled retrain trains it (``models.lightgbm_model.ImportanceModel``)."""

    def __init__(self, name: str = "LightGBM (Model 1)", family: str = "conventional ML", note: str = "deployed today") -> None:
        self.name, self.family, self.note = name, family, note
        self.model = None

    def fit(self, train_df: pd.DataFrame) -> "LightGBMContender":
        from models.lightgbm_model import ImportanceModel

        self.model = ImportanceModel()
        self.model.train(pd.DataFrame({"features": list(train_df["features"]), "label": train_df["label"].astype(int).to_numpy()}))
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba_batch(list(df["features"]))

    def size_bytes(self) -> int:
        return len(pickle.dumps(self.model))


class SklearnContender(Contender):
    """One of the scikit-learn challengers exactly as the live app trains and runs it (``models.challengers``)."""

    def __init__(self, algorithm: str, note: str = "") -> None:
        from models.registry import ALGORITHMS

        self.algorithm, self.note = algorithm, note
        self.name, self.family = ALGORITHMS[algorithm].display, ALGORITHMS[algorithm].family
        self.model = None

    def fit(self, train_df: pd.DataFrame) -> "SklearnContender":
        from models.challengers import SklearnScorer

        self.model = SklearnScorer(self.algorithm)
        self.model.train(train_df)
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba_batch(list(df["features"]), list(df["embedding"]))

    def size_bytes(self) -> int:
        return len(pickle.dumps(self.model.estimator))


class OnlineContender(Contender):
    """Continual learning with River: learns one context at a time. While it scores the test period it keeps
    learning, but a context's label only arrives ``label_delay`` after it was seen (48 h, as in real use), so it never
    learns from an answer it could not have had yet."""

    def __init__(self, name: str, make: Callable[[], Any], *, label_delay: timedelta = DEFAULT_LABEL_HORIZON,
                 learn_during_test: bool = True, note: str = "") -> None:
        self.name, self.family, self.note = name, "continual learning", note
        self.make, self.label_delay, self.learn_during_test = make, label_delay, learn_during_test
        self.model = None

    @staticmethod
    def _rows(df: pd.DataFrame) -> list[dict[str, float]]:
        return [dict(zip(FEATURE_COLUMNS, map(float, row))) for row in feature_matrix(df)]

    def _p(self, x: dict[str, float]) -> float:
        return float(self.model.predict_proba_one(x).get(1, 0.5))

    def fit(self, train_df: pd.DataFrame) -> "OnlineContender":
        self.model = self.make()
        ordered = train_df.sort_values("ts", kind="stable")
        for x, y in zip(self._rows(ordered), ordered["label"].astype(int)):
            self.model.learn_one(x, int(y))
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        xs, ts, ys = self._rows(df), list(df["ts"]), df["label"].tolist()
        out = np.empty(len(df), dtype=float)
        pending: deque[tuple[Any, dict[str, float], int]] = deque()  # (label arrives at, x, label), in arrival order
        for i in sorted(range(len(df)), key=lambda k: ts[k]):
            while pending and pending[0][0] <= ts[i]:
                _, x, y = pending.popleft()
                self.model.learn_one(x, y)
            out[i] = self._p(xs[i])
            if self.learn_during_test and ys[i] is not None and not pd.isna(ys[i]):
                pending.append((ts[i] + self.label_delay, xs[i], int(ys[i])))
        return out

    def size_bytes(self) -> int:
        return len(pickle.dumps(self.model))


class PeriodicRetrainContender(Contender):
    """LightGBM retrained from scratch every ``every`` on all labels that have arrived (``label_delay`` after each
    context), like the scheduled learning loop (Model 2). The fair partner of :class:`OnlineContender`."""

    def __init__(self, name: str = "LightGBM, retrained daily", *, every: timedelta = timedelta(days=1),
                 label_delay: timedelta = DEFAULT_LABEL_HORIZON, note: str = "what Model 2 does") -> None:
        self.name, self.family, self.note = name, "continual learning", note
        self.every, self.label_delay = every, label_delay
        self.train_df: pd.DataFrame | None = None
        self.retrains = 0
        self.last: LightGBMContender | None = None

    def fit(self, train_df: pd.DataFrame) -> "PeriodicRetrainContender":
        self.train_df = train_df
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        ordered = df.sort_values("ts", kind="stable")
        out = pd.Series(np.nan, index=df.index, dtype=float)
        start = ordered["ts"].iloc[0]
        block_start = start
        while block_start <= ordered["ts"].iloc[-1]:
            block_end = block_start + self.every
            block = ordered[(ordered["ts"] >= block_start) & (ordered["ts"] < block_end)]
            if not block.empty:
                arrived = ordered[(ordered["ts"] + self.label_delay <= block_start) & ordered["label"].notna()]
                known = pd.concat([self.train_df, arrived])
                self.last = LightGBMContender().fit(known)
                self.retrains += 1
                out.loc[block.index] = self.last.score(block)
            block_start = block_end
        return out.loc[df.index].to_numpy()

    def size_bytes(self) -> int:
        return self.last.size_bytes() if self.last is not None else 0


def default_contenders() -> list[Contender]:
    """A fresh set of every contender (they are stateful, so each evaluation needs its own). The River contender is
    left out, with a note, when River is not installed."""
    try:
        from models.challengers import river_model
    except ImportError:
        river_model = None
    from models.rule_based import RuleBasedImportanceScorer
    from models.static_threshold import StaticThresholdScorer

    contenders = [
        ScorerContender("Rules", "baseline", RuleBasedImportanceScorer(), "hand-written, served before any model"),
        ScorerContender("Dwell >= 30 s", "baseline", StaticThresholdScorer(), "one fixed rule"),
        LightGBMContender(),
        SklearnContender("logreg", "straight-line model"),
        SklearnContender("random_forest", "averaged trees (bagging)"),
        SklearnContender("mlp", "2 small hidden layers"),
        SklearnContender("mobileclip_logreg", "what the screen looks like, nothing else"),
        SklearnContender("features_mobileclip_logreg", "frozen pre-trained eyes, trained head"),
        PeriodicRetrainContender(),
    ]
    try:
        import river  # noqa: F401
    except ImportError:
        river_model = None
    if river_model is None:
        print("River is not installed, so the 'River adaptive random forest' contender is skipped (pip install river)")
    else:
        contenders.insert(-1, OnlineContender("River adaptive random forest", river_model, note="learns one context at a time"))
    return contenders


# =============================================================================================
# 4. Evaluation
# =============================================================================================

@dataclass
class Graded:
    contender: Contender
    scores: np.ndarray
    metrics: dict[str, Any]


def grade(contenders: Sequence[Contender], train: pd.DataFrame, test: pd.DataFrame, *,
          keep_budgets: Sequence[float] = (0.3, 0.5), progress: bool = True) -> list[Graded]:
    """Train each contender on ``train`` and grade it on ``test``: ranking metrics, training time, scoring speed,
    model size."""
    y = test["label"].astype(int).to_numpy()
    graded = []
    for c in contenders:
        started = time.perf_counter()
        c.fit(train)
        fit_s = time.perf_counter() - started
        started = time.perf_counter()
        scores = np.asarray(c.score(test), dtype=float)
        score_s = time.perf_counter() - started
        metrics = {
            "name": c.name, "family": c.family, **ranking_from_scores(y, scores, keep_budgets),
            "train_seconds": round(fit_s, 2), "ms_per_1000": round(1000 * score_s / max(1, len(test)) * 1000, 1),
            "size_kb": round(c.size_bytes() / 1024, 1), "note": c.note,
        }
        graded.append(Graded(c, scores, metrics))
        if progress:
            print(f"{c.name:45} AP {metrics['avg_precision']:.3f}  trained in {fit_s:6.1f}s")
    return graded


def results_table(graded: Sequence[Graded]) -> pd.DataFrame:
    return pd.DataFrame([g.metrics for g in graded]).sort_values("avg_precision", ascending=False).reset_index(drop=True)


def keep_by_app(test: pd.DataFrame, graded: Sequence[Graded], *, keep_budget: float = 0.3, top: int = 10) -> pd.DataFrame:
    """For the busiest apps: how much of each would be kept if every model may keep only the top ``keep_budget`` of
    the test period, next to how much of it is important by the labels."""
    apps = test["app"].fillna("(unknown)")
    busiest = apps.value_counts().head(top).index
    table = pd.DataFrame({"contexts": apps.value_counts()[busiest], "important": test.groupby(apps)["label"].mean()[busiest]})
    for g in graded:
        cutoff = np.quantile(g.scores, 1 - keep_budget)
        kept = pd.Series(g.scores >= cutoff, index=test.index)
        table[g.contender.name] = kept.groupby(apps).mean()[busiest]
    return table


def by_day(test: pd.DataFrame, graded: Sequence[Graded]) -> pd.DataFrame:
    """Average precision per test day and contender (days with both classes only)."""
    from sklearn.metrics import average_precision_score

    days = pd.to_datetime(test["ts"]).dt.tz_convert(None).dt.date
    y = test["label"].astype(int)
    rows = {}
    for day in sorted(days.unique()):
        mask = (days == day).to_numpy()
        if y[mask].nunique() == 2:
            rows[day] = {g.contender.name: average_precision_score(y[mask], g.scores[mask]) for g in graded}
    return pd.DataFrame(rows).T


def rolling_grade(df: pd.DataFrame, make: Callable[[], Sequence[Contender]], *, n_folds: int = 4, test_fraction: float = 0.5,
                  embargo: timedelta = DEFAULT_LABEL_HORIZON, min_train: int = 500) -> pd.DataFrame:
    """Expanding-window evaluation: the newest ``test_fraction`` of labelled rows is cut into ``n_folds`` consecutive
    periods; each is graded with everything older (minus the embargo) as training. One row per (fold, contender)."""
    labelled = df[df["label"].notna()].sort_values("ts").reset_index(drop=True)
    first = len(labelled) - int(len(labelled) * test_fraction)
    bounds = np.linspace(first, len(labelled), n_folds + 1).astype(int)
    rows = []
    for fold in range(n_folds):
        test = labelled.iloc[bounds[fold]:bounds[fold + 1]]
        if test.empty:
            continue
        train = labelled[labelled["ts"] + embargo <= test["ts"].iloc[0]]
        if len(train) < min_train or train["label"].nunique() < 2 or test["label"].nunique() < 2:
            print(f"fold {fold}: skipped ({len(train)} training rows)")
            continue
        print(f"fold {fold}: train {len(train)}, test {len(test)} ({test['ts'].iloc[0]:%d %b} to {test['ts'].iloc[-1]:%d %b})")
        for g in grade(make(), train, test, progress=False):
            rows.append({"fold": fold, "test_from": test["ts"].iloc[0], **g.metrics})
    return pd.DataFrame(rows)


def summarize_rolling(results: pd.DataFrame, metrics: Sequence[str] = ("avg_precision", "roc_auc", "recall_at_keep_30")) -> pd.DataFrame:
    """Mean and spread per contender across folds, and how many folds each won on average precision."""
    summary = results.groupby("name", sort=False)[list(metrics)].agg(["mean", "std"]).round(3)
    wins = results.loc[results.groupby("fold")["avg_precision"].idxmax(), "name"].value_counts()
    summary[("avg_precision", "folds_won")] = [int(wins.get(n, 0)) for n in summary.index]
    return summary.sort_values(("avg_precision", "mean"), ascending=False)


def markdown_table(df: pd.DataFrame, *, index: bool = False) -> str:
    """``df`` as a Markdown table for the report (floats to 3 places)."""
    frame = df.reset_index() if index else df
    frame = frame.copy()
    frame.columns = [" ".join(map(str, c)).strip() if isinstance(c, tuple) else str(c) for c in frame.columns]
    cell = lambda v: f"{v:.3f}" if isinstance(v, float) else str(v)
    lines = ["| " + " | ".join(frame.columns) + " |", "|" + "---|" * len(frame.columns)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join(lines)
