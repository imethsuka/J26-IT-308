"""LightGBM importance model (Model 1).

Same interface as the rule-based baseline (``predict_proba(features) -> float``), plus training and
versioned, append-only persistence:

- ``train(labelled_dataframe)`` vectorizes each row (see :mod:`models.vectorizer`) and fits a
  LightGBM binary classifier; feature importances are captured for error analysis.
- ``save(version_id)`` writes ``models/versions/<version_id>/`` and **refuses to overwrite** an
  existing version, so history is preserved for rollback.
- ``load(version_id)`` restores a saved version.

Only the classifier is here; deciding *which* version is active belongs to
``storage.model_versions``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .base import Features, ImportanceScorer
from .vectorizer import FEATURE_COLUMNS, vectorize, vectorize_many

DEFAULT_VERSIONS_DIR = Path(__file__).resolve().parent / "versions"

DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 15,
    "learning_rate": 0.05,
    "min_data_in_leaf": 5,
    "feature_pre_filter": False,
    "verbosity": -1,
}
DEFAULT_NUM_BOOST_ROUND = 100


class ImportanceModel(ImportanceScorer):
    name = "lightgbm-untrained"

    def __init__(
        self,
        *,
        params: Mapping[str, Any] | None = None,
        num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
        versions_dir: Path | str = DEFAULT_VERSIONS_DIR,
        extra_feature_columns: Sequence[str] = (),
    ) -> None:
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        # Experimental features appended after FEATURE_COLUMNS (e.g. MobileCLIP category scores).
        # The live worker does not compute them, so a model using them is for offline experiments.
        self.extra_feature_columns: tuple[str, ...] = tuple(extra_feature_columns)
        self.num_boost_round = num_boost_round
        self.versions_dir = Path(versions_dir)
        self._booster = None
        self._feature_columns: tuple[str, ...] = FEATURE_COLUMNS
        self._version_id: str | None = None
        self._metadata: dict[str, Any] = {}

    @property
    def is_trained(self) -> bool:
        return self._booster is not None

    # ------------------------------------------------------------------ training

    def train(self, labelled_dataframe: pd.DataFrame, *, label_column: str = "label") -> dict[str, Any]:
        """
        Fit on a labelled DataFrame. Each row is a feature dict (either a ``features`` column of
        dicts, or the feature keys as columns) plus ``label_column`` (0/1). Returns a training
        summary including feature importances.
        """
        import lightgbm as lgb

        if label_column not in labelled_dataframe.columns:
            raise ValueError(f"label column {label_column!r} not found")

        y = labelled_dataframe[label_column].to_numpy()
        classes = set(np.unique(y).tolist())
        if not classes <= {0, 1}:
            raise ValueError(f"labels must be 0/1, got {sorted(classes)}")
        if len(classes) < 2:
            raise ValueError("training needs both classes (0 and 1) present")

        X = self._matrix_from_dataframe(labelled_dataframe, label_column)

        columns = FEATURE_COLUMNS + self.extra_feature_columns
        train_set = lgb.Dataset(X, label=y, feature_name=list(columns), free_raw_data=False)
        self._booster = lgb.train(self.params, train_set, num_boost_round=self.num_boost_round)
        self._feature_columns = columns
        self.name = "lightgbm-trained"

        summary = {
            "n_train": int(len(y)),
            "class_counts": {int(c): int((y == c).sum()) for c in sorted(classes)},
            "num_boost_round": self.num_boost_round,
            "feature_importances_gain": self.feature_importances("gain"),
            "feature_importances_split": self.feature_importances("split"),
        }
        self._metadata = {k: v for k, v in summary.items()}
        return summary

    # ------------------------------------------------------------------ inference

    def predict_proba(self, features: Features) -> float:
        self._require_trained()
        row = vectorize(features, self.extra_feature_columns).reshape(1, -1)
        return float(self._booster.predict(row)[0])

    def predict_proba_batch(self, feature_dicts: Sequence[Features]) -> np.ndarray:
        self._require_trained()
        matrix = vectorize_many(feature_dicts, self.extra_feature_columns)
        return self._booster.predict(matrix).astype(np.float64)

    # ------------------------------------------------------------------ importances

    def feature_importances(self, importance_type: str = "gain") -> dict[str, float]:
        """Feature name -> importance, highest first. ``importance_type`` is 'gain' or 'split'."""
        self._require_trained()
        names = self._booster.feature_name()
        values = self._booster.feature_importance(importance_type=importance_type)
        pairs = sorted(zip(names, (float(v) for v in values)), key=lambda kv: kv[1], reverse=True)
        return dict(pairs)

    def format_importances(self, importance_type: str = "gain", top: int | None = None) -> str:
        items = list(self.feature_importances(importance_type).items())
        if top is not None:
            items = items[:top]
        total = sum(v for _, v in items) or 1.0
        width = max((len(k) for k, _ in items), default=0)
        lines = [f"Feature importances ({importance_type}):"]
        for name, value in items:
            lines.append(f"  {name:<{width}}  {value:12.4f}  {100 * value / total:5.1f}%")
        return "\n".join(lines)

    # ------------------------------------------------------------------ persistence (append-only)

    def save(self, version_id: str) -> Path:
        """Write this model to ``versions_dir/<version_id>/``. Raises if the version already exists."""
        self._require_trained()
        target = self.versions_dir / version_id
        if target.exists():
            raise FileExistsError(f"version {version_id!r} already exists at {target}; versions are never overwritten")
        target.mkdir(parents=True)

        self._booster.save_model(str(target / "model.txt"))
        metadata = {
            "version_id": version_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "feature_columns": list(self._feature_columns),
            "params": self.params,
            "num_boost_round": self.num_boost_round,
            **self._metadata,
        }
        (target / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._version_id = version_id
        self.name = f"lightgbm-{version_id}"
        return target

    @classmethod
    def load(cls, version_id: str, *, versions_dir: Path | str = DEFAULT_VERSIONS_DIR) -> "ImportanceModel":
        import lightgbm as lgb

        source = Path(versions_dir) / version_id
        if not source.is_dir():
            raise FileNotFoundError(f"version {version_id!r} not found at {source}")

        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        stored_columns = tuple(metadata.get("feature_columns", FEATURE_COLUMNS))
        extra = stored_columns[len(FEATURE_COLUMNS):]
        if stored_columns[:len(FEATURE_COLUMNS)] != FEATURE_COLUMNS:
            raise ValueError(
                f"version {version_id!r} was trained with a different feature layout; "
                "the code's FEATURE_COLUMNS changed, so this version must be retrained"
            )

        model = cls(params=metadata.get("params"), num_boost_round=metadata.get("num_boost_round", DEFAULT_NUM_BOOST_ROUND), versions_dir=versions_dir, extra_feature_columns=extra)
        model._booster = lgb.Booster(model_file=str(source / "model.txt"))
        model._feature_columns = stored_columns
        model._version_id = version_id
        model._metadata = {k: metadata[k] for k in ("n_train", "class_counts") if k in metadata}
        model.name = f"lightgbm-{version_id}"
        return model

    @staticmethod
    def list_versions(versions_dir: Path | str = DEFAULT_VERSIONS_DIR) -> list[str]:
        base = Path(versions_dir)
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if (p / "model.txt").exists())

    # ------------------------------------------------------------------ internals

    def _matrix_from_dataframe(self, df: pd.DataFrame, label_column: str) -> np.ndarray:
        if "features" in df.columns:
            return vectorize_many(df["features"].tolist(), self.extra_feature_columns)
        records = df.drop(columns=[label_column]).to_dict(orient="records")
        return vectorize_many(records, self.extra_feature_columns)

    def _require_trained(self) -> None:
        if self._booster is None:
            raise RuntimeError("model is not trained or loaded; call train() or load() first")
