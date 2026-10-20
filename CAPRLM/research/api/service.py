"""Local API for the .NET comparison UI. The UI talks to this over HTTP only — it never
imports Python.

    cd CAPRLM
    python -m uvicorn research.api.service:app --port 8787

Fail-closed: if a filter errors, that filter's entry comes back blocked=True with empty
text; if OCR is unavailable the image endpoint returns 503. Nothing is ever returned as
"passed through" because something failed.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.privacy_filter import PrivacyFilter
from shared.types import FilterResult, PrivacyContext, Source

from research.detection import Detector, OcrUnavailableError
from research.eval.run_comparison import RESULTS_PATH
from research.filters import build_filters
from research.model.training import load_or_train_default

log = logging.getLogger("caprlm.api")

RUN_EVAL_HINT = "cd CAPRLM && python -m research.eval.run_comparison"


class ContextIn(BaseModel):
    app: str
    domain: str | None = None
    url: str | None = None
    title: str | None = None
    source: Literal["uia", "ocr"] = "uia"

    def to_ctx(self, source: Source | None = None) -> PrivacyContext:
        return PrivacyContext(app=self.app, domain=self.domain, url=self.url, title=self.title,
                              source=source or Source(self.source))


class CompareRequest(BaseModel):
    text: str
    context: ContextIn


class CompareImageRequest(BaseModel):
    image_base64: str
    context: ContextIn


class AuditOut(BaseModel):
    category: str
    risk_tier: str
    action: str


class FilterResultOut(BaseModel):
    text: str
    blocked: bool
    redactions: int
    filter_version: str
    audit: list[AuditOut]
    error: str | None = None


class CompareResponse(BaseModel):
    results: dict[str, FilterResultOut]
    ocr_text: str | None = None


class _State:
    detector: Detector
    filters: dict[str, PrivacyFilter]
    trained_on: str | None


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.detector = Detector.default()
    model = load_or_train_default(state.detector)
    state.filters = build_filters(model, state.detector)
    state.trained_on = model.trained_on
    log.info("CAPRLM service ready (model %s, trained on %s)", model.version, model.trained_on)
    yield


app = FastAPI(title="CAPRLM comparison service", lifespan=lifespan)


def _to_out(result: FilterResult, error: str | None = None) -> FilterResultOut:
    return FilterResultOut(
        text=result.text, blocked=result.blocked, redactions=result.redactions,
        filter_version=result.filter_version, error=error,
        audit=[AuditOut(**dataclasses.asdict(r)) for r in result.audit],
    )


def _run_all(text: str, ctx: PrivacyContext) -> dict[str, FilterResultOut]:
    out: dict[str, FilterResultOut] = {}
    for name, filt in state.filters.items():
        try:
            out[name] = _to_out(filt.filter(text, ctx))
        except Exception as exc:   # caller-side fail-closed: an error means blocked
            log.error("%s raised %s; returning blocked", name, type(exc).__name__)
            out[name] = _to_out(
                FilterResult(text="", blocked=True, filter_version=getattr(filt, "version", name)),
                error=f"{type(exc).__name__}: filter failed, content treated as blocked",
            )
    return out


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "filters": list(state.filters), "model_trained_on": state.trained_on,
            "detection": state.detector.info()}


@app.post("/compare", response_model=CompareResponse)
def compare(req: CompareRequest) -> CompareResponse:
    return CompareResponse(results=_run_all(req.text, req.context.to_ctx()))


@app.post("/compare-image", response_model=CompareResponse)
def compare_image(req: CompareImageRequest) -> CompareResponse:
    try:
        image = base64.b64decode(req.image_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="image_base64 is not valid base64")
    try:
        text = state.detector.ocr_text(image)
    except OcrUnavailableError as exc:
        # Nothing is filtered or returned: the screenshot's content stays blocked.
        raise HTTPException(status_code=503, detail=f"OCR unavailable — content blocked. {exc}")
    return CompareResponse(results=_run_all(text, req.context.to_ctx(Source.OCR)), ocr_text=text)


@app.get("/eval-results")
def eval_results() -> dict:
    if not RESULTS_PATH.exists():
        raise HTTPException(status_code=404,
                            detail=f"No results.json yet. Generate it with: {RUN_EVAL_HINT}")
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
