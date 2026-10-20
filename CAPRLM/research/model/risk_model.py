"""The risk prediction model: (candidate span, context) -> risk tier.

Two simple candidates, both scikit-learn: multinomial logistic regression and a random
forest. `select_model` picks the configuration (regularisation, class weights, and which
family) and a *none-threshold*, all from out-of-fold predictions on the TRAINING data only —
the test set is never used for tuning. The selection rules and the recall-weighted objective
(F2) are defaults, not research decisions (BUILD_LOG.md).

None-threshold: with a threshold tau, a span is predicted "none" only if P(none) >= tau;
otherwise it gets its most likely non-none tier. A high tau therefore protects anything the
model is not confident is harmless — the privacy-first direction. tau=None is plain argmax.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline

from shared.types import PrivacyContext

from research.detection.candidate import Candidate
from .features import ALL_GROUPS, featurize

TIERS = ("none", "low", "medium", "high")
SELECTION_MARGIN = 0.02    # RandomForest must beat logreg's CV macro-F1 by this much
F_BETA = 2.0               # threshold objective: recall counts 2x as much as precision
LOGREG_C = (0.3, 1.0, 3.0, 10.0, 30.0)
CLASS_WEIGHTS = (None, "balanced")

Pair = tuple[Candidate, PrivacyContext]


@dataclass(frozen=True)
class ModelConfig:
    kind: str = "logreg"                 # "logreg" | "randomforest"
    C: float = 5.0                       # logreg inverse regularisation strength
    class_weight: str | None = None      # None | "balanced"
    none_threshold: float | None = None  # None = plain argmax
    features: tuple[str, ...] = ALL_GROUPS   # which feature groups the model may see (ablations)

    def label(self) -> str:
        if self.kind == "logreg":
            return f"logreg C={self.C:g} cw={self.class_weight or 'none'}"
        return f"randomforest cw={self.class_weight or 'none'}"


def _make_classifier(cfg: ModelConfig):
    if cfg.kind == "logreg":
        return LogisticRegression(C=cfg.C, class_weight=cfg.class_weight, max_iter=3000)
    if cfg.kind == "randomforest":
        cw = "balanced_subsample" if cfg.class_weight == "balanced" else None
        return RandomForestClassifier(n_estimators=200, class_weight=cw, random_state=0, n_jobs=1)
    raise ValueError(f"unknown model kind: {cfg.kind}")


def decide(probs: np.ndarray, classes: list[str], tau: float | None) -> str:
    """One span's tier from its class probabilities and the none-threshold."""
    if tau is not None and "none" in classes:
        i_none = classes.index("none")
        if probs[i_none] >= tau:
            return "none"
        masked = probs.copy()
        masked[i_none] = -1.0
        return classes[int(np.argmax(masked))]
    return classes[int(np.argmax(probs))]


class RiskModel:
    def __init__(self, config: ModelConfig | str = "logreg"):
        self.config = ModelConfig(kind=config) if isinstance(config, str) else config
        self.pipeline = make_pipeline(DictVectorizer(), _make_classifier(self.config))
        self.fitted = False
        self.trained_on: str | None = None   # dataset name, surfaced by the API

    @property
    def kind(self) -> str:
        return self.config.kind

    @property
    def version(self) -> str:
        return f"caprlm/{self.config.kind}-v0.2"

    def fit(self, pairs: list[Pair], tiers: list[str], trained_on: str | None = None) -> "RiskModel":
        self.pipeline.fit([featurize(c, x, self.config.features) for c, x in pairs], tiers)
        self.fitted = True
        self.trained_on = trained_on
        return self

    def predict(self, pairs: list[Pair]) -> list[str]:
        if not self.fitted:
            raise RuntimeError("RiskModel is not fitted")
        if not pairs:
            return []
        X = [featurize(c, x, self.config.features) for c, x in pairs]
        if self.config.none_threshold is None:
            return [str(t) for t in self.pipeline.predict(X)]
        proba = self.pipeline.predict_proba(X)
        classes = [str(c) for c in self.pipeline.classes_]
        return [decide(p, classes, self.config.none_threshold) for p in proba]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> "RiskModel":
        model = joblib.load(path)
        if not isinstance(model, RiskModel) or not model.fitted:
            raise ValueError(f"{path} is not a fitted RiskModel")
        return model


