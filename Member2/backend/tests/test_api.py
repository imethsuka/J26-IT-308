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


# ---------------------------------------------------------------------------
# POST /analyze — tokenized_summary and privacy guarantees
# ---------------------------------------------------------------------------

def _mock_bundle_high():
    """Mock that forces a HIGH risk prediction (P(HIGH)=0.95)."""
    from sklearn.preprocessing import LabelEncoder
    enc = LabelEncoder()
    enc.fit(["ChatGPT", "Google Chrome", "Unknown", "VS Code"])
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.95, 0.03, 0.02]])
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
        "model_version": "test-high",
    }


def test_analyze_response_has_tokenized_summary(client):
    """tokenized_summary must be present in every /analyze response."""
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle()):
            resp = _analyze_multipart(client)
    body = resp.json()
    assert "tokenized_summary" in body
    ts = body["tokenized_summary"]
    assert "tokenized_text" in ts
    assert "entity_token_count" in ts
    assert "tokenization_applied" in ts
    assert "note" in ts


def test_analyze_token_map_not_in_response(client):
    """The restoration token_map must never appear in the API response."""
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle()):
            resp = _analyze_multipart(client)
    body = resp.json()
    assert "token_map" not in body
    assert "restoration_map" not in body
    # Also verify it is not nested inside tokenized_summary
    ts = body.get("tokenized_summary", {})
    assert "token_map" not in ts
    assert "restoration_map" not in ts


def test_analyze_high_risk_detection_results_no_raw_email(client):
    """
    When risk is HIGH, detection_results must not contain the original
    email address string in any matches list.
    Mock OCR returns 'Sample text with alice@example.com'.
    """
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle_high()):
            resp = _analyze_multipart(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    email_result = body["detection_results"].get("EMAIL", {})
    for match in email_result.get("matches", []):
        assert match != "alice@example.com", (
            "Original sensitive email value found in API response matches list"
        )


def test_analyze_high_risk_tokenization_applied(client):
    """With a HIGH-risk prediction, tokenization_applied must be True."""
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle_high()):
            resp = _analyze_multipart(client)
    body = resp.json()
    assert body["tokenized_summary"]["tokenization_applied"] is True
    assert body["tokenized_summary"]["entity_token_count"] >= 1


def test_analyze_high_risk_tokenized_text_contains_token(client):
    """With HIGH risk and a detected email, tokenized_text must contain a token."""
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text", _mock_ocr):
        with patch.object(rp, "_bundle", _mock_bundle_high()):
            resp = _analyze_multipart(client)
    body = resp.json()
    tokenized_text = body["tokenized_summary"]["tokenized_text"]
    assert "alice@example.com" not in tokenized_text
    assert "[EMAIL_1]" in tokenized_text


# ---------------------------------------------------------------------------
# GET /model-info — training history consistency (SQLite as single source)
# ---------------------------------------------------------------------------

def test_model_info_no_training_history_returns_null_last_training(client):
    """When training_run table is empty, last_training must be null."""
    import backend.services.train_model as tm
    from unittest.mock import MagicMock, patch

    fake_path = MagicMock()
    fake_path.exists.return_value = True

    with patch.object(tm, "MODEL_PATH", fake_path):
        resp = client.get("/model-info")

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_available"] is True
    # No training run rows in the isolated test DB → last_training must be null
    assert body["last_training"] is None


def test_model_info_returns_last_training_run_from_sqlite(client, tmp_path):
    """After a training_run row is inserted, /model-info must return it."""
    from backend.database import get_connection, init_db
    import backend.services.train_model as tm
    from unittest.mock import MagicMock, patch

    db = tmp_path / "mi_test.db"
    init_db(db)

    conn = get_connection(db)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO training_run (
                    model_version, trained_at, dataset_rows,
                    label_low, label_medium, label_high, label_critical,
                    train_size, test_size, cv_folds,
                    cv_macro_f1_mean, cv_macro_f1_std,
                    test_macro_f1, test_accuracy
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                ("1.0.0", "2026-10-14T12:00:00Z", 50,
                 38, 10, 2, 0,
                 40, 10, 2,
                 0.353, 0.075,
                 0.444, 0.80),
            )
    finally:
        conn.close()

    fake_path = MagicMock()
    fake_path.exists.return_value = True

    import backend.services.user_profile_service as _ups
    with patch.object(_ups, "_DB_PATH", db):
        with patch.object(tm, "MODEL_PATH", fake_path):
            resp = client.get("/model-info")

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_available"] is True
    lt = body["last_training"]
    assert lt is not None
    assert lt["model_version"] == "1.0.0"
    assert lt["trained_at"] == "2026-10-14T12:00:00Z"
    assert lt["dataset_rows"] == 50
    assert lt["test_macro_f1"] == pytest.approx(0.444)
    assert lt["test_accuracy"] == pytest.approx(0.80)


def test_model_info_returns_most_recent_run_when_multiple_exist(client, tmp_path):
    """When multiple training_run rows exist, /model-info returns the latest."""
    from backend.database import get_connection, init_db
    import backend.services.train_model as tm
    from unittest.mock import MagicMock, patch

    db = tmp_path / "mi_multi.db"
    init_db(db)

    conn = get_connection(db)
    try:
        with conn:
            for version, ts in [("1.0.0", "2026-10-01T00:00:00Z"),
                                 ("1.0.1", "2026-10-14T00:00:00Z")]:
                conn.execute(
                    """
                    INSERT INTO training_run (
                        model_version, trained_at, dataset_rows,
                        label_low, label_medium, label_high, label_critical,
                        train_size, test_size, cv_folds,
                        cv_macro_f1_mean, cv_macro_f1_std,
                        test_macro_f1, test_accuracy
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (version, ts, 50, 38, 10, 2, 0, 40, 10, 2,
                     0.35, 0.07, 0.44, 0.80),
                )
    finally:
        conn.close()

    fake_path = MagicMock()
    fake_path.exists.return_value = True

    import backend.services.user_profile_service as _ups
    with patch.object(_ups, "_DB_PATH", db):
        with patch.object(tm, "MODEL_PATH", fake_path):
            resp = client.get("/model-info")

    body = resp.json()
    assert body["last_training"]["model_version"] == "1.0.1"


def test_model_info_no_model_file_returns_model_available_false(client):
    """When no model file exists, model_available must be False."""
    import backend.services.train_model as tm
    from unittest.mock import MagicMock, patch

    fake_path = MagicMock()
    fake_path.exists.return_value = False

    with patch.object(tm, "MODEL_PATH", fake_path):
        resp = client.get("/model-info")

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_available"] is False
    assert "last_training" not in body


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
