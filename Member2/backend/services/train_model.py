"""
CAPRLM shared-model training script.

Dataset:  CAPRLM_annotation_50.csv  (50 manually annotated rows)
Labels:   LOW=38, MEDIUM=10, HIGH=2, CRITICAL=0

Documented limitations
----------------------
- n=50 is insufficient for robust generalisation; reported metrics have wide
  confidence intervals and should not be treated as definitive.
- CRITICAL class is unrepresented; the classifier cannot learn or predict it.
- CSV entity columns are binary presence flags (0/1); runtime feature_extraction
  produces integer counts.  Integer counts are binarised (count > 0 → 1) at
  inference so the feature space matches the training data.
- Several MEDIUM and HIGH rows in the dataset have all-zero entity flags,
  meaning those labels cannot be explained by the entity features alone.
  This limits the separability achievable with the current feature set.
- Labels are manually annotated; inter-annotator agreement is unknown.
- Personalisation is implemented as decision-threshold adjustment, not
  per-user model retraining (insufficient per-user labelled data).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.preprocessing import LabelEncoder

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
DATASET_PATH = _HERE.parent / "data" / "CAPRLM_annotation_50.csv"
MODEL_PATH = _HERE.parent / "data" / "caprlm_model.joblib"
TRAINING_LOG_PATH = _HERE.parent / "data" / "training_log.json"

# ---------------------------------------------------------------------------
# Reproducibility constants
# ---------------------------------------------------------------------------
RANDOM_STATE: int = 42
TEST_SIZE: float = 0.20
MAX_CV_FOLDS: int = 5
MODEL_VERSION: str = "1.0.0"

# Feature columns that exist in both the CSV and the runtime feature dict
# (after binarisation of runtime counts).
ENTITY_FEATURES: list[str] = [
    "EMAIL",
    "PHONE",
    "URL",
    "IP_ADDRESS",
    "DATE",
    "WINDOWS_PATH",
    "sensitive_entity_present",
]

# Ordered risk levels (used for threshold adjustment logic)
RISK_LEVELS_ORDERED: list[str] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_annotations() -> pd.DataFrame:
    """Load and validate the CAPRLM annotation CSV from the repository."""
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Annotation dataset not found: {DATASET_PATH}\n"
            "Copy CAPRLM_annotation_50.csv into backend/data/ and retry."
        )

    df = pd.read_csv(DATASET_PATH)

    required_columns = [
        "screenshot_id",
        "application_context",
        *ENTITY_FEATURES,
        "privacy_risk_label",
    ]
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in dataset: {missing}")

    df = df.dropna(subset=["privacy_risk_label"]).copy()
    df["privacy_risk_label"] = (
        df["privacy_risk_label"].astype(str).str.strip().str.upper()
    )
    df["application_context"] = df["application_context"].fillna("Unknown")
    return df


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def build_features(
    df: pd.DataFrame,
    context_encoder: LabelEncoder | None = None,
) -> tuple[pd.DataFrame, LabelEncoder]:
    """
    Build the training feature matrix from an annotation dataframe.
    context_encoder is fitted when None (training) and reused otherwise.
    """
    X = df[ENTITY_FEATURES].copy().astype(int)

    if context_encoder is None:
        context_encoder = LabelEncoder()
        X["application_context_encoded"] = context_encoder.fit_transform(
            df["application_context"]
        )
    else:
        def _safe_encode(val: str) -> int:
            try:
                return int(context_encoder.transform([val])[0])
            except ValueError:
                return -1  # unseen category → treated as unknown

        X["application_context_encoded"] = df["application_context"].map(_safe_encode)

    return X, context_encoder


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train() -> dict:
    """
    Train the shared Random Forest classifier.

    Returns the model bundle dict and writes it to MODEL_PATH.
    Also appends an entry to TRAINING_LOG_PATH.
    """
    df = load_annotations()

    print(f"Loaded {len(df)} annotated rows.")
    label_dist = df["privacy_risk_label"].value_counts().to_dict()
    print(f"Label distribution: {label_dist}")
    print()

    y = df["privacy_risk_label"].values

    # ------------------------------------------------------------------
    # Split the raw dataframe BEFORE fitting the encoder so the encoder
    # never sees test-set application_context categories (leakage fix).
    # ------------------------------------------------------------------
    df_train, df_test, y_train, y_test = train_test_split(
        df, y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=y,
    )
    print(f"Train: {len(df_train)} rows  |  Test: {len(df_test)} rows")

    # Fit encoder on training fold only; transform test fold with it.
    X_train, context_encoder = build_features(df_train)
    X_test, _ = build_features(df_test, context_encoder=context_encoder)
    feature_names = list(X_train.columns)

    # ------------------------------------------------------------------
    # Stratified k-fold CV on the training fold only.
    # n_folds is capped by the smallest class count to avoid errors with
    # very small minority classes.
    # ------------------------------------------------------------------
    min_class_count = int(pd.Series(y_train).value_counts().min())
    n_folds = min(MAX_CV_FOLDS, min_class_count)
    cv_f1_scores: list[float] = []

    if n_folds < 2:
        print(
            f"WARNING: Smallest training class has {min_class_count} sample(s). "
            "Cross-validation skipped."
        )
    else:
        print(f"Running {n_folds}-fold stratified CV on training fold …")
        skf = StratifiedKFold(
            n_splits=n_folds, shuffle=True, random_state=RANDOM_STATE
        )
        for fold, (tr_idx, val_idx) in enumerate(skf.split(X_train, y_train), 1):
            clf = RandomForestClassifier(
                n_estimators=100,
                random_state=RANDOM_STATE,
                class_weight="balanced",
            )
            clf.fit(X_train.iloc[tr_idx], y_train[tr_idx])
            y_pred_val = clf.predict(X_train.iloc[val_idx])
            fold_f1 = float(f1_score(
                y_train[val_idx], y_pred_val, average="macro", zero_division=0
            ))
            cv_f1_scores.append(fold_f1)
            print(f"  Fold {fold} macro F1: {fold_f1:.3f}")

        cv_mean = float(np.mean(cv_f1_scores))
        cv_std = float(np.std(cv_f1_scores))
        print(f"\nCV macro F1 (mean ± std): {cv_mean:.3f} ± {cv_std:.3f}")

    # ------------------------------------------------------------------
    # Final model trained on the full training fold
    # ------------------------------------------------------------------
    final_model = RandomForestClassifier(
        n_estimators=100,
        random_state=RANDOM_STATE,
        class_weight="balanced",
    )
    final_model.fit(X_train, y_train)

    # ------------------------------------------------------------------
    # Evaluation on the held-out test set (reported as is — not tuned)
    # ------------------------------------------------------------------
    y_pred_test = final_model.predict(X_test)
    classes: list[str] = list(final_model.classes_)
    test_macro_f1 = float(f1_score(y_test, y_pred_test, average="macro", zero_division=0))
    report_dict = classification_report(y_test, y_pred_test, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_test, y_pred_test, labels=classes).tolist()

    print(f"\nTest-set classification report (n={len(X_test)}):")
    print(classification_report(y_test, y_pred_test, zero_division=0))
    print(f"Confusion matrix  (rows=true, cols=predicted)  classes={classes}")
    print(cm)

    # ------------------------------------------------------------------
    # Assemble and persist the model bundle
    # ------------------------------------------------------------------
    bundle: dict = {
        "model": final_model,
        "context_encoder": context_encoder,
        "feature_names": feature_names,
        "classes": classes,
        "model_version": MODEL_VERSION,
        "random_state": RANDOM_STATE,
        "train_size": int(len(X_train)),
        "test_size": int(len(X_test)),
        "cv_folds": n_folds,
        "cv_macro_f1_mean": float(np.mean(cv_f1_scores)) if cv_f1_scores else None,
        "cv_macro_f1_std": float(np.std(cv_f1_scores)) if cv_f1_scores else None,
        "test_macro_f1": test_macro_f1,
        "test_accuracy": float(report_dict.get("accuracy", 0.0)),
        "test_classification_report": report_dict,
        "test_confusion_matrix": cm,
        "confusion_matrix_labels": classes,
    }

    joblib.dump(bundle, MODEL_PATH)
    print(f"\nModel bundle saved → {MODEL_PATH}")

    # ------------------------------------------------------------------
    # Append entry to training log (append-only; never overwrites history)
    # ------------------------------------------------------------------
    log_entry = {
        "model_version": MODEL_VERSION,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_rows": int(len(df)),
        "label_distribution": label_dist,
        "train_size": int(len(X_train)),
        "test_size": int(len(X_test)),
        "cv_folds": n_folds,
        "cv_macro_f1_mean": bundle["cv_macro_f1_mean"],
        "cv_macro_f1_std": bundle["cv_macro_f1_std"],
        "test_macro_f1": bundle["test_macro_f1"],
        "test_classification_report": report_dict,
        "test_confusion_matrix": cm,
        "confusion_matrix_labels": classes,
        "limitations": [
            "n=50 is insufficient for robust generalisation.",
            "CRITICAL class is unrepresented; classifier cannot predict it.",
            "CSV entity columns are binary flags; counts are binarised at inference.",
            "Several MEDIUM/HIGH rows have all-zero entity flags — limited separability.",
            "Labels are manually annotated; inter-annotator agreement unknown.",
            "Personalisation is threshold adjustment, not per-user model retraining.",
        ],
    }

    history: list = []
    if TRAINING_LOG_PATH.exists():
        try:
            with TRAINING_LOG_PATH.open(encoding="utf-8") as fh:
                history = json.load(fh)
        except (json.JSONDecodeError, ValueError):
            history = []

    history.append(log_entry)
    TRAINING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRAINING_LOG_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"Training log appended → {TRAINING_LOG_PATH}")

    return bundle


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    train()
