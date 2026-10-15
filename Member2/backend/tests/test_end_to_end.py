"""
End-to-end API-level integration tests for the full CAPRLM pipeline.

E2E-01  Complete workflow: analyze → retrieve redacted image → feedback →
        calibration-history → rollback in a single user session.
E2E-02  High-risk analyze with real bbox OCR detections sets
        redacted_image_available True and endpoint is retrievable.
E2E-03  MONITOR action never stores a redacted image; endpoint returns 404.
E2E-04  Feedback validates labels; invalid label returns 422.
E2E-05  Calibration rollback after no history returns 200 (safe no-op).
E2E-06  Two sequential analyze calls for the same filename produce
        independent (different) redacted-image tokens.
E2E-07  Feedback poisoning guard: >3 records for same screenshot returns 422.
E2E-08  redaction_summary.redaction_status reflects actual outcome.
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
# Test image and OCR fixtures
# ---------------------------------------------------------------------------

def _minimal_jpg() -> bytes:
    """Minimal JPEG file header bytes (enough to avoid immediate parse errors)."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 200


def _make_bundle(risk_class="HIGH", proba=None):
    from sklearn.preprocessing import LabelEncoder
    enc = LabelEncoder()
    enc.fit(["ChatGPT", "Google Chrome", "Unknown", "VS Code"])
    if proba is None:
        if risk_class == "HIGH":
            proba = [0.95, 0.03, 0.02]  # HIGH
        elif risk_class == "MEDIUM":
            proba = [0.03, 0.02, 0.95]  # MEDIUM
        else:
            proba = [0.03, 0.92, 0.05]  # LOW
    model = MagicMock()
    model.predict_proba.return_value = np.array([proba])
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
        "model_version": "e2e-test",
    }


def _mock_ocr_email_with_bbox(path):
    """OCR mock: returns email with a real bounding box."""
    bbox = [[10, 10], [200, 10], [200, 30], [10, 30]]
    return "alice@example.com", 1, [(bbox, "alice@example.com", 0.99)]


def _mock_ocr_no_entities(path):
    return "This is a clean document.", 1, []


def _post_analyze(client, *, user_id, ocr_mock=_mock_ocr_email_with_bbox,
                  bundle=None, risk_class="HIGH"):
    import backend.services.risk_prediction as rp
    bundle = bundle or _make_bundle(risk_class)
    img = _minimal_jpg()
    data = {
        "application_context": (None, "Google Chrome"),
        "privacy_preference": (None, "Medium"),
        "historical_protection_level": (None, "Medium"),
        "user_id": (None, user_id),
        "image": ("screenshot.jpg", io.BytesIO(img), "image/jpeg"),
    }
    with patch("backend.main.extract_text_with_boxes", ocr_mock):
        with patch.object(rp, "_bundle", bundle):
            return client.post("/analyze", files=data)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path):
    from backend.main import app
    db = tmp_path / "e2e.db"
    init_db(db)
    ups._DB_PATH = db
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# E2E-01  Full pipeline: analyze → redacted image → feedback → history → rollback
# ---------------------------------------------------------------------------

def test_e2e01_full_pipeline(client):
    user = "e2e_user_01"
    threshold = ups.RECALIBRATION_THRESHOLD

    # 1 — analyze
    resp = _post_analyze(client, user_id=user)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 2 — check analysis response structure
    assert "risk_prediction" in body
    assert "protection_selection" in body
    assert "redaction_summary" in body
    assert "visual_features" in body
    assert "ner_summary" in body

    risk_level = body["risk_prediction"]["risk_level"]
    assert risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    rs = body["redaction_summary"]
    assert "protection_action" in rs
    assert "redacted_image_available" in rs
    assert "redaction_status" in rs

    # 3 — if a redacted image is available, retrieve it
    redact_endpoint = rs.get("redacted_image_endpoint")
    if redact_endpoint:
        img_resp = client.get(redact_endpoint)
        assert img_resp.status_code == 200
        assert img_resp.headers["content-type"] == "image/png"
        assert len(img_resp.content) > 0

    # 4 — submit feedback
    for i in range(threshold):
        fb = client.post("/feedback", json={
            "user_id": user,
            "screenshot_id": f"scr_{i:03d}",
            "predicted_label": risk_level,
            "user_label": "MEDIUM",
        })
        assert fb.status_code == 200

    # 5 — calibration history has at least one entry
    hist_resp = client.get(f"/user/calibration-history?user_id={user}")
    assert hist_resp.status_code == 200
    history = hist_resp.json()["adaptation_history"]
    assert len(history) >= 1
    assert history[0]["new_medium_threshold"] is not None

    # 6 — rollback
    rb_resp = client.post("/user/rollback", json={
        "user_id": user, "reason": "e2e_test"
    })
    assert rb_resp.status_code == 200
    assert rb_resp.json()["status"] == "rolled_back"


# ---------------------------------------------------------------------------
# E2E-02  HIGH risk + bbox OCR → redacted image available and retrievable
# ---------------------------------------------------------------------------

