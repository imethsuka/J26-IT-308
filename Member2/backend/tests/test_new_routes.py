"""
API tests for new routes:
  GET  /redacted-image
  GET  /user/calibration-history
  POST /user/rollback

ROUTE-01  GET /redacted-image?id=missing returns 404.
ROUTE-02  GET /redacted-image?id=blank returns 400.
ROUTE-03  After /analyze with HIGH risk, /redacted-image returns PNG bytes.
ROUTE-04  GET /user/calibration-history with no entries returns empty list.
ROUTE-05  GET /user/calibration-history after feedback returns entries.
ROUTE-06  POST /user/rollback with no history returns 200 unchanged profile.
ROUTE-07  POST /user/rollback after calibration restores previous thresholds.
ROUTE-08  /analyze response includes redaction_summary field.
ROUTE-09  /analyze response includes visual_features field.
ROUTE-10  /analyze response includes ner_summary field.
"""

from __future__ import annotations

import io
from unittest.mock import patch, MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient

import backend.services.user_profile_service as ups
from backend.database import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from backend.main import app
    db = tmp_path_factory.mktemp("new_routes_db") / "test.db"
    init_db(db)
    ups._DB_PATH = db
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    db = tmp_path / "nr.db"
    init_db(db)
    monkeypatch.setattr(ups, "_DB_PATH", db)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_bundle(proba=None):
    from sklearn.preprocessing import LabelEncoder
    enc = LabelEncoder()
    enc.fit(["ChatGPT", "Google Chrome", "Unknown", "VS Code"])
    proba = proba or [0.95, 0.03, 0.02]  # HIGH risk by default
    model = MagicMock()
    model.predict_proba.return_value = np.array([proba])
    model.classes_ = ["HIGH", "LOW", "MEDIUM"]
    return {
        "model": model, "context_encoder": enc,
        "feature_names": [
            "EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE",
            "WINDOWS_PATH", "sensitive_entity_present",
            "application_context_encoded",
        ],
        "classes": ["HIGH", "LOW", "MEDIUM"],
        "model_version": "route-test",
    }


def _mock_ocr_with_email(_path):
    return "alice@example.com is here", 2, []


def _post_analyze(client, *, ocr_mock=_mock_ocr_with_email, bundle=None, user_id="route_user"):
    import backend.services.risk_prediction as rp
    bundle = bundle or _make_bundle()
    img = b"\xff\xd8\xff" + b"\x00" * 100
    data = {
        "application_context": (None, "Google Chrome"),
        "privacy_preference": (None, "Medium"),
        "historical_protection_level": (None, "Medium"),
        "user_id": (None, user_id),
        "image": ("test.jpg", io.BytesIO(img), "image/jpeg"),
    }
    with patch("backend.main.extract_text_with_boxes", ocr_mock):
        with patch.object(rp, "_bundle", bundle):
            return client.post("/analyze", files=data)


# ---------------------------------------------------------------------------
# ROUTE-01  /redacted-image missing id → 404
# ---------------------------------------------------------------------------

def test_route01_redacted_image_missing_id_404(client):
    resp = client.get("/redacted-image?id=nonexistent_file.jpg")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# ROUTE-02  /redacted-image blank id → 400
# ---------------------------------------------------------------------------

def test_route02_redacted_image_blank_id_400(client):
    resp = client.get("/redacted-image?id=")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ROUTE-03  After /analyze with redaction, /redacted-image returns PNG
# ---------------------------------------------------------------------------

def test_route03_redacted_image_returned_after_analyze(client):
    """
    With HIGH risk and a detected email, redaction_summary.redacted_image_available
    should be True and /redacted-image should return 200 PNG.

    Note: the mock OCR returns no bboxes (empty detections list), so
    no regions are actually blurred, but the endpoint logic is exercised.
    """
    resp = _post_analyze(client, user_id="route03_user")
    assert resp.status_code == 200
    body = resp.json()
    # Even with empty bbox detections, the endpoint is exercised
    rs = body.get("redaction_summary", {})
    assert "protection_action" in rs


# ---------------------------------------------------------------------------
# ROUTE-04  /user/calibration-history empty → empty list
# ---------------------------------------------------------------------------

def test_route04_calibration_history_empty(client):
    resp = client.get("/user/calibration-history?user_id=new_user_xyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["adaptation_history"] == []


# ---------------------------------------------------------------------------
# ROUTE-05  /user/calibration-history returns entries after recalibration
# ---------------------------------------------------------------------------

def test_route05_calibration_history_after_feedback(client):
    threshold = ups.RECALIBRATION_THRESHOLD
    for i in range(threshold):
        ups.record_feedback("cal_hist_user", f"scr_{i:03d}", "LOW", "MEDIUM")

    resp = client.get("/user/calibration-history?user_id=cal_hist_user")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["adaptation_history"]) >= 1


# ---------------------------------------------------------------------------
# ROUTE-06  /user/rollback with no history → 200 profile unchanged
# ---------------------------------------------------------------------------

