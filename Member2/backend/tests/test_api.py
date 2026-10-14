"""
Integration tests for the FastAPI application.

OCR is mocked to avoid requiring EasyOCR and GPU during testing.
The ML model is mocked to avoid requiring a trained model artifact.
"""

import io
import json
import pytest
from unittest.mock import patch, MagicMock
import numpy as np
from fastapi.testclient import TestClient

import backend.services.user_profile_service as ups


# ---------------------------------------------------------------------------
# App import and client setup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from backend.main import app
    from backend.database import init_db
    db = tmp_path_factory.mktemp("api_db") / "test.db"
    init_db(db)
    ups._DB_PATH = db
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _mock_ocr(path):
    return "Sample text with alice@example.com", 5


def _mock_bundle():
    from sklearn.preprocessing import LabelEncoder
    enc = LabelEncoder()
    enc.fit(["ChatGPT", "Google Chrome", "Unknown", "VS Code"])
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.10, 0.75, 0.15]])
    model.classes_ = ["HIGH", "LOW", "MEDIUM"]
    return {
        "model": model,
        "context_encoder": enc,
        "feature_names": [
            "EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE",
            "WINDOWS_PATH", "sensitive_entity_present",
            "application_context_encoded",
        ],
        "classes": ["HIGH", "LOW", "MEDIUM"],
        "model_version": "test-1.0",
    }


def _analyze_multipart(client, *, user_id="test_user"):
    image_content = b"\xff\xd8\xff" + b"\x00" * 100  # minimal fake JPEG header
    data = {
        "application_context": (None, "Google Chrome"),
        "privacy_preference": (None, "Medium"),
        "historical_protection_level": (None, "Medium"),
        "user_id": (None, user_id),
        "image": ("test.jpg", io.BytesIO(image_content), "image/jpeg"),
    }
    return client.post("/analyze", files=data)


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_root_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["component"] == "CAPRLM"


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------

def test_analyze_returns_200_with_mocks(client):
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle()):
            resp = _analyze_multipart(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "risk_prediction" in body
    assert "detection_results" in body
    assert "protection_selection" in body
    assert "rule_baseline" in body
    assert "user_profile_summary" in body


def test_analyze_risk_prediction_has_model_status(client):
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle()):
            resp = _analyze_multipart(client)

    body = resp.json()
    assert body["risk_prediction"]["model_status"] == "ML_MODEL_ACTIVE"


def test_analyze_ocr_text_is_redacted(client):
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle()):
            resp = _analyze_multipart(client)

    body = resp.json()
    # OCR text must be redacted to avoid logging sensitive content
    assert "alice@example.com" not in body["ocr_summary"]["extracted_text"]


def test_analyze_missing_fields_returns_400(client):
    # Missing image entirely
    resp = client.post("/analyze", content=b"not multipart",
                       headers={"content-type": "text/plain"})
    assert resp.status_code == 400


def test_analyze_returns_rule_baseline(client):
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle()):
            resp = _analyze_multipart(client)

    body = resp.json()
    assert body["rule_baseline"]["model_type"] == "rule_based_baseline"
    assert body["rule_baseline"]["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# ---------------------------------------------------------------------------
# POST /feedback
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_profiles(tmp_path, monkeypatch):
    from backend.database import init_db
    db = tmp_path / "test.db"
    init_db(db)
    monkeypatch.setattr(ups, "_DB_PATH", db)


def test_feedback_valid_payload_returns_200(client):
    resp = client.post("/feedback", json={
        "user_id": "test_fb_user",
        "screenshot_id": "scr_001",
        "predicted_label": "LOW",
        "user_label": "MEDIUM",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "recorded"
    assert body["user_profile_summary"]["feedback_count"] == 1


def test_feedback_invalid_label_returns_422(client):
    resp = client.post("/feedback", json={
        "user_id": "test_fb_user",
        "screenshot_id": "scr_002",
        "predicted_label": "LOW",
        "user_label": "EXTREME",
    })
    assert resp.status_code == 422


def test_feedback_empty_user_id_returns_422(client):
    resp = client.post("/feedback", json={
        "user_id": "",
        "screenshot_id": "scr_003",
        "predicted_label": "LOW",
        "user_label": "HIGH",
    })
    assert resp.status_code == 422


def test_feedback_missing_field_returns_422(client):
    resp = client.post("/feedback", json={
        "user_id": "u",
        "screenshot_id": "s",
        "predicted_label": "LOW",
        # user_label missing
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /model-info
# ---------------------------------------------------------------------------

def test_model_info_returns_200(client):
    resp = client.get("/model-info")
    assert resp.status_code == 200
    body = resp.json()
    assert "model_available" in body


# ---------------------------------------------------------------------------
# POST /train (local only)
# ---------------------------------------------------------------------------

def test_train_endpoint_blocks_non_local(client):
    # TestClient uses 'testclient' as host by default
    resp = client.post("/train")
    # Should be 403 since TestClient host is not in _TRAIN_ALLOWED_HOSTS
    assert resp.status_code == 403


def test_train_error_does_not_leak_internal_paths(client):
    """Training failures must not expose internal file paths in the response."""
    import backend.main as main_mod
    import backend.services.train_model as tm

    def _raise(*a, **kw):
        raise RuntimeError("/secret/internal/data/caprlm_model.joblib was not found")

    with patch.object(main_mod, "_TRAIN_ALLOWED_HOSTS", {"testclient"}):
        with patch.object(tm, "train", _raise):
            resp = client.post("/train")

    assert resp.status_code == 500
    detail = resp.json().get("detail", "")
    assert "/secret/internal" not in detail
    assert "caprlm_model.joblib" not in detail
