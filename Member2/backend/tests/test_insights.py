"""
Model Insights dashboard API tests.

INS-01  GET /insights/model-overview returns 200 with required keys.
INS-02  feature_names in model-overview are in the expected order.
INS-03  GET /insights/feature-importance returns 200.
INS-04  feature_importances are sorted descending.
INS-05  feature importances sum to approximately 1.0.
INS-06  GET /insights/evaluation returns 200.
INS-07  evaluation response includes confusion_matrix and confusion_matrix_labels.
INS-08  POST /insights/predict with valid inputs returns 200.
INS-09  POST /insights/predict with out-of-range binary feature returns 422.
INS-10  POST /insights/predict with invalid privacy_preference returns 422.
INS-11  GET /insights/tree/0 returns 200 with a tree structure.
INS-12  GET /insights/tree with invalid index returns 400.
INS-13  GET /insights/model-overview when model unavailable returns model_available=False.
INS-14  GET /insights/feature-importance when model unavailable returns 503.
INS-15  GET /insights/evaluation when no training log returns available=False.
INS-16  tree structure includes feature_names and class_names.
INS-17  /insights/predict result contains disclaimer and input_features.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder

import backend.services.user_profile_service as ups
import backend.services.risk_prediction as rp
from backend.database import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FEATURE_NAMES = [
    "EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE",
    "WINDOWS_PATH", "sensitive_entity_present",
    "application_context_encoded",
]

_CONTEXT_VALUES = [
    "ChatGPT", "Google Chrome", "Microsoft Word", "Other",
    "Unknown", "Visual Studio Code",
]


@pytest.fixture(scope="module")
def real_bundle():
    """A real (tiny) sklearn RF bundle for endpoints that inspect tree_."""
    X = np.array([
        [1, 0, 0, 0, 0, 0, 0, 0],
        [0, 1, 0, 0, 0, 0, 0, 1],
        [0, 0, 0, 0, 0, 0, 1, 2],
        [1, 1, 1, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 1, 1, 1, 1],
        [0, 0, 0, 0, 0, 0, 0, 3],
        [1, 0, 1, 0, 0, 0, 1, 2],
    ])
    y = np.array(["HIGH", "LOW", "MEDIUM", "HIGH", "LOW", "LOW", "MEDIUM"])
    clf = RandomForestClassifier(n_estimators=5, random_state=42)
    clf.fit(X, y)
    enc = LabelEncoder()
    enc.fit(_CONTEXT_VALUES)
    return {
        "model": clf,
        "context_encoder": enc,
        "feature_names": _FEATURE_NAMES,
        "classes": list(clf.classes_),
        "model_version": "test-ins",
        "random_state": 42,
        "train_size": 6,
        "test_size": 1,
        "cv_folds": 2,
        "cv_macro_f1_mean": 0.5,
        "cv_macro_f1_std": 0.1,
        "test_macro_f1": 0.5,
        "test_accuracy": 0.5,
    }


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from backend.main import app
    db = tmp_path_factory.mktemp("ins_db") / "test.db"
    init_db(db)
    ups._DB_PATH = db
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    db = tmp_path / "ins.db"
    init_db(db)
    monkeypatch.setattr(ups, "_DB_PATH", db)


def _valid_predict_payload(**overrides):
    base = {
        "email": 1,
        "phone": 0,
        "url": 0,
        "ip_address": 0,
        "date": 0,
        "windows_path": 0,
        "sensitive_entity_present": 0,
        "application_context": "Google Chrome",
        "privacy_preference": "Medium",
        "historical_protection_level": "Medium",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# INS-01  model-overview returns 200 with required keys
# ---------------------------------------------------------------------------

def test_ins01_model_overview_returns_200(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.get("/insights/model-overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("model_available", "algorithm", "n_estimators", "n_features",
                "feature_names", "class_labels", "model_version"):
        assert key in body, f"INS-01: missing key '{key}'"


# ---------------------------------------------------------------------------
# INS-02  feature_names in correct order
# ---------------------------------------------------------------------------

def test_ins02_feature_names_order(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.get("/insights/model-overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_names"] == _FEATURE_NAMES, (
        f"INS-02: feature names mismatch. Got {body['feature_names']}"
    )


# ---------------------------------------------------------------------------
# INS-03  feature-importance returns 200
# ---------------------------------------------------------------------------

def test_ins03_feature_importance_returns_200(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.get("/insights/feature-importance")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "feature_importances" in body
    assert len(body["feature_importances"]) == len(_FEATURE_NAMES)


# ---------------------------------------------------------------------------
# INS-04  importances are sorted descending
# ---------------------------------------------------------------------------

def test_ins04_feature_importances_sorted_descending(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.get("/insights/feature-importance")
    assert resp.status_code == 200
    values = [item["importance"] for item in resp.json()["feature_importances"]]
    assert values == sorted(values, reverse=True), "INS-04: importances not sorted descending"


# ---------------------------------------------------------------------------
# INS-05  importances sum to approximately 1.0
# ---------------------------------------------------------------------------

def test_ins05_feature_importances_sum_to_one(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.get("/insights/feature-importance")
    assert resp.status_code == 200
    total = sum(item["importance"] for item in resp.json()["feature_importances"])
    assert abs(total - 1.0) < 1e-4, f"INS-05: importances sum to {total}, expected ~1.0"


# ---------------------------------------------------------------------------
# INS-06  evaluation returns 200
# ---------------------------------------------------------------------------

def test_ins06_evaluation_returns_200(client, tmp_path):
    log = [{
        "model_version": "1.0.0",
        "trained_at": "2026-10-13T23:08:37Z",
        "dataset_rows": 50,
        "label_distribution": {"LOW": 38, "MEDIUM": 10, "HIGH": 2},
        "train_size": 40,
        "test_size": 10,
        "cv_folds": 2,
        "cv_macro_f1_mean": 0.35,
        "cv_macro_f1_std": 0.07,
        "test_macro_f1": 0.44,
        "test_classification_report": {
            "LOW": {"precision": 0.8, "recall": 1.0, "f1-score": 0.89, "support": 8.0},
            "MEDIUM": {"precision": 0.0, "recall": 0.0, "f1-score": 0.0, "support": 2.0},
            "accuracy": 0.8,
            "macro avg": {"precision": 0.4, "recall": 0.5, "f1-score": 0.44, "support": 10.0},
            "weighted avg": {"precision": 0.64, "recall": 0.8, "f1-score": 0.71, "support": 10.0},
        },
        "test_confusion_matrix": [[0, 0, 0], [0, 8, 0], [0, 2, 0]],
        "confusion_matrix_labels": ["HIGH", "LOW", "MEDIUM"],
        "limitations": ["n=50 is insufficient."],
    }]
    log_path = tmp_path / "training_log.json"
    log_path.write_text(json.dumps(log), encoding="utf-8")

    import backend.main as bm
    with patch.object(bm, "_TRAINING_LOG_PATH", log_path):
        resp = client.get("/insights/evaluation")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True


# ---------------------------------------------------------------------------
# INS-07  evaluation has confusion_matrix and labels
# ---------------------------------------------------------------------------

def test_ins07_evaluation_has_confusion_matrix(client, tmp_path):
    log = [{
        "model_version": "1.0.0",
        "trained_at": "2026-10-13T23:08:37Z",
        "dataset_rows": 50,
        "label_distribution": {"LOW": 38, "MEDIUM": 10, "HIGH": 2},
        "train_size": 40,
        "test_size": 10,
        "cv_folds": 2,
        "cv_macro_f1_mean": 0.35,
        "cv_macro_f1_std": 0.07,
        "test_macro_f1": 0.44,
        "test_classification_report": {
            "LOW": {"precision": 0.8, "recall": 1.0, "f1-score": 0.89, "support": 8.0},
            "accuracy": 0.8,
            "macro avg": {"precision": 0.4, "recall": 0.5, "f1-score": 0.44, "support": 10.0},
            "weighted avg": {"precision": 0.64, "recall": 0.8, "f1-score": 0.71, "support": 10.0},
        },
        "test_confusion_matrix": [[0, 0], [0, 8]],
        "confusion_matrix_labels": ["HIGH", "LOW"],
        "limitations": [],
    }]
    log_path = tmp_path / "training_log.json"
    log_path.write_text(json.dumps(log), encoding="utf-8")

    import backend.main as bm
    with patch.object(bm, "_TRAINING_LOG_PATH", log_path):
        resp = client.get("/insights/evaluation")
    assert resp.status_code == 200
    body = resp.json()
    assert "confusion_matrix" in body
    assert "confusion_matrix_labels" in body
    assert body["confusion_matrix_labels"] == ["HIGH", "LOW"]


# ---------------------------------------------------------------------------
# INS-08  /insights/predict with valid inputs returns 200
# ---------------------------------------------------------------------------

def test_ins08_predict_valid_inputs_returns_200(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.post("/insights/predict", json=_valid_predict_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "risk_prediction" in body
    assert "input_features" in body
    assert body["risk_prediction"]["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL", "UNAVAILABLE")


# ---------------------------------------------------------------------------
# INS-09  out-of-range binary feature returns 422
# ---------------------------------------------------------------------------

def test_ins09_predict_invalid_binary_feature_returns_422(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.post("/insights/predict", json=_valid_predict_payload(email=2))
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# INS-10  invalid privacy_preference returns 422
# ---------------------------------------------------------------------------

def test_ins10_predict_invalid_preference_returns_422(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.post("/insights/predict",
                           json=_valid_predict_payload(privacy_preference="VeryHigh"))
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# INS-11  GET /insights/tree/0 returns 200 with a tree structure
# ---------------------------------------------------------------------------

def test_ins11_tree_0_returns_200(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.get("/insights/tree/0")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "tree" in body
    assert body["tree"]["type"] in ("split", "leaf")
    assert body["tree_index"] == 0


# ---------------------------------------------------------------------------
# INS-12  invalid tree index returns 400
# ---------------------------------------------------------------------------

def test_ins12_tree_invalid_index_returns_400(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.get("/insights/tree/9999")
    assert resp.status_code == 400, resp.text
    assert "tree_index" in resp.json().get("detail", "").lower() or "must be" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# INS-13  model-overview when model unavailable
# ---------------------------------------------------------------------------

def test_ins13_model_overview_when_unavailable(client):
    with patch.object(rp, "_bundle", None), \
         patch.object(rp, "_load_bundle", side_effect=RuntimeError("no model")):
        resp = client.get("/insights/model-overview")
    assert resp.status_code == 200
    assert resp.json()["model_available"] is False


# ---------------------------------------------------------------------------
# INS-14  feature-importance when model unavailable returns 503
# ---------------------------------------------------------------------------

def test_ins14_feature_importance_unavailable_returns_503(client):
    with patch.object(rp, "_bundle", None), \
         patch.object(rp, "_load_bundle", side_effect=RuntimeError("no model")):
        resp = client.get("/insights/feature-importance")
    assert resp.status_code == 503, resp.text


# ---------------------------------------------------------------------------
# INS-15  evaluation when no training log returns available=False
# ---------------------------------------------------------------------------

def test_ins15_evaluation_no_log_returns_available_false(client, tmp_path):
    missing_path = tmp_path / "nonexistent_log.json"
    import backend.main as bm
    with patch.object(bm, "_TRAINING_LOG_PATH", missing_path):
        resp = client.get("/insights/evaluation")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


# ---------------------------------------------------------------------------
# INS-16  tree structure includes feature_names and class_names
# ---------------------------------------------------------------------------

def test_ins16_tree_structure_has_feature_and_class_names(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.get("/insights/tree/0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_names"] == _FEATURE_NAMES
    assert isinstance(body["class_names"], list)
    assert len(body["class_names"]) > 0


# ---------------------------------------------------------------------------
# INS-17  predict result contains disclaimer and input_features
# ---------------------------------------------------------------------------

def test_ins17_predict_contains_disclaimer_and_input_features(client, real_bundle):
    with patch.object(rp, "_bundle", real_bundle):
        resp = client.post("/insights/predict", json=_valid_predict_payload(email=1, phone=1))
    assert resp.status_code == 200
    body = resp.json()
    assert "disclaimer" in body
    assert "experimental" in body["disclaimer"].lower()
    feats = body["input_features"]
    assert feats["EMAIL"] == 1
    assert feats["PHONE"] == 1
    assert feats["application_context"] == "Google Chrome"
