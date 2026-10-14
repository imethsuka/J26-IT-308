"""
Tests for baseline_eval.evaluate_baseline_comparison().

These tests verify the evaluation logic without running model training.
A synthetic 20-row dataset is used so tests run offline and do not depend
on the protected CAPRLM_annotation_50.csv or a trained model artifact.

Structure
---------
Section 1 — CSV-to-features bridge
Section 2 — _compute_metrics helper
Section 3 — evaluate_baseline_comparison (rule baseline only, no ML model)
Section 4 — evaluate_baseline_comparison (with synthetic ML model)
Section 5 — train/test split consistency (no leakage)
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from sklearn.preprocessing import LabelEncoder

from backend.services.baseline_eval import (
    _compute_metrics,
    _csv_row_to_features,
    evaluate_baseline_comparison,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv(rows: list[dict], tmp_path: Path) -> Path:
    """Write a minimal annotation CSV to tmp_path and return its path."""
    fieldnames = [
        "screenshot_id", "application_context",
        "EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE",
        "WINDOWS_PATH", "sensitive_entity_present", "privacy_risk_label",
    ]
    p = tmp_path / "test_annotations.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return p


def _row(sid: str, ctx: str, label: str, **flags) -> dict:
    base = {
        "screenshot_id": sid, "application_context": ctx,
        "EMAIL": 0, "PHONE": 0, "URL": 0, "IP_ADDRESS": 0,
        "DATE": 0, "WINDOWS_PATH": 0, "sensitive_entity_present": 0,
        "privacy_risk_label": label,
    }
    base.update(flags)
    return base


def _synthetic_dataset(n_low=14, n_medium=4, n_high=2) -> list[dict]:
    rows: list[dict] = []
    for i in range(n_low):
        rows.append(_row(f"low_{i}", "ChatGPT", "LOW"))
    for i in range(n_medium):
        rows.append(_row(f"med_{i}", "Google Chrome", "MEDIUM", URL=1, sensitive_entity_present=1))
    for i in range(n_high):
        rows.append(_row(f"hi_{i}", "Other", "HIGH", EMAIL=1))
    return rows


# ---------------------------------------------------------------------------
# Section 1 — CSV-to-features bridge
# ---------------------------------------------------------------------------

class TestCsvRowToFeatures:
    def _series(self, **kwargs) -> pd.Series:
        base = {
            "EMAIL": 0, "PHONE": 0, "URL": 0, "IP_ADDRESS": 0,
            "DATE": 0, "WINDOWS_PATH": 0, "sensitive_entity_present": 0,
            "application_context": "Other",
        }
        base.update(kwargs)
        return pd.Series(base)

    def test_all_zeros_maps_correctly(self):
        feats = _csv_row_to_features(self._series())
        assert feats["email_count"] == 0
        assert feats["phone_count"] == 0
        assert feats["url_count"] == 0
        assert feats["ip_count"] == 0
        assert feats["windows_path_count"] == 0
        assert feats["sensitive_entity_count"] == 0

    def test_email_flag_maps_to_email_count(self):
        feats = _csv_row_to_features(self._series(EMAIL=1))
        assert feats["email_count"] == 1

    def test_phone_flag_maps_to_phone_count(self):
        feats = _csv_row_to_features(self._series(PHONE=1))
        assert feats["phone_count"] == 1

    def test_ip_address_maps_to_ip_count(self):
        feats = _csv_row_to_features(self._series(IP_ADDRESS=1))
        assert feats["ip_count"] == 1

    def test_application_context_preserved(self):
        feats = _csv_row_to_features(self._series(application_context="Google Chrome"))
        assert feats["application_context"] == "Google Chrome"

    def test_null_application_context_becomes_empty_string(self):
        feats = _csv_row_to_features(self._series(application_context=None))
        assert feats["application_context"] == ""

    def test_all_ones_maps_correctly(self):
        feats = _csv_row_to_features(self._series(
            EMAIL=1, PHONE=1, URL=1, IP_ADDRESS=1,
            DATE=1, WINDOWS_PATH=1, sensitive_entity_present=1,
        ))
        assert feats["email_count"] == 1
        assert feats["phone_count"] == 1
        assert feats["url_count"] == 1
        assert feats["ip_count"] == 1
        assert feats["windows_path_count"] == 1
        assert feats["sensitive_entity_count"] == 1


# ---------------------------------------------------------------------------
# Section 2 — _compute_metrics helper
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def test_perfect_predictions_macro_f1_one(self):
        y_true = ["LOW", "MEDIUM", "HIGH"]
        y_pred = ["LOW", "MEDIUM", "HIGH"]
        m = _compute_metrics(y_true, y_pred, ["HIGH", "LOW", "MEDIUM"])
        assert m["macro_f1"] == pytest.approx(1.0)
        assert m["accuracy"] == pytest.approx(1.0)

    def test_all_wrong_macro_f1_zero(self):
        y_true = ["LOW", "LOW", "LOW"]
        y_pred = ["HIGH", "HIGH", "HIGH"]
        m = _compute_metrics(y_true, y_pred, ["HIGH", "LOW"])
        # LOW has 0 TP; HIGH has 0 TP → both F1=0 → macro=0
        assert m["macro_f1"] == pytest.approx(0.0)
        assert m["accuracy"] == pytest.approx(0.0)

    def test_classification_report_present(self):
        y_true = ["LOW", "MEDIUM"]
        y_pred = ["LOW", "LOW"]
        m = _compute_metrics(y_true, y_pred, ["LOW", "MEDIUM"])
        assert "classification_report" in m
        assert isinstance(m["classification_report"], dict)

    def test_macro_f1_imbalanced(self):
        # 4 LOW correct, 1 MEDIUM wrong (predicted LOW)
        # LOW:    precision=4/5=0.80, recall=4/4=1.00, F1=2*(0.8*1.0)/(0.8+1.0)=0.889
        # MEDIUM: precision=0/0=0 (zero_division=0), recall=0/1=0, F1=0.0
        # macro = (0.889 + 0.0) / 2 ≈ 0.444
        y_true = ["LOW"] * 4 + ["MEDIUM"]
        y_pred = ["LOW"] * 4 + ["LOW"]
        m = _compute_metrics(y_true, y_pred, ["LOW", "MEDIUM"])
        assert m["macro_f1"] == pytest.approx(0.444, abs=0.01)

    def test_returns_rounded_values(self):
        y_true = ["LOW", "LOW", "MEDIUM"]
        y_pred = ["LOW", "MEDIUM", "LOW"]
        m = _compute_metrics(y_true, y_pred, ["LOW", "MEDIUM"])
        # Verify values are rounded to 4 decimal places
        assert m["macro_f1"] == round(m["macro_f1"], 4)
        assert m["accuracy"] == round(m["accuracy"], 4)


# ---------------------------------------------------------------------------
# Section 3 — evaluate_baseline_comparison (rule baseline only, no ML model)
# ---------------------------------------------------------------------------

class TestEvaluateRuleBaselineOnly:
    def test_returns_required_keys(self, tmp_path):
        csv_path = _make_csv(_synthetic_dataset(), tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",  # does not exist
        )
        for key in ("dataset_rows", "train_rows", "test_rows",
                    "label_distribution", "all_classes",
                    "rule_baseline", "ml_model", "ml_warning", "limitations"):
            assert key in result, f"Missing key: {key}"

    def test_dataset_rows_matches_csv(self, tmp_path):
        rows = _synthetic_dataset(n_low=14, n_medium=4, n_high=2)
        csv_path = _make_csv(rows, tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        assert result["dataset_rows"] == 20

    def test_train_test_split_sizes(self, tmp_path):
        rows = _synthetic_dataset(n_low=14, n_medium=4, n_high=2)
        csv_path = _make_csv(rows, tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        assert result["train_rows"] + result["test_rows"] == result["dataset_rows"]
        # TEST_SIZE=0.20 → 4 test rows from 20
        assert result["test_rows"] == 4

    def test_ml_model_none_when_missing(self, tmp_path):
        csv_path = _make_csv(_synthetic_dataset(), tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        assert result["ml_model"] is None
        assert result["ml_warning"] is not None
        assert "not found" in result["ml_warning"].lower()

    def test_rule_baseline_metrics_present(self, tmp_path):
        csv_path = _make_csv(_synthetic_dataset(), tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        rb = result["rule_baseline"]
        assert "macro_f1" in rb
        assert "accuracy" in rb
        assert "classification_report" in rb

    def test_rule_baseline_macro_f1_in_range(self, tmp_path):
        csv_path = _make_csv(_synthetic_dataset(), tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        macro_f1 = result["rule_baseline"]["macro_f1"]
        assert 0.0 <= macro_f1 <= 1.0

    def test_limitations_list_present(self, tmp_path):
        csv_path = _make_csv(_synthetic_dataset(), tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        assert isinstance(result["limitations"], list)
        assert len(result["limitations"]) >= 4

    def test_label_distribution_matches_csv(self, tmp_path):
        rows = _synthetic_dataset(n_low=14, n_medium=4, n_high=2)
        csv_path = _make_csv(rows, tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        dist = result["label_distribution"]
        assert dist.get("LOW", 0) == 14
        assert dist.get("MEDIUM", 0) == 4
        assert dist.get("HIGH", 0) == 2


# ---------------------------------------------------------------------------
# Section 4 — evaluate_baseline_comparison with synthetic ML model
# ---------------------------------------------------------------------------

class TestEvaluateWithMlModel:
    def _make_real_bundle(self, classes, all_contexts, df_train, y_train):
        """Return a picklable bundle dict using a real DummyClassifier."""
        from sklearn.dummy import DummyClassifier
        from backend.services.train_model import build_features

        enc = LabelEncoder()
        enc.fit(all_contexts)

        X_train, _ = build_features(df_train, context_encoder=enc)
        clf = DummyClassifier(strategy="most_frequent", random_state=0)
        clf.fit(X_train, y_train)

        return {
            "model": clf,
            "context_encoder": enc,
            "feature_names": list(X_train.columns),
            "classes": classes,
            "model_version": "test-dummy",
        }

    def _make_bundle_for_dataset(self, rows, tmp_path):
        import joblib
        from sklearn.model_selection import train_test_split as _split

        df = pd.DataFrame(rows)
        y = df["privacy_risk_label"].str.strip().str.upper().values
        df_train, _, y_train, _ = _split(df, y, test_size=0.20, random_state=42, stratify=y)

        bundle = self._make_real_bundle(
            classes=sorted(set(y)),
            all_contexts=["ChatGPT", "Google Chrome", "Other", "Unknown"],
            df_train=df_train,
            y_train=y_train,
        )
        model_path = tmp_path / "dummy_model.joblib"
        joblib.dump(bundle, model_path)
        return model_path

    def test_ml_metrics_present_when_model_exists(self, tmp_path):
        rows = _synthetic_dataset(n_low=14, n_medium=4, n_high=2)
        csv_path = _make_csv(rows, tmp_path)
        model_path = self._make_bundle_for_dataset(rows, tmp_path)

        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=model_path,
        )
        assert result["ml_model"] is not None
        assert result["ml_warning"] is None
        ml = result["ml_model"]
        assert "macro_f1" in ml
        assert "accuracy" in ml
        assert 0.0 <= ml["macro_f1"] <= 1.0

    def test_ml_and_rule_both_evaluated_on_same_test_set(self, tmp_path):
        """Both classifiers must report on the same number of rows."""
        rows = _synthetic_dataset(n_low=14, n_medium=4, n_high=2)
        csv_path = _make_csv(rows, tmp_path)
        model_path = self._make_bundle_for_dataset(rows, tmp_path)

        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=model_path,
        )
        # Classification report covers the same n rows
        rb_support = sum(
            v.get("support", 0)
            for k, v in result["rule_baseline"]["classification_report"].items()
            if k not in ("accuracy", "macro avg", "weighted avg")
        )
        ml_support = sum(
            v.get("support", 0)
            for k, v in result["ml_model"]["classification_report"].items()
            if k not in ("accuracy", "macro avg", "weighted avg")
        )
        assert rb_support == ml_support


# ---------------------------------------------------------------------------
# Section 5 — train/test split consistency (no leakage)
# ---------------------------------------------------------------------------

class TestSplitConsistency:
    def test_split_is_deterministic(self, tmp_path):
        """Two calls with the same CSV must produce the same test rows."""
        rows = _synthetic_dataset(n_low=14, n_medium=4, n_high=2)
        csv_path = _make_csv(rows, tmp_path)

        r1 = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        r2 = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        assert r1["rule_baseline"]["macro_f1"] == r2["rule_baseline"]["macro_f1"]
        assert r1["rule_baseline"]["accuracy"] == r2["rule_baseline"]["accuracy"]

    def test_test_rows_plus_train_rows_equals_total(self, tmp_path):
        rows = _synthetic_dataset(n_low=14, n_medium=4, n_high=2)
        csv_path = _make_csv(rows, tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        assert result["train_rows"] + result["test_rows"] == result["dataset_rows"]

    def test_stratification_preserves_all_classes(self, tmp_path):
        """With stratify=y, every present class must appear in both folds."""
        rows = _synthetic_dataset(n_low=14, n_medium=4, n_high=2)
        csv_path = _make_csv(rows, tmp_path)
        result = evaluate_baseline_comparison(
            dataset_path=csv_path,
            model_path=tmp_path / "no_model.joblib",
        )
        # All classes present in the full dataset should be in all_classes
        assert "LOW" in result["all_classes"]
        assert "MEDIUM" in result["all_classes"]
        assert "HIGH" in result["all_classes"]