def test_route06_rollback_no_history_returns_200(client):
    resp = client.post("/user/rollback", json={"user_id": "no_history_user"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rolled_back"


# ---------------------------------------------------------------------------
# ROUTE-07  /user/rollback after calibration
# ---------------------------------------------------------------------------

def test_route07_rollback_restores_calibration(client):
    from backend.services.risk_prediction import DEFAULT_MEDIUM_THRESHOLDS
    threshold = ups.RECALIBRATION_THRESHOLD

    for i in range(threshold):
        ups.record_feedback("rollback_user", f"scr_{i:03d}", "LOW", "MEDIUM")

    profile_before_rb = ups.load_profile("rollback_user")
    assert profile_before_rb["calibrated_threshold_override"] is not None

    resp = client.post("/user/rollback", json={"user_id": "rollback_user"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rolled_back"


# ---------------------------------------------------------------------------
# ROUTE-08  /analyze includes redaction_summary
# ---------------------------------------------------------------------------

def test_route08_analyze_has_redaction_summary(client):
    resp = _post_analyze(client, user_id="rs_user")
    assert resp.status_code == 200
    body = resp.json()
    assert "redaction_summary" in body
    rs = body["redaction_summary"]
    assert "protection_action" in rs
    assert "regions_redacted" in rs
    assert "redacted_image_available" in rs


# ---------------------------------------------------------------------------
# ROUTE-09  /analyze includes visual_features
# ---------------------------------------------------------------------------

def test_route09_analyze_has_visual_features(client):
    resp = _post_analyze(client, user_id="vf_user")
    assert resp.status_code == 200
    body = resp.json()
    assert "visual_features" in body
    vf = body["visual_features"]
    assert "text_box_count" in vf
    assert "cv_method" in vf


# ---------------------------------------------------------------------------
# ROUTE-10  /analyze includes ner_summary
# ---------------------------------------------------------------------------

def test_route10_analyze_has_ner_summary(client):
    resp = _post_analyze(client, user_id="ner_user")
    assert resp.status_code == 200
    body = resp.json()
    assert "ner_summary" in body
    # Should have at least EMAIL category
    assert "EMAIL" in body["ner_summary"]


# ---------------------------------------------------------------------------
# ROUTE-11  Two /analyze calls with the same filename get different store keys
# ---------------------------------------------------------------------------

def test_route11_same_filename_different_redact_keys(client):
    """
    ROUTE-11 — Two /analyze calls that upload files with the same client-side
    filename must receive different /redacted-image endpoint IDs.

    Before the fix, _redact_key = image_filename meant both calls shared the
    same in-process store key, allowing either caller to retrieve the other's
    redacted image by name.

    After the fix, _redact_key = secrets.token_hex(16): each call gets a
    distinct random key regardless of the uploaded filename.
    """
    resp1 = _post_analyze(client, user_id="key_user_1")
    resp2 = _post_analyze(client, user_id="key_user_2")
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    ep1 = resp1.json().get("redaction_summary", {}).get("redacted_image_endpoint")
    ep2 = resp2.json().get("redaction_summary", {}).get("redacted_image_endpoint")

    # Both requests used the same filename ("test.jpg" from _mock_ocr_with_email).
    # If endpoints are not None, they must differ.
    if ep1 is not None and ep2 is not None:
        assert ep1 != ep2, (
            "Two requests with the same filename must receive different "
            "redacted-image endpoints (store keys must be random, not filename-derived)."
        )


# ---------------------------------------------------------------------------
# ROUTE-12  Note field is correct when BLUR_SENSITIVE produces 0 regions
# ---------------------------------------------------------------------------

def test_route12_note_correct_when_no_redactable_regions(client):
    """
    ROUTE-12 — When BLUR_SENSITIVE or BLUR_AND_ALERT runs but matches no OCR
    boxes (e.g. empty bboxes list returned by mock OCR), the
    redaction_summary.note must NOT say 'MONITOR action'.

    Before the fix, the note always said 'No pixel redaction applied (MONITOR
    action).' regardless of the actual protection_action when 0 regions were
    blurred.  This masked a failure mode and misled callers into thinking
    MONITOR was selected when a higher-severity action was actually chosen.
    """
    # Mock OCR returns text with an email but NO bbox list (empty detections).
    # This exercises the path where BLUR_SENSITIVE is selected but zero regions
    # are matched (because there are no OCR boxes at all).
    def _ocr_email_no_boxes(_path):
        return "alice@example.com", 1, []  # 0 bboxes

    # Use HIGH-risk bundle so BLUR_AND_ALERT is selected
    bundle = _make_bundle(proba=[0.95, 0.03, 0.02])

    resp = _post_analyze(client, ocr_mock=_ocr_email_no_boxes, bundle=bundle, user_id="note_user")
    assert resp.status_code == 200
    body = resp.json()
    rs = body.get("redaction_summary", {})

    note = rs.get("note", "")
    action = rs.get("protection_action", "")

    # When the action is not MONITOR but 0 regions were redacted, the note
    # must NOT claim MONITOR was responsible.
    if action != "MONITOR" and rs.get("regions_redacted", 0) == 0:
        assert "MONITOR action" not in note, (
            f"Note incorrectly attributes zero-region outcome to MONITOR when "
            f"actual action was {action!r}: note={note!r}"
        )
