import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend.database import get_connection, init_db
from backend.services.feature_extraction import extract_features
from backend.services.ocr_service import extract_text_with_boxes
from backend.services.protection import select_protection
from backend.services.risk_prediction import predict_privacy_risk, reload_bundle, get_bundle
from backend.services.rule_baseline import predict_rule_based
from backend.services.sensitive_detection import detect_sensitive_entities
from backend.services.tokenizer import tokenize as _tokenize_entities
from backend.services.ner_service import detect_entities_with_scores, high_confidence_entities
from backend.services.cv_features import extract_visual_features
from backend.services.redaction import (
    redact_image,
    image_to_png_bytes,
    load_image,
    compute_redaction_status,
)
from backend.services import user_profile_service as ups


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db(ups._DB_PATH, ups._PROFILES_DIR)
    yield


# In-process store for redacted images keyed by original filename.
# Max 50 entries to bound memory usage.
_REDACTED_STORE: dict[str, bytes] = {}
_REDACTED_STORE_MAX = 50


def _get_redacted_store() -> dict[str, bytes]:
    if len(_REDACTED_STORE) >= _REDACTED_STORE_MAX:
        oldest_key = next(iter(_REDACTED_STORE))
        del _REDACTED_STORE[oldest_key]
    return _REDACTED_STORE


app = FastAPI(title="J26-IT-308 CAPRLM Backend", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Serve the frontend directory for the demo page (same-origin — no CORS needed).
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/frontend", StaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")

_TRAINING_LOG_PATH = Path(__file__).parent / "data" / "training_log.json"

# ---------------------------------------------------------------------------
# Allowed values (validated at the boundary)
# ---------------------------------------------------------------------------
_VALID_RISK_LABELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
_VALID_PREFERENCE_VALUES = frozenset({"Low", "Medium", "High"})

# /train is restricted to local connections only
_TRAIN_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Maximum image upload size (20 MB).  Requests exceeding this limit are
# rejected with 413 before any OCR or redaction processing.
_MAX_IMAGE_BYTES: int = 20 * 1024 * 1024

# Recognised image file signatures (magic bytes).  Only files whose leading
# bytes match one of these patterns are accepted; everything else is rejected
# with 400 before the image is written to a temp file or passed to OCR.
_VALID_IMAGE_MAGIC: tuple[bytes, ...] = (
    b"\x89PNG",      # PNG
    b"\xff\xd8\xff", # JPEG / JFIF / Exif
    b"GIF8",         # GIF
    b"BM",           # BMP
    b"II*\x00",      # TIFF little-endian
    b"MM\x00*",      # TIFF big-endian
)


def _is_valid_image_bytes(data: bytes) -> bool:
    """Return True if *data* begins with a recognised image file signature."""
    if not data:
        return False
    return any(data[: len(magic)] == magic for magic in _VALID_IMAGE_MAGIC)


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "project": "J26-IT-308",
        "component": "CAPRLM",
        "status": "Backend is running",
    }


# ---------------------------------------------------------------------------
# Multipart upload parsing
# ---------------------------------------------------------------------------