def _oof_proba(cfg: ModelConfig, X: list[dict], y: list[str], folds: int = 5):
    pipe = make_pipeline(DictVectorizer(), _make_classifier(cfg))
    proba = cross_val_predict(pipe, X, y, cv=StratifiedKFold(folds, shuffle=True, random_state=0),
                              method="predict_proba")
    return proba, sorted(set(y))


def _macro_f1(proba: np.ndarray, classes: list[str], y: list[str]) -> float:
    pred = [classes[int(i)] for i in proba.argmax(axis=1)]
    return float(f1_score(y, pred, average="macro"))


def _f_beta(y: list[str], pred: list[str], beta: float = F_BETA) -> dict:
    """Protect-or-not quality: positive = true tier is not "none"."""
    tp = sum(t != "none" and p != "none" for t, p in zip(y, pred))
    fp = sum(t == "none" and p != "none" for t, p in zip(y, pred))
    fn = sum(t != "none" and p == "none" for t, p in zip(y, pred))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    b2 = beta * beta
    f = (1 + b2) * prec * rec / (b2 * prec + rec) if prec + rec else 0.0
    return {"precision": prec, "recall": rec, f"f{beta:g}": f}


def tune_threshold(proba: np.ndarray, classes: list[str], y: list[str]) -> tuple[float | None, dict]:
    """Pick tau maximising F2 of the protect/not-protect decision on out-of-fold probabilities.
    Ties go to the higher tau (more protective)."""
    key = f"f{F_BETA:g}"
    argmax = _f_beta(y, [decide(p, classes, None) for p in proba])
    best_tau, best = None, argmax
    for tau in np.round(np.arange(0.05, 1.0, 0.01), 2):
        res = _f_beta(y, [decide(p, classes, float(tau)) for p in proba])
        if res[key] >= best[key]:
            best_tau, best = float(tau), res
    return best_tau, {"objective": f"F{F_BETA:g} on protect-vs-pass, out-of-fold",
                      "argmax": argmax, "tuned": best, "tau": best_tau}


def select_model(pairs: list[Pair], tiers: list[str], features: tuple[str, ...] = ALL_GROUPS,
                 families: tuple[str, ...] = ("logreg", "randomforest")) -> tuple[ModelConfig, dict]:
    """Choose config + none-threshold from out-of-fold predictions on the training data."""
    X = [featurize(c, x, features) for c, x in pairs]
    grid = [ModelConfig("logreg", C=c, class_weight=w) for c in LOGREG_C for w in CLASS_WEIGHTS]
    if "randomforest" in families:
        grid += [ModelConfig("randomforest", class_weight=w) for w in CLASS_WEIGHTS]

    scored: dict[ModelConfig, tuple[float, np.ndarray, list[str]]] = {}
    for cfg in grid:
        proba, classes = _oof_proba(cfg, X, tiers)
        scored[cfg] = (_macro_f1(proba, classes, tiers), proba, classes)

    best_lr = max((c for c in scored if c.kind == "logreg"), key=lambda c: scored[c][0])
    chosen = best_lr
    if "randomforest" in families:
        best_rf = max((c for c in scored if c.kind == "randomforest"), key=lambda c: scored[c][0])
        if scored[best_rf][0] >= scored[best_lr][0] + SELECTION_MARGIN:
            chosen = best_rf

    _, proba, classes = scored[chosen]
    tau, threshold_report = tune_threshold(proba, classes, tiers)
    final = ModelConfig(chosen.kind, chosen.C, chosen.class_weight, tau, features)
    return final, {
        "metric": "cv_macro_f1_on_risk_tier",
        "folds": 5,
        "scores": {c.label(): round(scored[c][0], 4) for c in grid},
        "rule": f"best logreg unless best randomforest beats it by >= {SELECTION_MARGIN}",
        "chosen": final.label(),
        "none_threshold": threshold_report,
    }
