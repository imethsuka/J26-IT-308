"""Every importance model the system can run, by ``algorithm`` (the ``model_versions.algorithm`` column): how to train,
save and load each, so any of them can be the champion and all the others run in shadow mode.

A model's name in logs and in ``model_decisions.model`` is ``<algorithm>-<version>``; the rule-based baseline is
``rules-1.0``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from models.base import Features, ImportanceScorer

RULES = "rules"
RULES_NAME = "rules-1.0"
LIGHTGBM = "lightgbm"


@dataclass(frozen=True)
class Algorithm:
    key: str
    display: str
    family: str  # baseline / conventional ML / transfer learning / continual learning


ALGORITHMS: dict[str, Algorithm] = {a.key: a for a in (
    Algorithm(RULES, "Rules", "baseline"),
    Algorithm(LIGHTGBM, "LightGBM (Model 1)", "conventional ML"),
    Algorithm("logreg", "Logistic regression", "conventional ML"),
    Algorithm("random_forest", "Random forest", "conventional ML"),
    Algorithm("mlp", "Neural network (MLP)", "conventional ML"),
    Algorithm("mobileclip_logreg", "MobileCLIP only + logistic regression", "transfer learning"),
    Algorithm("features_mobileclip_logreg", "Features + MobileCLIP + logistic regression", "transfer learning"),
    Algorithm("river_arf", "River adaptive random forest", "continual learning"),
)}
# Trained by the learning cycle next to LightGBM, every cycle.
CHALLENGERS = ("logreg", "random_forest", "mlp", "mobileclip_logreg", "features_mobileclip_logreg", "river_arf")


def algorithm_of(stored: str | None) -> str:
    """``model_versions.algorithm``, with old rows (NULL) read as LightGBM."""
    return stored or LIGHTGBM


def model_name(algorithm: str, version: str) -> str:
    return f"{algorithm}-{version}"


def new_scorer(algorithm: str) -> ImportanceScorer:
    """An untrained model of ``algorithm``."""
    if algorithm == LIGHTGBM:
        from models.lightgbm_model import ImportanceModel

        return ImportanceModel()
    if algorithm == "river_arf":
        from models.challengers import RiverScorer

        return RiverScorer()
    from models.challengers import SklearnScorer

    return SklearnScorer(algorithm)


def load_scorer(algorithm: str | None, version: str, versions_dir: Path | str) -> ImportanceScorer:
    """A saved version. Raises FileNotFoundError / ValueError like ``ImportanceModel.load``."""
    algorithm = algorithm_of(algorithm)
    if algorithm == LIGHTGBM:
        from models.lightgbm_model import ImportanceModel

        model = ImportanceModel.load(version, versions_dir=versions_dir)
    elif algorithm == "river_arf":
        from models.challengers import RiverScorer

        model = RiverScorer.load(version, versions_dir)
    elif algorithm in ALGORITHMS and algorithm != RULES:
        from models.challengers import SklearnScorer

        model = SklearnScorer.load(version, versions_dir)
    else:
        raise ValueError(f"unknown algorithm {algorithm!r}")
    model.name = model_name(algorithm, version)
    return model


def train_scorer(algorithm: str, df: Any) -> tuple[ImportanceScorer, dict[str, Any]]:
    """Train a new model of ``algorithm`` on a frame with ``features``, ``label`` (and ``embedding``, ``ts``)."""
    import pandas as pd

    scorer = new_scorer(algorithm)
    if algorithm == LIGHTGBM:
        summary = scorer.train(pd.DataFrame({"features": list(df["features"]), "label": df["label"].astype(int).to_numpy()}))
    else:
        summary = scorer.train(df)
    return scorer, summary


def save_scorer(scorer: ImportanceScorer, algorithm: str, version: str, versions_dir: Path | str) -> Path:
    if algorithm == LIGHTGBM:
        scorer.versions_dir = Path(versions_dir)
        path = scorer.save(version)
    else:
        path = scorer.save(version, versions_dir)
    scorer.name = model_name(algorithm, version)
    return path


def uses_embedding(scorer: ImportanceScorer) -> bool:
    return bool(getattr(scorer, "uses_embedding", False))


def score(scorer: ImportanceScorer, features: Features, embedding: Sequence[float] | None = None) -> float:
    """One context's score, giving the embedding to the models that use it."""
    if uses_embedding(scorer):
        return float(scorer.predict_proba(features, embedding))
    return float(scorer.predict_proba(features))


def score_many(scorer: ImportanceScorer, features: Sequence[Features], embeddings: Sequence[Any] | None = None) -> list[float]:
    """Scores of many contexts, batched when the model can."""
    batch = getattr(scorer, "predict_proba_batch", None)
    if batch is not None:
        if uses_embedding(scorer):
            return [float(s) for s in batch(features, embeddings if embeddings is not None else [None] * len(features))]
        return [float(s) for s in batch(features)]
    return [score(scorer, f, e) for f, e in zip(features, embeddings if embeddings is not None else [None] * len(features))]