async def _parse_analyze_request(request: Request) -> tuple[bytes, str, str, str, str, str, str]:
    content_type = request.headers.get("content-type", "")
    boundary_match = re.search(
        r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type, re.IGNORECASE
    )
    if "multipart/form-data" not in content_type or boundary_match is None:
        raise HTTPException(status_code=400, detail="Expected a multipart form upload")

    boundary = (boundary_match.group(1) or boundary_match.group(2)).encode("latin-1")
    parts = (await request.body()).split(b"--" + boundary)
    fields: dict[str, str] = {}
    image_bytes: bytes | None = None
    image_suffix = ".jpg"

    for part in parts[1:]:
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.startswith(b"--"):
            continue
        if part.endswith(b"\r\n"):
            part = part[:-2]
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue

        headers = part[:header_end].decode("latin-1")
        value = part[header_end + 4:]
        disposition_match = re.search(
            r'name="([^"]+)"(?:;\s*filename="([^"]*)")?',
            headers,
            re.IGNORECASE,
        )
        if disposition_match is None:
            continue

        field_name = disposition_match.group(1)
        filename = disposition_match.group(2)
        if filename is not None:
            image_bytes = value
            image_suffix = Path(filename).suffix or ".jpg"
            image_filename = filename
        else:
            fields[field_name] = value.decode("utf-8")

    if image_bytes is None:
        raise HTTPException(status_code=400, detail="Missing image file upload")

    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Image file exceeds the maximum allowed size of "
                f"{_MAX_IMAGE_BYTES // (1024 * 1024)} MB."
            ),
        )

    if not _is_valid_image_bytes(image_bytes):
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a recognised image format (PNG, JPEG, BMP, GIF, or TIFF required).",
        )

    required_fields = (
        "application_context",
        "privacy_preference",
        "historical_protection_level",
    )
    missing_fields = [f for f in required_fields if f not in fields]
    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Missing form fields: {', '.join(missing_fields)}",
        )

    # user_id is optional; default to "default" if not supplied
    user_id = fields.get("user_id", "default")

    return (
        image_bytes,
        image_suffix,
        image_filename,
        fields["application_context"],
        fields["privacy_preference"],
        fields["historical_protection_level"],
        user_id,
    )


# ---------------------------------------------------------------------------
# DB logging helpers
# ---------------------------------------------------------------------------

def _log_analysis(
    *,
    screenshot_id: str,
    user_id: str,
    application_context: str,
    privacy_preference: str,
    historical_protection_level: str,
    extracted_features: dict[str, Any],
    risk_prediction: dict[str, Any],
    rule_prediction: dict[str, Any],
    protection_selection: dict[str, Any],
) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        conn = get_connection(ups._DB_PATH)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO analysis_log (
                        screenshot_id, analyzed_at, user_id,
                        application_context, privacy_preference,
                        historical_protection_level,
                        ocr_text_length,
                        email_count, phone_count, url_count, ip_count,
                        date_count, windows_path_count,
                        sensitive_entity_count,
                        risk_level, confidence, model_type, protection_action,
                        rule_risk_level, rule_confidence
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        screenshot_id, now, user_id,
                        application_context, privacy_preference,
                        historical_protection_level,
                        extracted_features.get("ocr_text_length"),
                        int(extracted_features.get("email_count", 0)),
                        int(extracted_features.get("phone_count", 0)),
                        int(extracted_features.get("url_count", 0)),
                        int(extracted_features.get("ip_count", 0)),
                        int(extracted_features.get("date_count", 0)),
                        int(extracted_features.get("windows_path_count", 0)),
                        int(extracted_features.get("sensitive_entity_count", 0)),
                        risk_prediction.get("risk_level"),
                        risk_prediction.get("confidence"),
                        risk_prediction.get("model_type"),
                        protection_selection.get("action"),
                        rule_prediction.get("risk_level"),
                        rule_prediction.get("confidence"),
                    ),
                )
        finally:
            conn.close()
    except Exception:
        pass  # logging must never fail an analysis request




# ---------------------------------------------------------------------------
# /analyze — main CAPRLM pipeline
# ---------------------------------------------------------------------------

