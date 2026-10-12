"""The challenger models: alternatives to Model 1 (LightGBM) that the worker runs in shadow mode on every screen and the
dashboard can promote to champion. ``models.registry`` names them and loads saved versions.

Each one trains on a frame with ``features`` (dict), ``embedding`` (512-d or None), ``label`` (0/1) and ``ts``, and
scores one context with ``predict_proba(features, embedding)``. The embedding is optional: models that use it treat a
missing one as all zeros with ``has_embedding`` 0, so they keep scoring when MobileCLIP is off.

Versions are append-only, like LightGBM's: ``<versions_dir>/<version>/model.joblib`` + ``metadata.json``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from models.base import Features, ImportanceScorer
from models.vectorizer import FEATURE_COLUMNS, vectorize, vectorize_many

EMBEDDING_DIM = 512
# Carries a context's embedding inside an in-memory feature dict, for code that only passes features (the deploy gate).
# Never stored: the raw embedding stays out of features_json.
EMBEDDING_KEY = "__embedding__"


def embedding_row(embedding: Sequence[float] | None) -> np.ndarray:
    """The 512 MobileCLIP numbers at unit length plus ``has_embedding``; zeros when there is none."""
    row = np.zeros(EMBEDDING_DIM + 1, dtype=np.float32)
    if embedding is not None and len(embedding) == EMBEDDING_DIM:
        vector = np.asarray(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm:
            row[:EMBEDDING_DIM] = vector / norm
        row[EMBEDDING_DIM] = 1.0
    return row


def embedding_rows(embeddings: Sequence[Any]) -> np.ndarray:
    return np.vstack([embedding_row(e) for e in embeddings]) if len(embeddings) else np.zeros((0, EMBEDDING_DIM + 1), dtype=np.float32)


def with_embedding(features: Features, embedding: Sequence[float] | None) -> dict[str, Any]:
    """A copy of ``features`` that carries ``embedding`` (see EMBEDDING_KEY)."""
    return {**features, EMBEDDING_KEY: embedding}


def _embedding_of(features: Features, embedding: Sequence[float] | None) -> Sequence[float] | None:
    return embedding if embedding is not None else features.get(EMBEDDING_KEY)


class _Saved(ImportanceScorer):
    """Shared persistence: one joblib file plus metadata, never overwritten."""

    algorithm = ""
    uses_embedding = False

    def __init__(self) -> None:
        self.version: str | None = None
        self.metadata: dict[str, Any] = {}

    def save(self, version_id: str, versions_dir: Path | str) -> Path:
        import joblib

        target = Path(versions_dir) / version_id
        if target.exists():
            raise FileExistsError(f"version {version_id!r} already exists at {target}; versions are never overwritten")
        target.mkdir(parents=True)
        joblib.dump(self._state(), target / "model.joblib", compress=3)
        metadata = {
            "version_id": version_id, "algorithm": self.algorithm, "feature_columns": list(FEATURE_COLUMNS),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **self.metadata,
        }
        (target / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
        self.version = version_id
        self.name = f"{self.algorithm}-{version_id}"
        return target

    @classmethod
    def load(cls, version_id: str, versions_dir: Path | str) -> "_Saved":
        import joblib

        source = Path(versions_dir) / version_id
        if not (source / "model.joblib").is_file():
            raise FileNotFoundError(f"version {version_id!r} not found at {source}")
        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        if tuple(metadata.get("feature_columns", ())) != FEATURE_COLUMNS:
            raise ValueError(f"version {version_id!r} was trained with a different feature layout; retrain it")
        scorer = cls._from_state(metadata["algorithm"], joblib.load(source / "model.joblib"))
        scorer.metadata = {k: v for k, v in metadata.items() if k not in ("version_id", "algorithm", "feature_columns", "created_at")}
        scorer.version = version_id
        scorer.name = f"{scorer.algorithm}-{version_id}"
        return scorer

    def _state(self) -> Any:
        raise NotImplementedError

    @classmethod
    def _from_state(cls, algorithm: str, state: Any) -> "_Saved":
        raise NotImplementedError


# --------------------------------------------------------------------------- scikit-learn models

# algorithm -> (inputs, estimator factory). Inputs: the Model 1 features, the MobileCLIP vector, or both.
def _estimators() -> dict[str, tuple[str, Any]]:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "logreg": ("features", lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))),
        "random_forest": ("features", lambda: RandomForestClassifier(
            n_estimators=200, min_samples_leaf=10, n_jobs=-1, random_state=0)),
        "mlp": ("features", lambda: make_pipeline(
            StandardScaler(), MLPClassifier(hidden_layer_sizes=(64, 32), early_stopping=True, random_state=0))),
        "mobileclip_logreg": ("embedding", lambda: LogisticRegression(max_iter=2000, C=0.5)),
        "features_mobileclip_logreg": ("both", lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=0.5))),
    }


SKLEARN_ALGORITHMS = ("logreg", "random_forest", "mlp", "mobileclip_logreg", "features_mobileclip_logreg")


class SklearnScorer(_Saved):
    """A scikit-learn classifier on the Model 1 features, the MobileCLIP vector, or both."""

    def __init__(self, algorithm: str) -> None:
        super().__init__()
        if algorithm not in SKLEARN_ALGORITHMS:
            raise ValueError(f"unknown algorithm {algorithm!r}")
        self.algorithm = algorithm
        self.inputs = _estimators()[algorithm][0]
        self.uses_embedding = self.inputs != "features"
        self.name = f"{algorithm}-untrained"
        self.estimator = None

    def _matrix(self, features: Sequence[Features], embeddings: Sequence[Any]) -> np.ndarray:
        if self.inputs == "features":
            return vectorize_many(features)
        if self.inputs == "embedding":
            return embedding_rows(embeddings)
        return np.hstack([vectorize_many(features), embedding_rows(embeddings)])

    def train(self, df: pd.DataFrame) -> dict[str, Any]:
        y = df["label"].astype(int).to_numpy()
        if len(set(y.tolist())) < 2:
            raise ValueError("training needs both classes (0 and 1) present")
        embeddings = list(df["embedding"]) if "embedding" in df else [None] * len(df)
        self.estimator = _estimators()[self.algorithm][1]()
        self.estimator.fit(self._matrix(list(df["features"]), embeddings), y)
        if hasattr(self.estimator, "n_jobs"):
            self.estimator.set_params(n_jobs=1)  # one row at a time live: threads cost more than they save
        self.name = f"{self.algorithm}-trained"
        self.metadata = {"n_train": int(len(y)), "class_counts": {int(c): int((y == c).sum()) for c in (0, 1)}}
        return self.metadata

    def predict_proba(self, features: Features, embedding: Sequence[float] | None = None) -> float:
        return float(self.predict_proba_batch([features], [_embedding_of(features, embedding)])[0])

    def predict_proba_batch(self, features: Sequence[Features], embeddings: Sequence[Any] | None = None) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("model is not trained")
        embeddings = list(embeddings) if embeddings is not None else [_embedding_of(f, None) for f in features]
        return self.estimator.predict_proba(self._matrix(list(features), embeddings))[:, 1].astype(np.float64)

    def _state(self) -> Any:
        return self.estimator

    @classmethod
    def _from_state(cls, algorithm: str, state: Any) -> "SklearnScorer":
        scorer = cls(algorithm)
        scorer.estimator = state
        return scorer


# --------------------------------------------------------------------------- River (continual learning)

def river_model() -> Any:
    """The River model behind ``river_arf`` (also used by the offline benchmark, so both run the same model)."""
    from river import forest

    # Smaller than River's default (10 unbounded trees): on the 24 Oct data AP 0.892 vs 0.843, 5 MB vs 24 MB a version.
    return forest.ARFClassifier(n_models=5, max_depth=8, grace_period=200, seed=0)


class RiverScorer(_Saved):
    """River's adaptive random forest: learns one context at a time and adapts to drift (ADWIN). A new version
    continues from the previous one with only the labels that arrived since (``learn``), rather than starting over."""

    algorithm = "river_arf"

    def __init__(self) -> None:
        super().__init__()
        self.name = "river_arf-untrained"
        self.model = None
        self.learned_until: datetime | None = None

    @staticmethod
    def _x(features: Features) -> dict[str, float]:
        return dict(zip(FEATURE_COLUMNS, map(float, vectorize(features))))

    def learn(self, df: pd.DataFrame) -> int:
        """Learn the labelled rows newer than everything learned so far, oldest first. Returns how many."""
        if self.model is None:
            self.model = river_model()
        rows = df[df["label"].notna()].sort_values("ts", kind="stable")
        if self.learned_until is not None:
            rows = rows[pd.to_datetime(rows["ts"], utc=True) > pd.Timestamp(self.learned_until)]
        for features, label in zip(rows["features"], rows["label"]):
            self.model.learn_one(self._x(features), int(label))
        if len(rows):
            self.learned_until = pd.Timestamp(rows["ts"].iloc[-1]).to_pydatetime()
        self.metadata = {**self.metadata, "learned_until": self.learned_until,
                         "n_learned": int(self.metadata.get("n_learned", 0)) + len(rows)}
        return len(rows)

    def train(self, df: pd.DataFrame) -> dict[str, Any]:
        self.learn(df)
        self.name = "river_arf-trained"
        return self.metadata

    def predict_proba(self, features: Features, embedding: Sequence[float] | None = None) -> float:
        if self.model is None:
            raise RuntimeError("model is not trained")
        return float(self.model.predict_proba_one(self._x(features)).get(1, 0.5))

    def predict_proba_batch(self, features: Sequence[Features], embeddings: Sequence[Any] | None = None) -> np.ndarray:
        return np.array([self.predict_proba(f) for f in features], dtype=np.float64)

    def _state(self) -> Any:
        return {"model": self.model, "learned_until": self.learned_until}

    @classmethod
    def _from_state(cls, algorithm: str, state: Any) -> "RiverScorer":
        scorer = cls()
        scorer.model, scorer.learned_until = state["model"], state["learned_until"]
        return scorer
