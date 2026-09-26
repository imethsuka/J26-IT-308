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
from research.eval.curve import build_curve
from research.model import RiskModel
from research.model.explain import global_effects
from research.model.training import load_or_train_default
from research.profile_store import EVIDENCE_NEEDED, GROUPS, LEARNABLE_CATEGORIES, ProfileStore
from research.review import level_taus, review

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


class ReviewRequest(BaseModel):
    text: str
    context: ContextIn
    level: Literal["relaxed", "balanced", "strict"] | None = None   # None = the saved policy level
    # entity id ("start:end") -> what the user chose for that entity in this review
    overrides: dict[str, Literal["masked", "visible"]] = {}
    record_activity: bool = False     # True once per captured text, so re-renders don't inflate stats


class FilterRequest(BaseModel):
    text: str
    context: ContextIn


class FilterOut(BaseModel):
    text: str
    blocked: bool
    redactions: int
    filter_version: str


class CorrectionIn(BaseModel):
    category: str
    decision: Literal["masked", "visible"]


class GroupPolicyIn(BaseModel):
    action: Literal["never", "tokenize", "mask"] | None = None
    enabled: bool | None = None


class LlmPolicyIn(BaseModel):
    mask_before_send: bool | None = None
    provider: str | None = None
    model: str | None = None


class PolicyUpdate(BaseModel):
    level: Literal["relaxed", "balanced", "strict"] | None = None
    groups: dict[str, GroupPolicyIn] = {}
    llm: LlmPolicyIn | None = None


class _State:
    detector: Detector
    filters: dict[str, PrivacyFilter]
    model: RiskModel
    curve: dict | None = None
    store: ProfileStore
    trained_on: str | None


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.detector = Detector.default()
    model = load_or_train_default(state.detector)
    state.model = model
    state.curve = None
    state.store = ProfileStore()
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


@app.post("/review")
def review_text(req: ReviewRequest) -> dict:
    try:
        result = review(req.text, req.context.to_ctx(), detector=state.detector, model=state.model,
                        store=state.store, level=req.level, overrides=dict(req.overrides))
    except Exception as exc:
        # Fail closed: no entities, no text — the UI shows the error instead of a result.
        log.error("review failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail=f"Review failed — content blocked. {type(exc).__name__}: {exc}")
    if req.record_activity:
        state.store.record_review(result["flagged"], result["masked"], result["utility_kept"])
    return result


@app.post("/filter", response_model=FilterOut)
def filter_text(req: FilterRequest) -> FilterOut:
    """The PrivacyFilter contract over HTTP, for the worker's memory stage: redacted text, whether the page must be dropped,
    and how many spans were acted on. No entity values and no audit trail leave this call. A filter failure comes back as
    blocked=True with empty text (the filter itself fails closed); a missing model is a 503, never unfiltered text."""
    try:
        result = state.filters["caprlm"].filter(req.text, req.context.to_ctx())
    except Exception as exc:
        log.error("filter failed (%s); blocking", type(exc).__name__)
        raise HTTPException(status_code=503, detail=f"Filter failed, content blocked. {type(exc).__name__}")
    return FilterOut(text=result.text, blocked=result.blocked, redactions=result.redactions,
                     filter_version=result.filter_version)


@app.post("/corrections")
def add_correction(body: CorrectionIn) -> dict:
    if body.category not in LEARNABLE_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"unknown category: {body.category}")
    state.store.add_correction(body.category, body.decision)
    return _profile()


@app.get("/profile")
def profile() -> dict:
    return _profile()


@app.delete("/profile")
def reset_profile() -> dict:
    state.store.reset_profile()
    return _profile()


def _profile() -> dict:
    rules = state.store.learned_rules()
    return {"corrections": state.store.n_corrections(),
            "rules": [{"category": c, **r} for c, r in sorted(rules.items())],
            "evidence_needed": EVIDENCE_NEEDED}


def _policy() -> dict:
    pol = state.store.policy()
    return {
        "level": pol["level"], "taus": level_taus(state.model),
        "groups": [{"id": g, "label": label, "categories": list(cats), **pol["groups"][g]}
                   for g, (label, cats, _) in GROUPS.items()],
        "llm": pol["llm"],
    }


@app.get("/policy")
def get_policy() -> dict:
    return _policy()


@app.put("/policy")
def put_policy(body: PolicyUpdate) -> dict:
    try:
        state.store.update_policy(
            level=body.level,
            groups={g: c.model_dump(exclude_none=True) for g, c in body.groups.items()},
            llm=body.llm.model_dump(exclude_none=True) if body.llm else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _policy()


@app.get("/stats")
def stats() -> dict:
    week = state.store.weekly()
    reviews = sum(d["reviews"] for d in week)
    rules = state.store.learned_rules()
    return {
        "flagged_7d": sum(d["flagged"] for d in week),
        "redacted_7d": sum(d["redacted"] for d in week),
        "utility_kept": round(sum(d["kept_sum"] for d in week) / reviews, 3) if reviews else None,
        "reviews_7d": reviews,
        "learned_rules": len(rules), "learnable_categories": len(LEARNABLE_CATEGORIES),
        "weekly": [{k: d[k] for k in ("date", "weekday", "flagged", "redacted")} for d in week],
    }


@app.get("/model-info")
def model_info() -> dict:
    """What the risk model is and what it has learned: its version, what it was trained on, and for each risk level the features that
    most raise the model's belief in it. Read from the learned weights (logistic regression); empty for other model kinds."""
    m = state.model
    effects = global_effects(m, top=8)
    classes = [str(c) for c in m.pipeline.classes_] if m.fitted else []
    return {"version": m.version, "kind": m.kind, "trained_on": m.trained_on, "classes": classes,
            "n_features": int(len(m.pipeline.steps[0][1].get_feature_names_out())) if m.fitted else 0,
            "taus": level_taus(m), "explainable": effects is not None, "effects": effects or {}}


def _curve_scenarios():
    """The held-out test scenarios the Metrics page's numbers come from (same split rule and seed as research.eval.run_comparison)."""
    from research.data.llm_dataset import DATA_DIR, load_dir, split

    if not DATA_DIR.exists():
        raise HTTPException(status_code=404, detail="The labelled scenarios are not available on this install, so no curve can be drawn.")
    scenarios, _ = load_dir(DATA_DIR)
    seed = 7
    if RESULTS_PATH.exists():
        seed = int(json.loads(RESULTS_PATH.read_text(encoding="utf-8")).get("dataset_detail", {}).get("seed", 7))
    _, test = split(scenarios, 0.2, seed)
    return test


@app.get("/model-curve")
def model_curve() -> dict:
    """Precision and recall at each protection level and per category, from the model's scores on the test scenarios. Computed once."""
    if getattr(state, "curve", None) is None:
        state.curve = build_curve(state.model, state.detector, _curve_scenarios(), level_taus(state.model))
    return state.curve


@app.get("/eval-results")
def eval_results() -> dict:
    if not RESULTS_PATH.exists():
        raise HTTPException(status_code=404,
                            detail=f"No results.json yet. Generate it with: {RUN_EVAL_HINT}")
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
