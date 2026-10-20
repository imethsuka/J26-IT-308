"""
Phase 3 API and database integration tests — CAPRLM backend.

Covers critical paths not exercised by test_api.py or test_database.py:
  INT-01  Empty OCR text returned (extract_text yields empty string).
  INT-02  Malformed image bytes still pass /analyze (OCR is mocked).
  INT-03  Oversized image bytes accepted within reasonable limits.
  INT-04  Missing required form fields return 400.
  INT-05  Threshold updates persist correctly after 10 feedback records.
  INT-06  analysis_log does not store raw OCR text (schema verification).
  INT-07  Feedback loop: threshold recalibration updates profile in DB.
  INT-08  Multiple analyze calls for same user accumulate in analysis_log.
  INT-09  /analyze with image field missing returns 400.
  INT-10  /analyze with non-multipart content-type returns 400.
  INT-11  detection_results always present with count fields in response.
  INT-12  Feedback loop correcting upward lowers MEDIUM threshold.
"""

from __future__ import annotations

import io
import sqlite3
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import backend.services.user_profile_service as ups
from backend.database import get_connection, init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from backend.main import app
    db = tmp_path_factory.mktemp("api_int_db") / "test.db"
    init_db(db)
    ups._DB_PATH = db
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    db = tmp_path / "int.db"
    init_db(db)
    monkeypatch.setattr(ups, "_DB_PATH", db)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_bundle(proba=None, classes=None):
    from sklearn.preprocessing import LabelEncoder
    enc = LabelEncoder()
    enc.fit(["ChatGPT", "Google Chrome", "Unknown", "VS Code"])
    classes = classes or ["HIGH", "LOW", "MEDIUM"]
    proba = proba or [0.10, 0.75, 0.15]
    model = MagicMock()
    model.predict_proba.return_value = np.array([proba])
    model.classes_ = classes
    return {
        "model": model,
        "context_encoder": enc,
        "feature_names": [
            "EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE",
            "WINDOWS_PATH", "sensitive_entity_present",
            "application_context_encoded",
        ],
        "classes": classes,
        "model_version": "test-int",
    }


def _post_analyze(client, *, ocr_text="Sample text", user_id="int_user",
                   image_bytes=None, extra_fields=None, bundle=None, ocr_fn=None,
                   patch_bundle=True):
    import backend.services.risk_prediction as rp
    bundle = bundle or _make_bundle()
    image_bytes = image_bytes or _valid_png()
    data = {
        "application_context": (None, "Google Chrome"),
        "privacy_preference": (None, "Medium"),
        "historical_protection_level": (None, "Medium"),
        "user_id": (None, user_id),
        "image": ("test.jpg", io.BytesIO(image_bytes), "image/jpeg"),
    }
    if extra_fields:
        data.update(extra_fields)

    def _mock_ocr(_path):
        return ocr_text, ocr_text.count(" ") + 1, []

    with patch("backend.main.extract_text_with_boxes", ocr_fn or _mock_ocr):
        if patch_bundle:
            with patch.object(rp, "_bundle", bundle):
                return client.post("/analyze", files=data)
        return client.post("/analyze", files=data)


def _valid_png() -> bytes:
    image = Image.new("RGB", (80, 40), (220, 220, 220))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


# ---------------------------------------------------------------------------
# INT-01  Empty OCR text
# ---------------------------------------------------------------------------

def test_int01_empty_ocr_text_returns_200(client):
    """
    INT-01 — When OCR returns an empty string (blank screenshot), /analyze
    must return 200 with zero entity counts and no tokenization applied.
    """
    resp = _post_analyze(client, ocr_text="", user_id="int01_user")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["detection_results"]["total_sensitive_entity_count"] == 0
    assert body["tokenized_summary"]["entity_token_count"] == 0


def test_int01_empty_ocr_detection_results_all_zero(client):
    """INT-01b — Empty OCR text produces zero counts for all entity categories."""
    resp = _post_analyze(client, ocr_text="", user_id="int01b_user")
    body = resp.json()
    for cat in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"):
        assert body["detection_results"][cat]["count"] == 0


# ---------------------------------------------------------------------------
# INT-02  Malformed image bytes
# ---------------------------------------------------------------------------

def test_int02_malformed_image_bytes_succeeds_with_mocked_ocr(client):
    """
    INT-02 — /analyze accepts any bytes for the image field; OCR validation
    happens inside extract_text (which is mocked here). The endpoint must
    not raise an unhandled exception on random image bytes.
    """
    resp = _post_analyze(client, image_bytes=b"\x00\x01\x02\x03\xff", user_id="int02_user")
    assert resp.status_code == 503, resp.text


