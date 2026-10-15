"""
Offline evaluation comparing the rule-based baseline against the ML model
on the held-out test fold of the CAPRLM annotation dataset.

Both classifiers are evaluated on the same test set produced by the
deterministic split used in train_model.py (RANDOM_STATE=42, TEST_SIZE=0.20,
stratify=y).  This prevents evaluation-time data leakage.

Documented limitations
----------------------
- n=50 is too small for statistically reliable evaluation.  A 10-row test
  set means each misclassification shifts macro F1 by ≈0.1.
- Class imbalance (LOW≈38, MEDIUM≈10, HIGH=2, CRITICAL=0) means micro F1
  is dominated by the majority class; macro F1 is preferred.
- Several MEDIUM and HIGH rows have all-zero entity flags; neither
  classifier can explain or correctly predict these rows.
- ML model metrics here use raw model.predict() with no personalisation
  layer, matching the training evaluation in train_model.py.
- Results are not statistically significant and must not be presented as
  definitive performance claims.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import train_test_split

from backend.services.rule_baseline import predict_rule_based
from backend.services.train_model import (
    DATASET_PATH,
    MODEL_PATH,
    RANDOM_STATE,
    TEST_SIZE,
    build_features,
    load_annotations,
)


# ---------------------------------------------------------------------------
# CSV → extracted_features bridge
# ---------------------------------------------------------------------------

def _safe_context(val: Any) -> str:
    """Return a clean string for application_context, handling NaN from pandas."""
    if val is None:
        return ""
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def _csv_row_to_features(row: pd.Series) -> dict[str, Any]:
    """
    Convert one annotation CSV row to the extracted_features dict format
    expected by predict_rule_based().

    CSV entity columns are binary flags (0 or 1).  The rule baseline reads
    *_count keys, so each flag is passed through directly (flag=1 means
    count=1 from the rule's perspective).
    """
    return {
        "email_count":         int(row.get("EMAIL", 0)),
        "phone_count":         int(row.get("PHONE", 0)),
        "url_count":           int(row.get("URL", 0)),
        "ip_count":            int(row.get("IP_ADDRESS", 0)),
        "date_count":          int(row.get("DATE", 0)),
        "windows_path_count":  int(row.get("WINDOWS_PATH", 0)),
        "sensitive_entity_count": int(row.get("sensitive_entity_present", 0)),
        "application_context": _safe_context(row.get("application_context")),
    }


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_baseline_comparison(
    dataset_path=DATASET_PATH,
    model_path=MODEL_PATH,
) -> dict[str, Any]:
    """
    Run the rule baseline and the ML model on the same held-out test fold
    and return a side-by-side comparison of their metrics.

    Parameters
    ----------
    dataset_path:
        Path to the annotation CSV.  Defaults to the protected DATASET_PATH.
        Passing an alternative path is for testing only.
    model_path:
        Path to the trained model joblib.  Defaults to MODEL_PATH.
        When no model file exists, ML metrics are omitted and a warning is
        included in the result.

    Returns
    -------
    dict with keys:
        dataset_rows, label_distribution, test_size,
        rule_baseline (metrics dict),
        ml_model (metrics dict or None),
        all_classes,
        limitations (list[str])
    """
    df = load_annotations() if dataset_path == DATASET_PATH else _load_csv(dataset_path)

    y = df["privacy_risk_label"].values

    # Identical split to train_model.py — test fold is completely held out
    df_train, df_test, y_train, y_test = train_test_split(
        df, y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=y,
    )

    label_dist: dict[str, int] = (
        pd.Series(y).value_counts().sort_index().to_dict()
    )

    # -----------------------------------------------------------------------
    # Rule baseline predictions on test fold
    # -----------------------------------------------------------------------
    rule_preds: list[str] = []
    for _, row in df_test.iterrows():
        feats = _csv_row_to_features(row)
        result = predict_rule_based(feats)
        rule_preds.append(result["risk_level"])

    # -----------------------------------------------------------------------
    # Majority-class baseline (always predicts most frequent training class)
    # If the rule baseline and ML model cannot beat this trivial classifier,
    # their added complexity is unjustified by these results alone.
    # -----------------------------------------------------------------------
    majority_preds, majority_class = _majority_baseline_predictions(y_train, y_test)

    all_classes = sorted(set(y) | set(rule_preds) | set(majority_preds))
    rule_metrics = _compute_metrics(y_test, rule_preds, all_classes)
    majority_metrics = _compute_metrics(y_test, majority_preds, all_classes)

    # -----------------------------------------------------------------------
    # ML model predictions on test fold (no personalisation)
    # -----------------------------------------------------------------------
    ml_metrics: dict[str, Any] | None = None
    ml_warning: str | None = None

    if not model_path.exists():
        ml_warning = (
            "ML model not found at expected path. "
            "Run `python -m backend.services.train_model` to train the model first."
        )
    else:
        try:
            import joblib

            bundle = joblib.load(model_path)
            model = bundle["model"]

            # Use the encoder stored in the bundle — it was fitted on the
            # original training fold and is the one the model was trained with.
            # Re-fitting a fresh encoder would silently produce wrong integer
            # mappings if the dataset version ever differs from training time.
            X_test, _ = build_features(df_test, context_encoder=bundle["context_encoder"])

            ml_preds: list[str] = list(model.predict(X_test))
            all_classes = sorted(set(y) | set(ml_preds) | set(rule_preds) | set(majority_preds))
            ml_metrics = _compute_metrics(y_test, ml_preds, all_classes)
        except Exception as exc:
            ml_warning = f"ML evaluation failed: {exc}"

    return {
        "dataset_rows": int(len(df)),
        "train_rows": int(len(df_train)),
        "test_rows": int(len(df_test)),
        "label_distribution": label_dist,
        "all_classes": all_classes,
        "majority_baseline": majority_metrics,
        "majority_class": majority_class,
        "rule_baseline": rule_metrics,
        "ml_model": ml_metrics,
        "ml_warning": ml_warning,
        "limitations": _LIMITATIONS,
    }


def _load_csv(path) -> pd.DataFrame:
    """Load and minimally validate an alternative annotation CSV for testing."""
    from pathlib import Path as _Path

    df = pd.read_csv(_Path(path))
    df = df.dropna(subset=["privacy_risk_label"]).copy()
    df["privacy_risk_label"] = (
        df["privacy_risk_label"].astype(str).str.strip().str.upper()
    )
    df["application_context"] = df["application_context"].fillna("Unknown") \
        if "application_context" in df.columns else "Unknown"
    return df


def _compute_metrics(y_true, y_pred, classes: list[str]) -> dict[str, Any]:
    """Compute classification metrics for one set of predictions."""
    macro_f1 = float(f1_score(y_true, y_pred, average="macro", zero_division=0))
    accuracy = float(accuracy_score(y_true, y_pred))
    report = classification_report(
        y_true, y_pred, labels=classes, output_dict=True, zero_division=0
    )
    cm = confusion_matrix(y_true, y_pred, labels=classes)
    return {
        "macro_f1": round(macro_f1, 4),
        "accuracy": round(accuracy, 4),
        "classification_report": report,
        "confusion_matrix": cm.tolist(),
        "confusion_matrix_labels": classes,
    }


def _majority_baseline_predictions(
    y_train: np.ndarray, y_test: np.ndarray
) -> tuple[list[str], str]:
    """
    Return predictions and majority class for a trivial classifier that always
    predicts the most frequent class in the training fold.

    This is the minimal non-trivial baseline: if neither the rule classifier nor
    the ML model beats the majority-class baseline, their added complexity is
    unjustified.
    """
    from collections import Counter
    majority_class: str = Counter(y_train).most_common(1)[0][0]
    return [majority_class] * len(y_test), majority_class


# ---------------------------------------------------------------------------
# Limitations (shared between the result dict and the module docstring)
# ---------------------------------------------------------------------------

_LIMITATIONS: list[str] = [
    "n=50 dataset is insufficient for statistically reliable evaluation; "
    "a 10-row test set means each misclassification shifts macro F1 by ≈0.1.",
    "Class imbalance (LOW≈76 %) means accuracy is dominated by the majority class; "
    "macro F1 is the preferred metric.",
    "Several MEDIUM and HIGH rows have all-zero entity flags; neither classifier "
    "can explain or predict these from the available features.",
    "The ML model here uses raw model.predict() with no personalisation layer; "
    "production behaviour (with preference-based threshold adjustment) may differ.",
    "No statistical significance testing is applied; results should not be "
    "interpreted as a definitive performance comparison.",
    "CRITICAL class has zero annotated examples; no classifier can learn or be "
    "evaluated on it.",
]


# ---------------------------------------------------------------------------
# CLI entry point (read-only; does not train)
# ---------------------------------------------------------------------------

EVAL_LOG_PATH = DATASET_PATH.parent / "baseline_eval_log.json"


def persist_eval_result(result: dict[str, Any]) -> None:
    """Append a baseline evaluation result to the append-only log file."""
    import time

    def _summary(metrics: dict | None, majority_class: str | None = None) -> dict | None:
        if metrics is None:
            return None
        d = {
            "macro_f1": metrics["macro_f1"],
            "accuracy": metrics["accuracy"],
        }
        if majority_class is not None:
            d["majority_class"] = majority_class
        return d

    entry = {
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_rows": result["dataset_rows"],
        "train_rows": result["train_rows"],
        "test_rows": result["test_rows"],
        "label_distribution": result["label_distribution"],
        "all_classes": result.get("all_classes"),
        "majority_baseline": _summary(
            result.get("majority_baseline"),
            result.get("majority_class"),
        ),
        "rule_baseline": _summary(result.get("rule_baseline")),
        "ml_model": _summary(result.get("ml_model")),
        "ml_warning": result.get("ml_warning"),
        "limitations": result.get("limitations"),
    }

    history: list = []
    if EVAL_LOG_PATH.exists():
        try:
            with EVAL_LOG_PATH.open(encoding="utf-8") as fh:
                history = json.load(fh)
        except (json.JSONDecodeError, ValueError):
            history = []

    history.append(entry)
    EVAL_LOG_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")


def _per_class_table(metrics: dict | None, label: str) -> str:
    """Format per-class precision/recall/F1 as a compact text table."""
    if metrics is None:
        return f"  {label}: not available\n"
    report = metrics.get("classification_report", {})
    classes = metrics.get("confusion_matrix_labels", [])
    lines = [f"  {label}  (accuracy={metrics['accuracy']:.4f}  macro_f1={metrics['macro_f1']:.4f})"]
    lines.append(f"  {'Class':<14} {'Precision':>10} {'Recall':>8} {'F1':>8} {'Support':>8}")
    for cls in classes:
        row = report.get(cls, {})
        lines.append(
            f"  {cls:<14} {row.get('precision', 0.0):>10.4f}"
            f" {row.get('recall', 0.0):>8.4f}"
            f" {row.get('f1-score', 0.0):>8.4f}"
            f" {int(row.get('support', 0)):>8}"
        )
    cm = metrics.get("confusion_matrix")
    if cm and classes:
        lines.append(f"  Confusion matrix (rows=actual, cols=predicted, labels={classes}):")
        for i, row in enumerate(cm):
            lines.append(f"    {classes[i]:<12}: {row}")
    return "\n".join(lines)


if __name__ == "__main__":
    result = evaluate_baseline_comparison()

    # Summary table (concise)
    def _brief(metrics: dict | None, name: str | None = None) -> dict | None:
        if metrics is None:
            return None
        d: dict = {"macro_f1": metrics["macro_f1"], "accuracy": metrics["accuracy"]}
        if name:
            d["majority_class"] = name
        return d

    output = {
        "dataset_rows": result["dataset_rows"],
        "train_rows": result["train_rows"],
        "test_rows": result["test_rows"],
        "label_distribution": result["label_distribution"],
        "majority_baseline": _brief(result.get("majority_baseline"), result.get("majority_class")),
        "rule_baseline": _brief(result["rule_baseline"]),
        "ml_model": _brief(result["ml_model"]),
        "ml_warning": result["ml_warning"],
        "limitations": result["limitations"],
    }
    print(json.dumps(output, indent=2))

    # Per-class detail block
    print("\n--- Per-class metrics ---")
    print(_per_class_table(result.get("majority_baseline"), "majority_baseline"))
    print(_per_class_table(result["rule_baseline"], "rule_baseline"))
    print(_per_class_table(result["ml_model"], "ml_model"))

    persist_eval_result(result)
    print(f"\nEvaluation result appended → {EVAL_LOG_PATH}", flush=True)
