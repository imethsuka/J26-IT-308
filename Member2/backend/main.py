import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from services.feature_extraction import extract_features
from services.ocr_service import extract_text
from services.protection import select_protection
from services.risk_prediction import predict_privacy_risk
from services.sensitive_detection import detect_sensitive_entities

app = FastAPI(title="J26-IT-308 CAPRLM Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
)


@app.get("/")
def root():
    return {
        "project": "J26-IT-308",
        "component": "CAPRLM",
        "status": "Backend is running"
    }


# Multipart upload parsing
async def _parse_analyze_request(request: Request) -> tuple[bytes, str, str, str, str]:
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
    missing_fields = [field for field in required_fields if field not in fields]
    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Missing form fields: {', '.join(missing_fields)}",
        )

    return (
        image_bytes,
        image_suffix,
        fields["application_context"],
        fields["privacy_preference"],
        fields["historical_protection_level"],
    )


# CAPRLM analysis pipeline
@app.post("/analyze")
async def analyze(request: Request):
    (
        image_bytes,
        image_suffix,
        application_context,
        privacy_preference,
        historical_protection_level,
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
        risk_prediction = predict_privacy_risk(
            extracted_features,
            application_context,
            privacy_preference,
            historical_protection_level,
        )
        protection_selection = select_protection(
            risk_prediction["risk_level"],
            risk_prediction["confidence"],
            detection_output,
        )

        return {
            "ocr_summary": {
                "extracted_text": extracted_text,
                "detections_count": detections_count,
            },
            "detection_results": detection_output,
            "extracted_features": extracted_features,
            "risk_prediction": risk_prediction,
            "protection_selection": protection_selection,
        }
    finally:
        temporary_path.unlink(missing_ok=True)