@app.post("/analyze")
async def analyze(request: Request):
    (
        image_bytes,
        image_suffix,
        image_filename,
        application_context,
        privacy_preference,
        historical_protection_level,
        user_id,
    ) = await _parse_analyze_request(request)

    temporary_file = NamedTemporaryFile(suffix=image_suffix, delete=False)
    temporary_path = Path(temporary_file.name)
    try:
        temporary_file.write(image_bytes)
        temporary_file.close()

        try:
            extracted_text, detections_count, ocr_detections = extract_text_with_boxes(
                temporary_path
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Privacy processing could not complete because OCR failed.",
            ) from exc

        try:
            detection_output = detect_sensitive_entities(extracted_text)

            # Contextual NER — confidence-scored entity detection
            ner_output = detect_entities_with_scores(extracted_text)

            extracted_features = extract_features(
                extracted_text, detection_output, application_context
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Privacy processing could not complete because sensitive-entity detection failed.",
            ) from exc

        # Load user profile for personalised threshold
        profile = ups.load_profile(user_id)
        calibrated_override = profile.get("calibrated_threshold_override")

        # ML prediction
        risk_prediction = predict_privacy_risk(
            extracted_features,
            application_context,
            privacy_preference,
            historical_protection_level,
            calibrated_threshold_override=calibrated_override,
        )

        # Rule-based baseline (for comparison; not the primary model)
        rule_prediction = predict_rule_based(extracted_features)

        protection_selection = select_protection(
            risk_prediction["risk_level"],
            risk_prediction["confidence"],
            detection_output,
        )

        # T5 — Risk-adaptive reversible tokenization.
        # Replaces detected entity values in the API response with tokens.
        # token_map is ephemeral: it is intentionally not returned to the
        # client, not logged, and not stored.  See services/tokenizer.py.
        tokenized_text, token_map, tokenized_detection = _tokenize_entities(
            extracted_text,
            detection_output,
            risk_level=risk_prediction.get("risk_level", "LOW"),
            confidence=risk_prediction.get("confidence"),
        )

        # Visual features — spatial layout analysis from OCR bounding boxes
        from PIL import Image as _PIL_Image
        try:
            with _PIL_Image.open(temporary_path) as _pil_img:
                _pil_img.load()
                img_w, img_h = _pil_img.size
        except Exception:
            img_w, img_h = 0, 0

        entity_vals = [
            v for cat, info in detection_output.items()
            if cat != "total_sensitive_entity_count" and isinstance(info, dict)
            for v in info.get("matches", [])
        ]
        visual_features = extract_visual_features(img_w, img_h, ocr_detections, entity_vals)

        # Pixel-level redaction
        protection_action = protection_selection.get("protection_action", "MONITOR")
        redacted_png_bytes: bytes | None = None
        redacted_regions: list[dict] = []
        redaction_stats: dict = {}
        try:
            from PIL import Image as _PIL_Image2
            with _PIL_Image2.open(temporary_path) as _src_img:
                _src_img.load()
                _redacted_img, redacted_regions, redaction_stats = redact_image(
                    _src_img, ocr_detections, detection_output, protection_action
                )
                if redacted_regions:
                    redacted_png_bytes = image_to_png_bytes(_redacted_img)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Privacy processing could not complete because image redaction failed.",
            ) from exc

        protection_status = "completed"
        if (
            protection_action != "MONITOR"
            and redaction_stats.get("entities_unmapped", 0) > 0
        ):
            # A partial blur is not a safe result. Replace it with an opaque
            # block so detected entities cannot remain visible.
            incomplete_stats = dict(redaction_stats)
            try:
                from PIL import Image as _PIL_Image3
                with _PIL_Image3.open(temporary_path) as _src_img:
                    _src_img.load()
                    _blocked_img, redacted_regions, blocked_stats = redact_image(
                        _src_img, ocr_detections, detection_output, "BLOCK"
                    )
                    redacted_png_bytes = image_to_png_bytes(_blocked_img)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Privacy processing could not complete after incomplete entity mapping.",
                ) from exc

            protection_action = "BLOCK"
            protection_status = "incomplete_entity_mapping"
            protection_selection = {
                **protection_selection,
                "protection_action": "BLOCK",
                "description": (
                    "Detected entities could not all be mapped to OCR regions; "
                    "the image was blocked as a fail-safe."
                ),
                "reversible": False,
                "severity": 3,
                "fallback_reason": "unmapped_sensitive_entities",
            }
            redaction_stats = {
                **blocked_stats,
                "entities_detected": incomplete_stats.get("entities_detected", 0),
                "entities_mapped": incomplete_stats.get("entities_mapped", 0),
                "entities_unmapped": incomplete_stats.get("entities_unmapped", 0),
                "unmapped_entity_categories": incomplete_stats.get(
                    "unmapped_entity_categories", []
                ),
                "fallback_action": "BLOCK",
            }

        # Store redacted image keyed by a random token, NOT the user-supplied
        # filename.  Using the filename as a key allows any caller who knows
        # (or guesses) another user's filename to retrieve their redacted image.
        _redacted_store: dict = _get_redacted_store()
        _redact_key = secrets.token_hex(16)
        if redacted_png_bytes is not None:
            _redacted_store[_redact_key] = redacted_png_bytes

        # DB logging uses the original detection_output (original counts are
        # correct; we must not store tokenized values or the token_map).
        _log_analysis(
            screenshot_id=image_filename,
            user_id=user_id,
            application_context=application_context,
            privacy_preference=privacy_preference,
            historical_protection_level=historical_protection_level,
            extracted_features=extracted_features,
            risk_prediction=risk_prediction,
            rule_prediction=rule_prediction,
            protection_selection=protection_selection,
        )

        return {
            "ocr_summary": {
                "extracted_text": "[redacted — not logged for privacy]",
                "detections_count": detections_count,
            },
            "detection_results": tokenized_detection,
            "ner_summary": {
                cat: {
                    "count": info.get("count", 0),
                    "scored_matches": [
                        {"confidence": sm["confidence"], "context_trigger": sm["context_trigger"]}
                        for sm in info.get("scored_matches", [])
                    ],
                }
                for cat, info in ner_output.items()
                if cat not in ("total_sensitive_entity_count", "ner_method")
                and isinstance(info, dict)
            },
            "extracted_features": extracted_features,
            "visual_features": visual_features,
            "risk_prediction": risk_prediction,
            "rule_baseline": rule_prediction,
            "protection_selection": protection_selection,
            "user_profile_summary": ups.profile_summary(profile),
            "tokenized_summary": {
                "tokenized_text": (
                    tokenized_text
                    if token_map
                    else "[redacted — entity tokenization not applied]"
                ),
                "entity_token_count": len(token_map),
                "tokenization_applied": len(token_map) > 0,
                "note": (
                    "Token-to-value restoration map is ephemeral and not "
                    "returned. Restoration across requests is not supported."
                ),
            },
            "redaction_summary": {
                "protection_action":          protection_action,
                "protection_status":          protection_status,
                "regions_redacted":           len(redacted_regions),
                "redacted_image_available":   redacted_png_bytes is not None,
                "redacted_image_endpoint": (
                    f"/redacted-image?id={_redact_key}"
                    if redacted_png_bytes is not None
                    else None
                ),
                "entities_detected":          redaction_stats.get("entities_detected", 0),
                "entities_mapped":            redaction_stats.get("entities_mapped", 0),
                "entities_unmapped":          redaction_stats.get("entities_unmapped", 0),
                "fragmented_matches":         redaction_stats.get("fragmented_matches", 0),
                "unmapped_entity_categories": redaction_stats.get("unmapped_entity_categories", []),
                "redaction_status": compute_redaction_status(
                    protection_action,
                    redaction_stats.get("entities_detected", 0),
                    redaction_stats.get("entities_mapped", 0),
                    redaction_stats.get("entities_unmapped", 0),
                ),
                "note": (
                    "Retrieve the pixel-redacted image via the endpoint above. "
                    "The original screenshot is not stored or returned."
                ) if redacted_png_bytes is not None else (
                    "No pixel redaction applied."
                    if protection_action == "MONITOR"
                    else f"No redactable regions identified for action {protection_action}."
                ),
            },
        }
    finally:
        temporary_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# /feedback — record user correction and recalibrate threshold
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    user_id: str = Field(max_length=128)
    screenshot_id: str = Field(max_length=512)
    predicted_label: str
    user_label: str

    @field_validator("user_label", "predicted_label")
    @classmethod
    def _validate_label(cls, v: str) -> str:
        normalised = v.strip().upper()
        if normalised not in _VALID_RISK_LABELS:
            raise ValueError(
                f"Label must be one of {sorted(_VALID_RISK_LABELS)}, got '{v}'."
            )
        return normalised

    @field_validator("user_id", "screenshot_id")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty.")
        return v.strip()


