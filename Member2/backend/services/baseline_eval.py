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

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
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

    all_classes = sorted(set(y) | set(rule_preds))
    rule_metrics = _compute_metrics(y_test, rule_preds, all_classes)

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
            all_classes = sorted(set(y) | set(ml_preds) | set(rule_preds))
            ml_metrics = _compute_metrics(y_test, ml_preds, all_classes)
        except Exception as exc:
            ml_warning = f"ML evaluation failed: {exc}"

    return {
        "dataset_rows": int(len(df)),
        "train_rows": int(len(df_train)),
        "test_rows": int(len(df_test)),
        "label_distribution": label_dist,
        "all_classes": all_classes,
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
    return {
        "macro_f1": round(macro_f1, 4),
        "accuracy": round(accuracy, 4),
        "classification_report": report,
    }


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

if __name__ == "__main__":
    import json

    result = evaluate_baseline_comparison()

    # Strip the full classification_report for terminal readability
    def _summary(metrics: dict | None) -> dict | None:
        if metrics is None:
            return None
        return {
            "macro_f1": metrics["macro_f1"],
            "accuracy": metrics["accuracy"],
        }

    print(json.dumps({
        "dataset_rows": result["dataset_rows"],
        "train_rows": result["train_rows"],
        "test_rows": result["test_rows"],
        "label_distribution": result["label_distribution"],
        "rule_baseline": _summary(result["rule_baseline"]),
        "ml_model": _summary(result["ml_model"]),
        "ml_warning": result["ml_warning"],
        "limitations": result["limitations"],
    }, indent=2))