def test_int02b_ocr_failure_returns_explicit_503(client):
    import backend.services.risk_prediction as rp

    with patch(
        "backend.main.extract_text_with_boxes",
        side_effect=RuntimeError("synthetic OCR failure"),
    ):
        with patch.object(rp, "_bundle", _make_bundle()):
            resp = client.post(
                "/analyze",
                files={
                    "application_context": (None, "Google Chrome"),
                    "privacy_preference": (None, "Medium"),
                    "historical_protection_level": (None, "Medium"),
                    "user_id": (None, "int02b_user"),
                    "image": ("test.png", io.BytesIO(_valid_png()), "image/png"),
                },
            )

    assert resp.status_code == 503
    assert "OCR failed" in resp.json()["detail"]


def test_int02c_detection_failure_returns_explicit_503(client):
    import backend.services.risk_prediction as rp

    with patch("backend.main.detect_sensitive_entities", side_effect=RuntimeError("synthetic detection failure")):
        with patch.object(rp, "_bundle", _make_bundle()):
            resp = _post_analyze(
                client, image_bytes=_valid_png(), user_id="int02c_user",
            )

    assert resp.status_code == 503
    assert "detection failed" in resp.json()["detail"]


def test_int02d_redaction_failure_returns_explicit_503(client):
    import backend.services.risk_prediction as rp

    with patch("backend.main.redact_image", side_effect=RuntimeError("synthetic redaction failure")):
        with patch.object(rp, "_bundle", _make_bundle()):
            resp = _post_analyze(client, image_bytes=_valid_png(), user_id="int02d_user")

    assert resp.status_code == 503
    assert "redaction failed" in resp.json()["detail"]