@app.post("/feedback")
async def feedback(payload: FeedbackRequest):
    """
    Record a user's correction of the model's risk prediction.
    After RECALIBRATION_THRESHOLD corrections from this user, per-user
    decision thresholds are automatically recalibrated.
    """
    try:
        updated_profile = ups.record_feedback(
            user_id=payload.user_id,
            screenshot_id=payload.screenshot_id,
            predicted_label=payload.predicted_label,
            user_label=payload.user_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "status": "recorded",
        "user_profile_summary": ups.profile_summary(updated_profile),
    }


# ---------------------------------------------------------------------------
# /train — trigger offline training (local connections only)
# ---------------------------------------------------------------------------

@app.post("/train")
async def train(request: Request):
    """
    Trigger model retraining from the annotation CSV.
    Restricted to local connections to prevent unauthorised retraining.
    """
    client_host = request.client.host if request.client else "unknown"
    if client_host not in _TRAIN_ALLOWED_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="Training endpoint is restricted to local connections.",
        )

    try:
        from backend.services.train_model import train as run_training
        bundle = run_training(db_path=ups._DB_PATH)
        reload_bundle()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Training failed. Check server logs for details.",
        ) from exc

    return {
        "status": "trained",
        "model_version": bundle.get("model_version"),
        "train_size": bundle.get("train_size"),
        "test_size": bundle.get("test_size"),
        "cv_folds": bundle.get("cv_folds"),
        "cv_macro_f1_mean": bundle.get("cv_macro_f1_mean"),
        "cv_macro_f1_std": bundle.get("cv_macro_f1_std"),
        "test_macro_f1": bundle.get("test_macro_f1"),
        "classes": bundle.get("classes"),
        "test_classification_report": bundle.get("test_classification_report"),
        "test_confusion_matrix": bundle.get("test_confusion_matrix"),
        "confusion_matrix_labels": bundle.get("confusion_matrix_labels"),
    }