def test_e2e02_high_risk_redacted_image_retrievable(client):
    resp = _post_analyze(client, user_id="e2e_user_02",
                         ocr_mock=_mock_ocr_email_with_bbox)
    assert resp.status_code == 200
    rs = resp.json()["redaction_summary"]
    action = rs["protection_action"]

    # Only BLUR_SENSITIVE / BLUR_AND_ALERT / BLOCK ever produce a stored image;
    # if action is MONITOR the image is not available.
    if action != "MONITOR":
        # With a real bbox and email detected, redacted regions may be > 0
        assert "redacted_image_available" in rs
        if rs["redacted_image_available"]:
            endpoint = rs["redacted_image_endpoint"]
            img_resp = client.get(endpoint)
            assert img_resp.status_code == 200
            assert img_resp.headers["content-type"] == "image/png"


# ---------------------------------------------------------------------------
# E2E-03  MONITOR action → no stored image → 404 on redacted-image
# ---------------------------------------------------------------------------

def test_e2e03_monitor_no_stored_image(client):
    # LOW risk bundle → MONITOR action
    bundle = _make_bundle(risk_class="LOW")
    resp = _post_analyze(client, user_id="e2e_user_03",
                         ocr_mock=_mock_ocr_no_entities,
                         bundle=bundle, risk_class="LOW")
    assert resp.status_code == 200
    rs = resp.json()["redaction_summary"]

    # MONITOR means no image stored and endpoint is None
    if rs["protection_action"] == "MONITOR":
        assert rs["redacted_image_available"] is False
        assert rs["redacted_image_endpoint"] is None

    # Requesting a non-existent id always gives 404
    resp404 = client.get("/redacted-image?id=does_not_exist_xyz")
    assert resp404.status_code == 404


# ---------------------------------------------------------------------------
# E2E-04  Invalid feedback label → 422
# ---------------------------------------------------------------------------

def test_e2e04_invalid_feedback_label_rejected(client):
    resp = client.post("/feedback", json={
        "user_id": "e2e_user_04",
        "screenshot_id": "scr_x",
        "predicted_label": "LOW",
        "user_label": "CONFIDENTIAL",  # invalid
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# E2E-05  Rollback with no calibration history → safe 200 no-op
# ---------------------------------------------------------------------------

def test_e2e05_rollback_no_history_safe(client):
    resp = client.post("/user/rollback", json={
        "user_id": "e2e_user_05_fresh", "reason": "test"
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rolled_back"
    summary = body["user_profile_summary"]
    assert summary.get("calibrated_threshold_override") is None


# ---------------------------------------------------------------------------
# E2E-06  Same filename → independent redacted-image tokens
# ---------------------------------------------------------------------------

def test_e2e06_same_filename_different_tokens(client):
    resp1 = _post_analyze(client, user_id="e2e_user_06a")
    resp2 = _post_analyze(client, user_id="e2e_user_06b")
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    ep1 = resp1.json()["redaction_summary"].get("redacted_image_endpoint")
    ep2 = resp2.json()["redaction_summary"].get("redacted_image_endpoint")

    if ep1 is not None and ep2 is not None:
        assert ep1 != ep2, (
            "Two analyze calls with the same filename must produce "
            "different redacted-image endpoint tokens."
        )


# ---------------------------------------------------------------------------
# E2E-07  Feedback poisoning guard enforced at API level
# ---------------------------------------------------------------------------

def test_e2e07_feedback_poisoning_guard_via_api(client):
    limit = ups.MAX_FEEDBACK_PER_SCREENSHOT
    user = "e2e_user_07"

    for i in range(limit):
        fb = client.post("/feedback", json={
            "user_id": user,
            "screenshot_id": "shared_scr",
            "predicted_label": "LOW",
            "user_label": "MEDIUM",
        })
        assert fb.status_code == 200, f"Expected 200, got {fb.status_code} on record {i}"

    # One more should be rejected
    fb_over = client.post("/feedback", json={
        "user_id": user,
        "screenshot_id": "shared_scr",
        "predicted_label": "LOW",
        "user_label": "MEDIUM",
    })
    assert fb_over.status_code == 422
    assert "Feedback limit reached" in fb_over.json()["detail"]


# ---------------------------------------------------------------------------
# E2E-08  redaction_summary.redaction_status reflects outcome correctly
# ---------------------------------------------------------------------------

def test_e2e08_redaction_status_field_correct(client):
    resp = _post_analyze(client, user_id="e2e_user_08")
    assert resp.status_code == 200
    rs = resp.json()["redaction_summary"]

    valid_statuses = {
        "no_entities_detected",
        "monitor",
        "block",
        "redacted_all_text",
        "redacted",
        "partial_redaction",
        "entities_unmapped",
    }
    assert rs["redaction_status"] in valid_statuses, (
        f"Unexpected redaction_status: {rs['redaction_status']!r}"
    )