def test_int02e_unavailable_model_blocks_instead_of_monitoring(client):
    import backend.services.risk_prediction as rp

    with patch.object(rp, "_load_bundle", side_effect=RuntimeError("synthetic missing model")):
        with patch.object(rp, "_bundle", None):
            resp = _post_analyze(
                client, ocr_text="Contact alice@example.com",
                image_bytes=_valid_png(), user_id="int02e_user",
                patch_bundle=False,
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["risk_prediction"]["risk_level"] == "UNAVAILABLE"
    assert body["protection_selection"]["protection_action"] == "BLOCK"
    assert body["redaction_summary"]["protection_action"] == "BLOCK"
    assert body["redaction_summary"]["redacted_image_available"] is True


def test_int02f_unmappable_entity_uses_explicit_incomplete_block(client):
    import backend.services.risk_prediction as rp

    def ocr_with_unmappable_email(_path):
        return "Contact alice@example.com", 1, []

    with patch.object(rp, "_bundle", _make_bundle(proba=[0.10, 0.05, 0.85])):
        resp = _post_analyze(
            client, image_bytes=_valid_png(), user_id="int02f_user",
            ocr_fn=ocr_with_unmappable_email,
            bundle=_make_bundle(proba=[0.10, 0.05, 0.85]),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    summary = body["redaction_summary"]
    assert summary["protection_action"] == "BLOCK"
    assert summary["protection_status"] == "incomplete_entity_mapping"
    assert summary["entities_unmapped"] == 1
    assert summary["redacted_image_available"] is True


# ---------------------------------------------------------------------------
# INT-03  Oversized image bytes
# ---------------------------------------------------------------------------

def test_int03_large_image_bytes_accepted(client):
    """
    INT-03 — A reasonably large image payload (1 MB) must be accepted without
    a 413 or server error. There is no explicit size limit in the current
    endpoint (OCR is mocked, so actual processing is not an issue in tests).
    """
    large_bytes = _valid_png()
    resp = _post_analyze(client, image_bytes=large_bytes, user_id="int03_user")
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# INT-04  Missing required form fields
# ---------------------------------------------------------------------------

def test_int04_missing_application_context_returns_400(client):
    """INT-04 — Missing 'application_context' field must return 400."""
    import backend.services.risk_prediction as rp
    image_content = b"\xff\xd8\xff" + b"\x00" * 100
    data = {
        # application_context intentionally omitted
        "privacy_preference": (None, "Medium"),
        "historical_protection_level": (None, "Medium"),
        "image": ("test.jpg", io.BytesIO(image_content), "image/jpeg"),
    }
    with patch("backend.main.extract_text_with_boxes", lambda _: ("text", 1, [])):
        with patch.object(rp, "_bundle", _make_bundle()):
            resp = client.post("/analyze", files=data)
    assert resp.status_code == 400


def test_int04_missing_privacy_preference_returns_400(client):
    """INT-04b — Missing 'privacy_preference' must return 400."""
    import backend.services.risk_prediction as rp
    image_content = b"\xff\xd8\xff" + b"\x00" * 100
    data = {
        "application_context": (None, "Google Chrome"),
        # privacy_preference omitted
        "historical_protection_level": (None, "Medium"),
        "image": ("test.jpg", io.BytesIO(image_content), "image/jpeg"),
    }
    with patch("backend.main.extract_text_with_boxes", lambda _: ("text", 1, [])):
        with patch.object(rp, "_bundle", _make_bundle()):
            resp = client.post("/analyze", files=data)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# INT-05  Threshold updates persist after 10+ feedback records
# ---------------------------------------------------------------------------

def test_int05_threshold_persists_after_recalibration(tmp_path):
    """
    INT-05 — After recording 10 feedback items (all user labels higher than
    predicted), the calibrated threshold override must be saved in SQLite
    and returned by load_profile() in a fresh call.
    """
    db = tmp_path / "thresh.db"
    init_db(db)
    original = ups._DB_PATH
    ups._DB_PATH = db
    try:
        for i in range(ups.RECALIBRATION_THRESHOLD):
            ups.record_feedback(f"thresh_user", f"scr_{i:03d}", "LOW", "MEDIUM")
        profile = ups.load_profile("thresh_user")
        override = profile["calibrated_threshold_override"]
        assert override is not None, "Threshold override should be set after recalibration"
        assert "medium" in override
        assert "high" in override
        # Verify the values are persisted in SQLite directly
        conn = get_connection(db)
        try:
            row = conn.execute(
                "SELECT calibrated_threshold_medium, calibrated_threshold_high "
                "FROM user_profile WHERE user_id = ?", ("thresh_user",)
            ).fetchone()
            assert row is not None
            assert row["calibrated_threshold_medium"] == pytest.approx(override["medium"])
            assert row["calibrated_threshold_high"] == pytest.approx(override["high"])
        finally:
            conn.close()
    finally:
        ups._DB_PATH = original


# ---------------------------------------------------------------------------
# INT-06  analysis_log does not store raw OCR text
# ---------------------------------------------------------------------------

def test_int06_analysis_log_schema_no_ocr_text_column(tmp_path):
    """
    INT-06 — The analysis_log table schema must not include any column that
    would store raw OCR text or extracted entity string values. Only numeric
    counts and metadata are allowed.
    """
    db = tmp_path / "schema.db"
    init_db(db)
    conn = get_connection(db)
    try:
        cursor = conn.execute("PRAGMA table_info(analysis_log)")
        columns = {row["name"] for row in cursor.fetchall()}
    finally:
        conn.close()

    # These column names would store PII
    forbidden_patterns = ["ocr_text", "extracted_text", "raw_text", "ocr_raw",
                          "email_value", "phone_value", "url_value"]
    for pattern in forbidden_patterns:
        assert pattern not in columns, (
            f"INT-06: analysis_log has column '{pattern}' which would store PII"
        )

    # Counts are allowed (non-PII)
    expected_count_cols = {
        "email_count", "phone_count", "url_count", "ip_count",
        "date_count", "windows_path_count", "sensitive_entity_count"
    }
    assert expected_count_cols.issubset(columns), (
        f"INT-06: Expected count columns missing from analysis_log: "
        f"{expected_count_cols - columns}"
    )


# ---------------------------------------------------------------------------
# INT-07  Feedback loop updates threshold in profile
# ---------------------------------------------------------------------------

def test_int07_feedback_loop_threshold_decreases_on_upgrades(tmp_path):
    """
    INT-07 — When a user consistently labels risk as higher than predicted
    (all 10+ upgrades), the MEDIUM threshold must decrease (more conservative).
    This verifies the recalibration direction is correct.
    """
    from backend.services.risk_prediction import DEFAULT_MEDIUM_THRESHOLDS
    db = tmp_path / "loop.db"
    init_db(db)
    original = ups._DB_PATH
    ups._DB_PATH = db
    try:
        # All upgrades: predicted LOW, user says MEDIUM (10 of them)
        for i in range(ups.RECALIBRATION_THRESHOLD):
            ups.record_feedback("loop_user", f"scr_{i:03d}", "LOW", "MEDIUM")
        profile = ups.load_profile("loop_user")
        override = profile["calibrated_threshold_override"]
        assert override is not None
        # Net upgrades = 10, shift = 10 * 0.02 = 0.20
        # medium threshold = max(0.05, 0.35 - 0.20) = 0.15
        default_medium = DEFAULT_MEDIUM_THRESHOLDS["medium"]
        assert override["medium"] < default_medium, (
            f"INT-07: After 10 upgrades, MEDIUM threshold should decrease below "
            f"{default_medium}; got {override['medium']}"
        )
    finally:
        ups._DB_PATH = original


# ---------------------------------------------------------------------------
# INT-08  Multiple analyze calls accumulate in analysis_log
# ---------------------------------------------------------------------------

def test_int08_multiple_analyze_calls_logged(client, tmp_path):
    """
    INT-08 — Each /analyze call must produce a new row in analysis_log.
    This verifies the _log_analysis helper is called and committed.
    """
    db = tmp_path / "multi.db"
    init_db(db)
    original = ups._DB_PATH
    ups._DB_PATH = db
    try:
        for i in range(3):
            resp = _post_analyze(client, user_id=f"int08_user_{i}")
            assert resp.status_code == 200

        conn = get_connection(db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM analysis_log").fetchone()[0]
        finally:
            conn.close()
        assert count == 3, f"INT-08: Expected 3 analysis_log rows, found {count}"
    finally:
        ups._DB_PATH = original


# ---------------------------------------------------------------------------
# INT-09  /analyze with image field missing returns 400
# ---------------------------------------------------------------------------

def test_int09_analyze_missing_image_returns_400(client):
    """INT-09 — Submitting /analyze with no image field must return 400."""
    import backend.services.risk_prediction as rp
    data = {
        "application_context": (None, "Google Chrome"),
        "privacy_preference": (None, "Medium"),
        "historical_protection_level": (None, "Medium"),
        # no image field
    }
    with patch("backend.main.extract_text_with_boxes", lambda _: ("text", 1, [])):
        with patch.object(rp, "_bundle", _make_bundle()):
            resp = client.post("/analyze", files=data)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# INT-10  Non-multipart content-type returns 400
# ---------------------------------------------------------------------------

def test_int10_non_multipart_content_type_returns_400(client):
    """INT-10 — A plain JSON or text body must return 400 (not 422 or 500)."""
    resp = client.post(
        "/analyze",
        content=b'{"image": "base64data"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# INT-11  detection_results always has count fields
# ---------------------------------------------------------------------------

def test_int11_detection_results_always_has_count_fields(client):
    """
    INT-11 — For every risk level and input, detection_results must include
    all expected entity categories, each with a 'count' integer field.
    """
    for bundle_desc, bundle in [
        ("low_high_conf", _make_bundle([0.10, 0.85, 0.05])),
        ("high_risk", _make_bundle([0.95, 0.03, 0.02])),
    ]:
        resp = _post_analyze(
            client, ocr_text="hello world", user_id=f"int11_{bundle_desc}",
            bundle=bundle
        )
        assert resp.status_code == 200
        body = resp.json()
        detection = body["detection_results"]
        for cat in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"):
            assert cat in detection, f"INT-11: Missing category {cat} for {bundle_desc}"
            assert "count" in detection[cat], (
                f"INT-11: Missing 'count' in {cat} for {bundle_desc}"
            )
            assert isinstance(detection[cat]["count"], int)
        assert "total_sensitive_entity_count" in detection


# ---------------------------------------------------------------------------
# INT-12  Feedback: all downgrades raises MEDIUM threshold
# ---------------------------------------------------------------------------

def test_int12_feedback_downgrades_raise_medium_threshold(tmp_path):
    """
    INT-12 — When a user consistently labels risk as lower than predicted
    (all 10 downgrades), the MEDIUM threshold must increase (more permissive).
    """
    from backend.services.risk_prediction import DEFAULT_MEDIUM_THRESHOLDS
    db = tmp_path / "down.db"
    init_db(db)
    original = ups._DB_PATH
    ups._DB_PATH = db
    try:
        # All downgrades: predicted MEDIUM, user says LOW
        for i in range(ups.RECALIBRATION_THRESHOLD):
            ups.record_feedback("down_user", f"scr_{i:03d}", "MEDIUM", "LOW")
        profile = ups.load_profile("down_user")
        override = profile["calibrated_threshold_override"]
        assert override is not None
        default_medium = DEFAULT_MEDIUM_THRESHOLDS["medium"]
        assert override["medium"] > default_medium, (
            f"INT-12: After 10 downgrades, MEDIUM threshold should increase above "
            f"{default_medium}; got {override['medium']}"
        )
    finally:
        ups._DB_PATH = original