# ---------------------------------------------------------------------------
# /model-info — model metadata (no retrain; safe for any caller)
# ---------------------------------------------------------------------------

@app.get("/redacted-image")
async def get_redacted_image(id: str):
    """
    Return the pixel-redacted PNG produced by the most recent /analyze call
    for the given screenshot filename.

    The redacted image is held in process memory; it is not persisted to disk.
    Sensitive source images are never stored by this endpoint.
    """
    if not id or not id.strip():
        raise HTTPException(status_code=400, detail="id parameter is required.")
    png = _REDACTED_STORE.get(id.strip())
    if png is None:
        raise HTTPException(
            status_code=404,
            detail="No redacted image found for this id. Run /analyze first.",
        )
    return Response(content=png, media_type="image/png")


@app.get("/user/calibration-history")
async def calibration_history(user_id: str, limit: int = 10):
    """Return adaptation log entries for a user (newest first)."""
    if not user_id or not user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required.")
    try:
        history = ups.get_adaptation_history(user_id.strip(), limit=min(limit, 100))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"user_id": user_id.strip(), "adaptation_history": history}


class RollbackRequest(BaseModel):
    user_id: str = Field(max_length=128)
    reason: str = "manual_rollback"

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("user_id must not be empty.")
        return v.strip()


@app.post("/user/rollback")
async def rollback_calibration(payload: RollbackRequest):
    """
    Roll back a user's threshold calibration to the previous safe state.
    Writes a rollback event to adaptation_log for reproducibility.
    """
    try:
        updated = ups.rollback_calibration(payload.user_id, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "rolled_back",
        "user_profile_summary": ups.profile_summary(updated),
    }


@app.get("/demo")
async def demo_redirect():
    """Convenience redirect — opens the presentation demo page."""
    return RedirectResponse(url="/frontend/demo.html")


# ---------------------------------------------------------------------------
# /demo-predict — text-only prediction (no OCR) for live demonstrations
# ---------------------------------------------------------------------------

