import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from backend.services.feature_extraction import extract_features
from backend.services.ocr_service import extract_text
from backend.services.protection import select_protection
from backend.services.risk_prediction import predict_privacy_risk, reload_bundle
from backend.services.rule_baseline import predict_rule_based
from backend.services.sensitive_detection import detect_sensitive_entities
from backend.services import user_profile_service as ups

app = FastAPI(title="J26-IT-308 CAPRLM Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
)

# ---------------------------------------------------------------------------
# Allowed values (validated at the boundary)
# ---------------------------------------------------------------------------
_VALID_RISK_LABELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
_VALID_PREFERENCE_VALUES = frozenset({"Low", "Medium", "High"})

# /train is restricted to local connections only
_TRAIN_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


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

async def _parse_analyze_request(request: Request) -> tuple[bytes, str, str, str, str, str]:
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
        else:
            fields[field_name] = value.decode("utf-8")

    if image_bytes is None:
        raise HTTPException(status_code=400, detail="Missing image file upload")

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
        fields["application_context"],
        fields["privacy_preference"],
        fields["historical_protection_level"],
        user_id,
    )


# ---------------------------------------------------------------------------
# /analyze — main CAPRLM pipeline
# ---------------------------------------------------------------------------

@app.post("/analyze")
async def analyze(request: Request):
    (
        image_bytes,
        image_suffix,
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

        extracted_text, detections_count = extract_text(temporary_path)
        detection_output = detect_sensitive_entities(extracted_text)
        extracted_features = extract_features(
            extracted_text, detection_output, application_context
        )

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

        return {
            "ocr_summary": {
                "extracted_text": "[redacted — not logged for privacy]",
                "detections_count": detections_count,
            },
            "detection_results": detection_output,
            "extracted_features": extracted_features,
            "risk_prediction": risk_prediction,
            "rule_baseline": rule_prediction,
            "protection_selection": protection_selection,
            "user_profile_summary": ups.profile_summary(profile),
        }
    finally:
        temporary_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# /feedback — record user correction and recalibrate threshold
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    user_id: str
    screenshot_id: str
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
        bundle = run_training()
        reload_bundle()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Training failed: {exc}",
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

@app.get("/model-info")
async def model_info():
    """Return current model metadata and last training results."""
    import json
    from backend.services.train_model import TRAINING_LOG_PATH, MODEL_PATH

    if not MODEL_PATH.exists():
        return {
            "model_available": False,
            "message": "No trained model found. POST /train to train the model.",
        }

    last_entry: dict | None = None
    if TRAINING_LOG_PATH.exists():
        try:
            with TRAINING_LOG_PATH.open(encoding="utf-8") as fh:
                history = json.load(fh)
            if history:
                last_entry = history[-1]
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "model_available": True,
        "model_path": str(MODEL_PATH),
        "last_training": last_entry,
    }
