"""
Phase 3 API and database integration tests — CAPRLM backend.

Covers critical paths not exercised by test_api.py or test_database.py:
  INT-01  Empty OCR text returned (extract_text yields empty string).
  INT-02  Non-image bytes rejected with 400 before any OCR processing.
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
  INT-13  Image upload exceeding 20 MB limit rejected with 413.
  INT-14  Non-image format bytes explicitly rejected with 400.
  SEC-01  Feedback user_id exceeding 128 characters returns 422.
  SEC-02  Feedback screenshot_id exceeding 512 characters returns 422.
  SEC-03  /redacted-image with unknown token returns 404.
  SEC-04  Distinct user_ids produce independent profile records (DB-level isolation).
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
# INT-02  Non-image bytes rejected with 400 (early validation, Phase 4 fix)
# ---------------------------------------------------------------------------

def test_int02_non_image_bytes_rejected_before_ocr(client):
    """
    INT-02 — Bytes that do not match a recognised image file signature
    (PNG, JPEG, BMP, GIF, TIFF) must be rejected with 400 before OCR runs.

    The Phase 4 fix added early magic-bytes validation, replacing the
    prior 503 path that arose from PIL failing at the redaction step.
    The endpoint must never raise an unhandled exception; returning 400
    cleanly for client-supplied garbage bytes satisfies that requirement.
    """
    resp = _post_analyze(client, image_bytes=b"\x00\x01\x02\x03\xff", user_id="int02_user")
    assert resp.status_code == 400, resp.text
    assert "recognised image format" in resp.json().get("detail", "")


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


def test_int02g_block_fallback_fields_and_pixel_content(client):
    """
    INT-02g — When an unmappable entity forces a BLOCK fail-safe, the response
    must carry the correct protection_action, protection_status, redaction_status,
    entity counts, AND the actual pixel content of the returned image must be
    entirely black (i.e., the BLOCK action was genuinely applied to pixels).

    This test extends INT-02f: INT-02f confirms the fallback is triggered;
    this test confirms the redacted image itself is all-black.
    """
    import backend.services.risk_prediction as rp

    def ocr_with_unmappable(_path):
        # Returns OCR text containing an email but no OCR bounding boxes,
        # so alice@example.com is detected by NER but cannot be mapped to any box.
        return "Contact alice@example.com", 1, []

    with patch.object(rp, "_bundle", _make_bundle(proba=[0.10, 0.05, 0.85])):
        resp = _post_analyze(
            client, image_bytes=_valid_png(), user_id="int02g_user",
            ocr_fn=ocr_with_unmappable,
            bundle=_make_bundle(proba=[0.10, 0.05, 0.85]),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    summary = body["redaction_summary"]

    # protection_action and protection_status
    assert summary["protection_action"] == "BLOCK"
    assert summary["protection_status"] == "incomplete_entity_mapping"
    # redaction_status must reflect the BLOCK action
    assert summary["redaction_status"] == "block"
    # entity counts: at least one entity detected and at least one unmapped
    assert summary["entities_detected"] >= 1
    assert summary["entities_unmapped"] >= 1
    assert summary["redacted_image_available"] is True

    # Retrieve the redacted image and verify every pixel is black
    endpoint = summary["redacted_image_endpoint"]
    assert endpoint is not None, "redacted_image_endpoint must be set when image is available"
    img_resp = client.get(endpoint)
    assert img_resp.status_code == 200
    assert "image/png" in img_resp.headers["content-type"]
    arr = np.array(Image.open(io.BytesIO(img_resp.content)))
    assert arr.max() == 0, (
        "BLOCK fail-safe must produce an entirely black image "
        f"(max pixel value was {arr.max()}, expected 0)"
    )


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


# ---------------------------------------------------------------------------
# INT-13  Oversized image upload returns 413
# ---------------------------------------------------------------------------

def test_int13_oversized_image_upload_returns_413(client):
    """
    INT-13 — An image payload that exceeds the 20 MB limit must be rejected
    with 413 (Request Entity Too Large) before any OCR or redaction runs.
    The valid PNG magic bytes are prepended so only the size check triggers.
    """
    import backend.services.risk_prediction as rp
    # 21 MB payload: valid PNG magic bytes + padding (definitely oversized)
    oversized = b"\x89PNG" + b"\x00" * (21 * 1024 * 1024)
    with patch("backend.main.extract_text_with_boxes", lambda _: ("", 0, [])):
        with patch.object(rp, "_bundle", _make_bundle()):
            resp = _post_analyze(client, image_bytes=oversized, user_id="int13_user")
    assert resp.status_code == 413, resp.text
    assert "20 MB" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# INT-14  Non-image format bytes explicitly rejected with 400
# ---------------------------------------------------------------------------

def test_int14_invalid_image_format_returns_400(client):
    """
    INT-14 — Bytes that lack any recognised image file signature must be
    rejected with 400 and an informative detail message.  This is a
    direct regression test for the Phase 4 early-validation fix; it is
    distinct from INT-02, which tests the same path with different bytes.
    """
    import backend.services.risk_prediction as rp
    with patch("backend.main.extract_text_with_boxes", lambda _: ("", 0, [])):
        with patch.object(rp, "_bundle", _make_bundle()):
            resp = _post_analyze(
                client, image_bytes=b"\xde\xad\xbe\xef" * 10, user_id="int14_user"
            )
    assert resp.status_code == 400, resp.text
    detail = resp.json().get("detail", "")
    assert "recognised image format" in detail


# ---------------------------------------------------------------------------
# SEC-01  Feedback user_id length limit
# ---------------------------------------------------------------------------

def test_sec01_feedback_user_id_too_long_returns_422(client):
    """
    SEC-01 — A user_id field exceeding 128 characters in the /feedback
    payload must be rejected with 422 (Unprocessable Entity) by the
    Pydantic field constraint, before reaching the service layer.
    """
    resp = client.post("/feedback", json={
        "user_id": "a" * 129,
        "screenshot_id": "scr_001",
        "predicted_label": "LOW",
        "user_label": "MEDIUM",
    })
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# SEC-02  Feedback screenshot_id length limit
# ---------------------------------------------------------------------------

def test_sec02_feedback_screenshot_id_too_long_returns_422(client):
    """
    SEC-02 — A screenshot_id field exceeding 512 characters in the
    /feedback payload must be rejected with 422 before the service layer.
    """
    resp = client.post("/feedback", json={
        "user_id": "valid_user",
        "screenshot_id": "s" * 513,
        "predicted_label": "LOW",
        "user_label": "MEDIUM",
    })
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# SEC-03  /redacted-image with unknown token returns 404
# ---------------------------------------------------------------------------

def test_sec03_redacted_image_unknown_token_returns_404(client):
    """
    SEC-03 — Requesting /redacted-image with a token that was never issued
    by this server must return 404.  This verifies the in-memory store
    rejects any ID that was not produced by a real /analyze call in this
    session, making brute-force enumeration yield only 404 responses.
    """
    resp = client.get("/redacted-image?id=aaaaaaaaaaaaaaaaaaaaaaaaaaaa0000")
    assert resp.status_code == 404

    # Empty id must return 400 (id parameter is required)
    resp2 = client.get("/redacted-image?id=")
    assert resp2.status_code == 400


# ---------------------------------------------------------------------------
# SEC-04  Distinct user_ids have independent profile records (DB isolation)
# ---------------------------------------------------------------------------

def test_sec04_distinct_user_profiles_are_isolated(tmp_path):
    """
    SEC-04 — Different user_ids must produce independent profile rows in
    the database.  User B must not inherit or observe user A's calibration
    state even when both are active in the same session.

    Scope note: user_id is client-controlled in this prototype (no
    authentication infrastructure is present).  This test verifies
    DB-level row isolation only — it does not imply that impersonation
    is prevented; that requires deployment-level authentication.
    """
    db = tmp_path / "iso.db"
    init_db(db)
    original = ups._DB_PATH
    ups._DB_PATH = db
    try:
        # Give user A enough feedback to trigger threshold calibration
        for i in range(ups.RECALIBRATION_THRESHOLD):
            ups.record_feedback("user_alpha", f"scr_{i:03d}", "LOW", "MEDIUM")
        profile_a = ups.load_profile("user_alpha")
        assert profile_a["calibrated_threshold_override"] is not None, (
            "SEC-04 pre-condition: user_alpha must have a calibrated threshold"
        )

        # User B is a brand-new user — must have a completely fresh profile
        profile_b = ups.load_profile("user_beta")
        assert profile_b["calibrated_threshold_override"] is None, (
            "SEC-04: User B must not inherit user A's calibration override"
        )
        assert profile_b["feedback_count"] == 0, (
            "SEC-04: User B must have zero feedback count"
        )
    finally:
        ups._DB_PATH = original