class DemoPredictRequest(BaseModel):
    text: str
    application_context: str
    privacy_preference: str
    historical_protection_level: str

    @field_validator("privacy_preference", "historical_protection_level")
    @classmethod
    def _validate_preference(cls, v: str) -> str:
        normalised = v.strip().capitalize()
        if normalised not in _VALID_PREFERENCE_VALUES:
            raise ValueError(
                f"Value must be one of {sorted(_VALID_PREFERENCE_VALUES)}, got '{v}'."
            )
        return normalised

    @field_validator("application_context", "text")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty.")
        return v.strip()


@app.post("/demo-predict")
async def demo_predict(payload: DemoPredictRequest):
    """
    Text-only prediction for live demonstrations.
    Runs entity detection, feature extraction, and ML prediction
    without any image upload or OCR step.
    Not for production use — no DB logging, no tokenization, no redaction.
    """
    detection_output = detect_sensitive_entities(payload.text)
    extracted_features = extract_features(
        payload.text, detection_output, payload.application_context
    )
    risk_prediction = predict_privacy_risk(
        extracted_features,
        payload.application_context,
        payload.privacy_preference,
        payload.historical_protection_level,
    )
    rule_prediction = predict_rule_based(extracted_features)

    return {
        "detection_results": detection_output,
        "extracted_features": extracted_features,
        "risk_prediction": risk_prediction,
        "rule_baseline": rule_prediction,
    }


