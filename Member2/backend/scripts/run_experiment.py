"""
CAPRLM reproducible experiment script — Phase 4.

Loads the existing trained model and annotation CSV (read-only), runs
baseline_eval, performs a threshold sensitivity analysis, and writes
results to experiment_results.json (append-only).

SAFETY:
  - Never retrains or modifies the model.
  - Never modifies any CSV or annotation file.
  - Only appends to experiment_results.json; never overwrites it.

Usage (from Member2/ directory):
    .venv\\Scripts\\python -m backend.scripts.run_experiment

Output:
    backend/data/experiment_results.json  (append-only log of runs)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import train_test_split

from backend.services.train_model import (
    DATASET_PATH,
    MODEL_PATH,
    RANDOM_STATE,
    TEST_SIZE,
    build_features,
    load_annotations,
)
from backend.services.rule_baseline import predict_rule_based
from backend.services.baseline_eval import (
    evaluate_baseline_comparison,
    _csv_row_to_features,
)

# Output file — append-only log
RESULTS_PATH = DATASET_PATH.parent / "experiment_results.json"

# Threshold sensitivity range
THRESHOLD_SENSITIVITY_MIN = 0.50
THRESHOLD_SENSITIVITY_MAX = 0.95
THRESHOLD_SENSITIVITY_STEPS = 10


# ---------------------------------------------------------------------------
# Threshold sensitivity analysis
# ---------------------------------------------------------------------------

def _apply_threshold_to_probas(
    proba_matrix: np.ndarray,
    classes: list[str],
    medium_threshold: float,
    high_threshold: float,
) -> list[str]:
    """
    Apply a custom threshold pair to a probability matrix and return predictions.

    Replicates the logic of risk_prediction._apply_thresholds using
    raw model probabilities without the personalisation layer.

    Parameters
    ----------
    proba_matrix:
        Array of shape (n_samples, n_classes) from model.predict_proba().
    classes:
        Class labels corresponding to proba_matrix columns.
    medium_threshold:
        Minimum P(MEDIUM or above) to predict MEDIUM.
    high_threshold:
        Minimum P(HIGH or above) to predict HIGH.
    """
    class_idx = {c: i for i, c in enumerate(classes)}

    def _p(row: np.ndarray, *labels: str) -> float:
        return float(sum(row[class_idx[l]] for l in labels if l in class_idx))

    preds = []
    for row in proba_matrix:
        p_high_plus = _p(row, "HIGH", "CRITICAL")
        p_medium_plus = _p(row, "MEDIUM", "HIGH", "CRITICAL")
        if p_high_plus >= high_threshold:
            preds.append("HIGH")
        elif p_medium_plus >= medium_threshold:
            preds.append("MEDIUM")
        else:
            preds.append("LOW")
    return preds


def run_threshold_sensitivity(
    model,
    X_test: pd.DataFrame,
    y_test: np.ndarray,
    classes: list[str],
    all_classes: list[str],
) -> list[dict[str, Any]]:
    """
    Vary the MEDIUM threshold from THRESHOLD_SENSITIVITY_MIN to
    THRESHOLD_SENSITIVITY_MAX and record metrics at each step.

    HIGH threshold is set to MEDIUM threshold * 0.6 (heuristic ratio
    matching the default threshold tables in risk_prediction.py).

    Returns a list of result dicts, one per threshold value.
    """
    proba_matrix = model.predict_proba(X_test)
    thresholds = np.linspace(
        THRESHOLD_SENSITIVITY_MIN,
        THRESHOLD_SENSITIVITY_MAX,
        THRESHOLD_SENSITIVITY_STEPS,
    )
    results = []
    for med_thresh in thresholds:
        high_thresh = round(med_thresh * 0.6, 4)
        preds = _apply_threshold_to_probas(proba_matrix, classes, med_thresh, high_thresh)
        macro_f1 = float(f1_score(y_test, preds, average="macro", zero_division=0))
        acc = float(accuracy_score(y_test, preds))
        cm = confusion_matrix(y_test, preds, labels=all_classes).tolist()
        results.append({
            "medium_threshold": round(float(med_thresh), 4),
            "high_threshold": round(float(high_thresh), 4),
            "macro_f1": round(macro_f1, 4),
            "accuracy": round(acc, 4),
            "predictions": preds,
            "confusion_matrix": cm,
            "confusion_matrix_labels": all_classes,
        })
    return results


# ---------------------------------------------------------------------------
# Confusion matrix formatter
# ---------------------------------------------------------------------------

def _format_confusion_matrix(cm: list[list[int]], labels: list[str]) -> str:
    """Return a compact text representation of a confusion matrix."""
    header = f"  {'':14}" + "".join(f"{l:>10}" for l in labels)
    lines = [header]
    for i, row in enumerate(cm):
        lines.append(f"  {labels[i]:<14}" + "".join(f"{v:>10}" for v in row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main experiment function
# ---------------------------------------------------------------------------

def run_experiment() -> dict[str, Any]:
    """
    Execute the full experiment suite and return the result dict.

    Steps:
      1. Load existing annotation CSV (read-only).
      2. Load existing trained model bundle (read-only).
      3. Reproduce the deterministic train/test split.
      4. Run baseline_eval comparison (rule + ML + majority).
      5. Run threshold sensitivity analysis on the test fold.
      6. Build confusion matrix for the default threshold.
      7. Assemble and return results dict.
    """
    print("=" * 72)
    print("CAPRLM Reproducible Experiment")
    print(f"  Dataset:    {DATASET_PATH}")
    print(f"  Model:      {MODEL_PATH}")
    print(f"  RANDOM_STATE = {RANDOM_STATE}, TEST_SIZE = {TEST_SIZE}")
    print("=" * 72)

    # --- Step 1: Load dataset (read-only) ---
    df = load_annotations()
    print(f"\n[1] Dataset: {len(df)} rows")
    label_dist = df["privacy_risk_label"].value_counts().to_dict()
    print(f"    Label distribution: {label_dist}")

    # --- Step 2: Load model bundle (read-only) ---
    if not MODEL_PATH.exists():
        print(f"\n[!] Model not found at {MODEL_PATH}. Cannot run experiment.")
        return {"error": "Model file not found", "model_path": str(MODEL_PATH)}

    bundle = joblib.load(MODEL_PATH)
    model = bundle["model"]
    classes: list[str] = bundle["classes"]
    print(f"\n[2] Model loaded: version={bundle.get('model_version')}, classes={classes}")

    # --- Step 3: Reproduce deterministic split ---
    y = df["privacy_risk_label"].values
    df_train, df_test, y_train, y_test = train_test_split(
        df, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )
    # Use the trained encoder from the bundle (not refitted — prevents leakage)
    X_train, _ = build_features(df_train, context_encoder=bundle["context_encoder"])
    X_test, _ = build_features(df_test, context_encoder=bundle["context_encoder"])
    print(f"\n[3] Split: train={len(df_train)}, test={len(df_test)}")

    all_classes = sorted(set(y))

    # --- Step 4: Run baseline_eval ---
    print("\n[4] Running baseline evaluation …")
    baseline_result = evaluate_baseline_comparison()
    print(f"    Rule baseline:     accuracy={baseline_result['rule_baseline']['accuracy']:.4f}  "
          f"macro_f1={baseline_result['rule_baseline']['macro_f1']:.4f}")
    if baseline_result["ml_model"]:
        print(f"    ML model:          accuracy={baseline_result['ml_model']['accuracy']:.4f}  "
              f"macro_f1={baseline_result['ml_model']['macro_f1']:.4f}")
    if baseline_result.get("majority_baseline"):
        print(f"    Majority baseline: accuracy={baseline_result['majority_baseline']['accuracy']:.4f}  "
              f"macro_f1={baseline_result['majority_baseline']['macro_f1']:.4f}  "
              f"class='{baseline_result.get('majority_class')}'")

    # --- Step 5: Threshold sensitivity analysis ---
    print(f"\n[5] Threshold sensitivity analysis "
          f"(MEDIUM threshold {THRESHOLD_SENSITIVITY_MIN:.2f}–{THRESHOLD_SENSITIVITY_MAX:.2f}, "
          f"{THRESHOLD_SENSITIVITY_STEPS} steps) …")
    sensitivity_results = run_threshold_sensitivity(model, X_test, y_test, classes, all_classes)
    print(f"    {'MEDIUM_thresh':>14} {'HIGH_thresh':>12} {'macro_F1':>10} {'accuracy':>10}")
    for r in sensitivity_results:
        print(f"    {r['medium_threshold']:>14.4f} {r['high_threshold']:>12.4f} "
              f"{r['macro_f1']:>10.4f} {r['accuracy']:>10.4f}")

    # --- Step 6: Default-threshold confusion matrix ---
    print("\n[6] Confusion matrix at default threshold (model.predict()) …")
    y_pred_default = list(model.predict(X_test))
    cm_default = confusion_matrix(y_test, y_pred_default, labels=all_classes).tolist()
    print(_format_confusion_matrix(cm_default, all_classes))
    macro_f1_default = float(f1_score(y_test, y_pred_default, average="macro", zero_division=0))
    acc_default = float(accuracy_score(y_test, y_pred_default))
    print(f"    Default prediction macro_F1={macro_f1_default:.4f}, accuracy={acc_default:.4f}")

    # Per-class metrics
    report_dict = classification_report(
        y_test, y_pred_default, labels=all_classes,
        output_dict=True, zero_division=0
    )
    print("\n    Per-class metrics:")
    print(f"    {'Class':<14} {'Precision':>10} {'Recall':>8} {'F1':>8} {'Support':>8}")
    for cls in all_classes:
        row = report_dict.get(cls, {})
        print(f"    {cls:<14} {row.get('precision', 0.0):>10.4f} "
              f"{row.get('recall', 0.0):>8.4f} "
              f"{row.get('f1-score', 0.0):>8.4f} "
              f"{int(row.get('support', 0)):>8}")

    # --- Assemble result ---
    result: dict[str, Any] = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_path": str(DATASET_PATH),
        "model_path": str(MODEL_PATH),
        "model_version": bundle.get("model_version"),
        "random_state": RANDOM_STATE,
        "test_size": TEST_SIZE,
        "dataset_rows": int(len(df)),
        "train_rows": int(len(df_train)),
        "test_rows": int(len(df_test)),
        "label_distribution": label_dist,
        "all_classes": all_classes,
        "baseline_comparison": {
            "majority_baseline": baseline_result.get("majority_baseline"),
            "majority_class": baseline_result.get("majority_class"),
            "rule_baseline": baseline_result["rule_baseline"],
            "ml_model": baseline_result["ml_model"],
        },
        "default_threshold_results": {
            "macro_f1": round(macro_f1_default, 4),
            "accuracy": round(acc_default, 4),
            "confusion_matrix": cm_default,
            "confusion_matrix_labels": all_classes,
            "per_class_report": report_dict,
        },
        "threshold_sensitivity": [
            {
                "medium_threshold": r["medium_threshold"],
                "high_threshold": r["high_threshold"],
                "macro_f1": r["macro_f1"],
                "accuracy": r["accuracy"],
                "confusion_matrix": r["confusion_matrix"],
                "confusion_matrix_labels": r["confusion_matrix_labels"],
            }
            for r in sensitivity_results
        ],
        "limitations": baseline_result.get("limitations", []),
        "notes": [
            "Threshold sensitivity uses MEDIUM threshold varied from "
            f"{THRESHOLD_SENSITIVITY_MIN} to {THRESHOLD_SENSITIVITY_MAX}; "
            "HIGH threshold = MEDIUM * 0.6 (heuristic).",
            "Model was NOT retrained. All predictions use the loaded bundle.",
            "Test fold is the same deterministic split as train_model.py.",
            "HIGH class has 0 test examples; HIGH F1 cannot be evaluated.",
        ],
    }
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _append_result(result: dict[str, Any]) -> None:
    """Append result to RESULTS_PATH (append-only; never overwrites history)."""
    history: list = []
    if RESULTS_PATH.exists():
        try:
            with RESULTS_PATH.open(encoding="utf-8") as fh:
                history = json.load(fh)
        except (json.JSONDecodeError, ValueError):
            history = []
    history.append(result)
    RESULTS_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"\n[7] Results appended → {RESULTS_PATH}  (total runs: {len(history)})")


if __name__ == "__main__":
    result = run_experiment()
    _append_result(result)
    print("\nExperiment complete.")