@app.get("/model-info")
async def model_info():
    """Return current model metadata and last training results from SQLite."""
    from backend.services.train_model import MODEL_PATH

    if not MODEL_PATH.exists():
        return {
            "model_available": False,
            "message": "No trained model found. POST /train to train the model.",
        }

    last_entry: dict | None = None
    try:
        conn = get_connection(ups._DB_PATH)
        try:
            row = conn.execute(
                """
                SELECT model_version, trained_at, dataset_rows,
                       label_low, label_medium, label_high, label_critical,
                       train_size, test_size, cv_folds,
                       cv_macro_f1_mean, cv_macro_f1_std,
                       test_macro_f1, test_accuracy
                FROM training_run
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if row:
                last_entry = dict(row)
        finally:
            conn.close()
    except Exception:
        pass

    return {
        "model_available": True,
        "model_path": str(MODEL_PATH),
        "last_training": last_entry,
    }


# ---------------------------------------------------------------------------
# /insights/* — Model Insights dashboard endpoints (read-only, no retraining)
# ---------------------------------------------------------------------------

@app.get("/insights/model-overview")
async def insights_model_overview():
    """Model metadata for the insights dashboard."""
    try:
        bundle = get_bundle()
    except RuntimeError:
        return {"model_available": False}

    model = bundle["model"]
    feature_names: list[str] = bundle.get("feature_names", [])
    classes: list[str] = bundle.get("classes", [])

    return {
        "model_available": True,
        "algorithm": "RandomForestClassifier",
        "n_estimators": getattr(model, "n_estimators", None),
        "n_features": len(feature_names),
        "feature_names": feature_names,
        "class_labels": classes,
        "model_version": bundle.get("model_version"),
        "random_state": bundle.get("random_state"),
        "train_size": bundle.get("train_size"),
        "test_size": bundle.get("test_size"),
        "cv_folds": bundle.get("cv_folds"),
        "cv_macro_f1_mean": bundle.get("cv_macro_f1_mean"),
        "cv_macro_f1_std": bundle.get("cv_macro_f1_std"),
        "test_macro_f1": bundle.get("test_macro_f1"),
        "test_accuracy": bundle.get("test_accuracy"),
        "model_artifact": "backend/data/caprlm_model.joblib",
    }


@app.get("/insights/feature-importance")
async def insights_feature_importance():
    """Feature importances from the actual trained Random Forest model."""
    try:
        bundle = get_bundle()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Model not available.")

    model = bundle["model"]
    feature_names: list[str] = bundle.get("feature_names", [])

    if not hasattr(model, "feature_importances_"):
        raise HTTPException(
            status_code=503,
            detail="Model artifact does not expose feature_importances_.",
        )

    importances = model.feature_importances_.tolist()
    paired = sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True)

    return {
        "feature_importances": [
            {"feature": name, "importance": round(imp, 6)}
            for name, imp in paired
        ],
        "note": (
            "Feature importance (mean decrease in impurity) indicates the model's "
            "relative reliance on each feature. It does not prove causality."
        ),
    }


@app.get("/insights/evaluation")
async def insights_evaluation():
    """Evaluation metrics from the most recent training run in training_log.json."""
    if not _TRAINING_LOG_PATH.exists():
        return {"available": False, "message": "No training log found."}

    try:
        with open(_TRAINING_LOG_PATH, "r", encoding="utf-8") as f:
            log: list[dict] = json.load(f)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not read training log: {exc}"
        ) from exc

    if not log:
        return {"available": False, "message": "Training log is empty."}

    latest = log[-1]
    report = latest.get("test_classification_report", {})

    return {
        "available": True,
        "evaluation_split": "held-out test set (20% stratified)",
        "model_version": latest.get("model_version"),
        "trained_at": latest.get("trained_at"),
        "dataset_rows": latest.get("dataset_rows"),
        "label_distribution": latest.get("label_distribution"),
        "train_size": latest.get("train_size"),
        "test_size": latest.get("test_size"),
        "cv_folds": latest.get("cv_folds"),
        "cv_macro_f1_mean": latest.get("cv_macro_f1_mean"),
        "cv_macro_f1_std": latest.get("cv_macro_f1_std"),
        "test_macro_f1": latest.get("test_macro_f1"),
        "test_accuracy": report.get("accuracy"),
        "macro_precision": report.get("macro avg", {}).get("precision"),
        "macro_recall": report.get("macro avg", {}).get("recall"),
        "macro_f1": report.get("macro avg", {}).get("f1-score"),
        "per_class_metrics": {
            cls: report[cls]
            for cls in report
            if cls not in ("accuracy", "macro avg", "weighted avg")
        },
        "confusion_matrix": latest.get("test_confusion_matrix"),
        "confusion_matrix_labels": latest.get("confusion_matrix_labels"),
        "limitations": latest.get("limitations"),
        "note": (
            "These are held-out test metrics from a single 80/20 split on n=50 samples. "
            "Small dataset; results may not generalise. "
            "Training metrics are not displayed to avoid overfitting illusions."
        ),
    }


def _tree_to_dict(
    sklearn_tree: Any,
    feature_names: list[str],
    class_names: list[str],
    node_id: int = 0,
    max_depth: int = 4,
    depth: int = 0,
) -> dict:
    """Recursively serialise one scikit-learn decision tree to a plain dict."""
    TREE_LEAF = -1
    if depth > max_depth:
        return {"type": "truncated", "message": f"Display limit (depth {max_depth}) reached."}

    left_child = int(sklearn_tree.children_left[node_id])
    values = sklearn_tree.value[node_id][0]
    predicted_idx = int(values.argmax())
    predicted_class = class_names[predicted_idx] if predicted_idx < len(class_names) else "Unknown"

    if left_child == TREE_LEAF:
        return {
            "type": "leaf",
            "predicted_class": predicted_class,
            "samples": int(sklearn_tree.n_node_samples[node_id]),
            "class_distribution": {c: int(v) for c, v in zip(class_names, values)},
            "impurity": round(float(sklearn_tree.impurity[node_id]), 4),
        }

    feature_idx = int(sklearn_tree.feature[node_id])
    feature_name = (
        feature_names[feature_idx] if feature_idx < len(feature_names) else f"feature_{feature_idx}"
    )
    threshold = round(float(sklearn_tree.threshold[node_id]), 4)

    return {
        "type": "split",
        "feature": feature_name,
        "threshold": threshold,
        "condition": f"{feature_name} ≤ {threshold}",
        "predicted_class": predicted_class,
        "samples": int(sklearn_tree.n_node_samples[node_id]),
        "impurity": round(float(sklearn_tree.impurity[node_id]), 4),
        "left": _tree_to_dict(
            sklearn_tree, feature_names, class_names,
            int(sklearn_tree.children_left[node_id]), max_depth, depth + 1,
        ),
        "right": _tree_to_dict(
            sklearn_tree, feature_names, class_names,
            int(sklearn_tree.children_right[node_id]), max_depth, depth + 1,
        ),
    }


@app.get("/insights/tree/{tree_index}")
async def insights_tree(tree_index: int):
    """Return the split structure of one decision tree from the Random Forest."""
    try:
        bundle = get_bundle()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Model not available.")

    model = bundle["model"]
    feature_names: list[str] = bundle.get("feature_names", [])
    class_names: list[str] = bundle.get("classes", [])

    if not hasattr(model, "estimators_"):
        raise HTTPException(
            status_code=503,
            detail="Model artifact does not expose estimators_. Tree exploration unavailable.",
        )

    n_trees = len(model.estimators_)
    if tree_index < 0 or tree_index >= n_trees:
        raise HTTPException(
            status_code=400,
            detail=f"tree_index must be 0–{n_trees - 1}. Got {tree_index}.",
        )

    estimator = model.estimators_[tree_index]
    tree_structure = _tree_to_dict(estimator.tree_, feature_names, class_names)

    return {
        "tree_index": tree_index,
        "n_trees_total": n_trees,
        "max_depth_displayed": 4,
        "feature_names": feature_names,
        "class_names": class_names,
        "tree": tree_structure,
        "warning": (
            "This is one tree out of the full Random Forest. "
            "Individual tree predictions may differ from the ensemble."
        ),
    }


class InsightsPredictRequest(BaseModel):
    email: int = Field(ge=0, le=1)
    phone: int = Field(ge=0, le=1)
    url: int = Field(ge=0, le=1)
    ip_address: int = Field(ge=0, le=1)
    date: int = Field(ge=0, le=1)
    windows_path: int = Field(ge=0, le=1)
    sensitive_entity_present: int = Field(ge=0, le=1)
    application_context: str
    privacy_preference: str = "Medium"
    historical_protection_level: str = "Medium"

    @field_validator("privacy_preference", "historical_protection_level")
    @classmethod
    def _validate_preference(cls, v: str) -> str:
        normalised = v.strip().capitalize()
        if normalised not in _VALID_PREFERENCE_VALUES:
            raise ValueError(
                f"Value must be one of {sorted(_VALID_PREFERENCE_VALUES)}, got '{v}'."
            )
        return normalised

    @field_validator("application_context")
    @classmethod
    def _validate_context(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("application_context must not be empty.")
        return v.strip()


@app.post("/insights/predict")
async def insights_predict(payload: InsightsPredictRequest):
    """
    Direct feature prediction for the Model Insights dashboard.
    Accepts the 8 model features explicitly — no OCR, no image upload.
    Inputs are labelled experimental; they are not screenshot-derived observations.
    """
    synthetic_features = {
        "email_count": payload.email,
        "phone_count": payload.phone,
        "url_count": payload.url,
        "ip_count": payload.ip_address,
        "date_count": payload.date,
        "windows_path_count": payload.windows_path,
        "sensitive_entity_count": payload.sensitive_entity_present,
        "ocr_text_length": 0,
        "application_context": payload.application_context,
    }

    result = predict_privacy_risk(
        synthetic_features,
        payload.application_context,
        payload.privacy_preference,
        payload.historical_protection_level,
    )

    return {
        "input_features": {
            "EMAIL": payload.email,
            "PHONE": payload.phone,
            "URL": payload.url,
            "IP_ADDRESS": payload.ip_address,
            "DATE": payload.date,
            "WINDOWS_PATH": payload.windows_path,
            "sensitive_entity_present": payload.sensitive_entity_present,
            "application_context": payload.application_context,
        },
        "risk_prediction": result,
        "disclaimer": (
            "These are manually entered experimental inputs, not screenshot-derived "
            "observations. Do not treat results as valid real-world risk assessments."
        ),
    }
