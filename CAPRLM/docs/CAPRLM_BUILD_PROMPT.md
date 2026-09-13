# CAPRLM Build Prompt — Detection, Risk Model, Baselines, Comparison UI

Paste this whole file as the instruction to the Claude session that will do the
implementation work in this repo (J26-IT-308). It assumes that session has shell
access to the repo and can run Python and build .NET projects.

## Context you need before touching anything

- This repo is a 4-member honours research project (SLIIT, DSIS group, J26-IT-308).
- **CAPRLM is Member 2's component** (W.L.S.A. Wijesingha, IT23252936) — Context-Aware
  Privacy Risk Learning Model. You are implementing it as an engineering task for the
  shared team codebase; the research decisions it embodies (model choice, label
  protocol, dataset) belong to Wijesingha, not to whoever writes the code.
- `CAPRLM/shared/` already defines the interface contract both members rely on:
  - `shared/types.py` — `PrivacyContext`, `FilterResult`, `RedactionRecord`
  - `shared/privacy_filter.py` — the `PrivacyFilter` Protocol: `filter(text, ctx) -> FilterResult`
  - `shared/rule_based_filter.py` — the rule-based baseline (already implemented, reuse it)
  - `shared/passthrough_filter.py` — dev-only no-op filter
  - `shared/contract_tests.py` — every filter you write must pass this (`pytest CAPRLM/shared/contract_tests.py -v`)
  Read `CAPRLM/docs/PRIVACY_HANDOFF.md` in full before writing any filter — it states the
  fail-closed rule: if no filter is installed, or a filter call raises, the caller treats
  the content as blocked. Nothing bypasses that.
- `CAPRLM/research/` is intentionally empty except for `research/README.md`, which lists
  five **open, unresolved research decisions**: label protocol for risk ground truth,
  model choice (decide empirically vs. the rule-based baseline, don't assume), dataset
  construction, reversible-tokenization/key-store design, and the leakage/re-identification
  evaluation method. Read that file first. Nothing in `shared/` assumes any of these are
  answered — it only assumes you hand back something that implements `PrivacyFilter`.
- Do not touch `context-importance-system/` or `ContextDashboard/` (Member 1's PCCILM
  code) except to read an example call site if you need one.

## Where OCR and NER fit

They are the **detection stage**, upstream of the risk model — not the risk model itself:

```
raw content (UIA text, or screenshot)
        │
        ▼
 ┌─────────────────────┐
 │  DETECTION STAGE     │   OCR (screenshots → text) + NER (entity spans) +
 │                      │   the existing regex categories in rule_based_filter.py
 └─────────┬────────────┘
           │  candidate spans: (text span, category, confidence)
           ▼
 ┌─────────────────────┐
 │  RISK PREDICTION     │   ← this is CAPRLM itself, the learned/research part
 │  MODEL               │     (features: category + confidence + PrivacyContext
 └─────────┬────────────┘       fields — app/domain/title/source)
           │  risk tier per span
           ▼
 ┌─────────────────────┐
 │  PROTECTION          │   mask / tokenize / drop / pass-through, per risk tier
 └─────────┬────────────┘
           ▼
     FilterResult (text, blocked, redactions, audit[])
```

OCR and NER are feature-extraction / entity-detection — necessary inputs, not the
contribution. The research contribution is the risk-prediction stage deciding *how
sensitive* a detected entity actually is given its context, personalized per user and
adapted over time — that's what has to be compared against the rule-based baseline, not
whether OCR/NER can find entities at all (they're off-the-shelf).

## Part A — Python, under `CAPRLM/research/`

1. **Detection** (`research/detection/`):
   - NER on text already extracted (whether from UIA directly, or from OCR output) —
     use an off-the-shelf model (e.g. spaCy `en_core_web_trf`) *plus* the existing regex
     categories in `rule_based_filter.py`; don't discard those, they're free precision on
     structured entities regex already handles well.
   - OCR, for `PrivacyContext.source == Source.OCR` content (screenshots): run OCR (e.g.
     Tesseract or EasyOCR) before NER. This needs its own method — e.g. `filter_image()`
     — not bolted onto `filter()`. `PRIVACY_HANDOFF.md` already flags this as an open,
     in-scope item (§4); if you add it to the `PrivacyFilter` Protocol in `shared/`,
     call that out explicitly in your build log (see Documentation below) rather than
     changing the shared contract silently.
   - Output: a list of `(span, category, confidence)` candidates. This is the *input*
     to the risk model, not CAPRLM's output.

2. **Risk prediction model** (`research/caprlm_filter.py`):
   - Features: entity category + confidence + `PrivacyContext` fields. If you add any
     personalization signal, don't invent the mechanism silently — it's not one of the
     five decisions already logged in `research/README.md`; add it there as a new item
     with your reasoning instead of just coding it in.
   - Start with the simplest model that could plausibly work (logistic regression or a
     plain Random Forest) and only justify something more complex (LightGBM, a
     personalized/continual variant) by showing it beats the simpler model *and* the
     rule-based baseline on the evaluation harness below. The TAF explicitly requires
     Member 2 to "compare CAPRLM against conventional risk scoring" — a model you can't
     show beating the baseline isn't ready to be called the result.
   - Implement as a class satisfying `PrivacyFilter` (e.g. `CAPRLMFilter`), with
     `filter_version` like `"caprlm/<model-name>-v0.1"` so the audit trail distinguishes
     which model produced which result.

3. **Baselines for the comparison table** (`research/baselines/`), each its own
   `PrivacyFilter` implementation — these are the ones the TAF and proposal name:
   - `rule_based` — already exists in `shared/rule_based_filter.py`; reuse, don't
     reimplement.
   - `static_masking` — fixed category → mask mapping, no risk tiers, no context.
   - `anonymization` — category-generalization (replace with class label, e.g.
     `[PERSON]`, `[LOCATION]`), not reversible.
   - `pseudonymization` — consistent per-entity surrogate values (same email → same
     token within a session), reversible only via a key store kept *inside*
     `research/` — per `PRIVACY_HANDOFF.md`'s open question, this key store never
     crosses to Member 1's code or to `shared/`.

4. **Evaluation harness** (`research/eval/run_comparison.py`):
   - Runs CAPRLM + all four baselines over the same labeled scenario set.
   - Metrics — use only what the project's own evaluation plan already names, don't
     invent new ones: Precision/Recall/F1 (detection), leakage rate / re-identification
     success (privacy), semantic similarity before/after (utility), and
     personalization before/after *only if* a personalization mechanism actually exists.
   - Writes one `research/eval/results.json` — the only thing the UI reads; the UI must
     never import Python directly.
   - **If no labeled dataset exists yet** (decision #1 in `research/README.md` is still
     open), build the harness against a small placeholder scenario set and mark it
     unmistakably, e.g. `"dataset": "placeholder-not-for-reporting"` in the output. A
     placeholder number must never be presentable as a real result.

5. **Local API for the .NET UI** (`research/api/service.py`, FastAPI):
   - `POST /compare` — body `{text, context: {...}}` → returns `FilterResult` from
     CAPRLM + all four baselines, keyed by filter name, for one input shown five ways.
   - `GET /eval-results` — returns the current `results.json`, for the metrics charts.
   - Document the exact run command (e.g. `uvicorn research.api.service:app --port 8787`)
     in the build log — the UI depends on it being running locally.

## Part B — C#/.NET comparison UI, new project at repo root: `/UI`

- Match `ContextDashboard`'s conventions: `net8.0-windows`, WPF + WPF-UI (Fluent/Mica
  styling). Skip WinForms/tray — this is a standalone comparison tool, not a background
  service.
- Pick a concrete project layout and record it in the build log — e.g. `/UI/CAPRLMComparison/`
  (leaving room for other comparison tools under `/UI` later) or a single project
  directly under `/UI`.
- Screens:
  1. **Input + live comparison** — a text box, a file picker for a screenshot (OCR path),
     and the `PrivacyContext` fields (app, domain, title, source dropdown). A "Compare"
     button calls `POST /compare` and renders all five results as side-by-side cards:
     redacted text, redaction count, `filter_version`, audit category list.
  2. **Metrics dashboard** — calls `GET /eval-results`, renders CAPRLM vs. each baseline
     per metric (bar/line charts) using a WPF-compatible chart library (e.g. LiveCharts2
     or ScottPlot.WPF — pick one, don't hand-roll chart rendering).
  3. Surface the `"dataset"` field from `results.json` directly and visibly in the UI —
     never let a placeholder-dataset result display as if it were real.
- `HttpClient` + `System.Text.Json` against `http://localhost:8787` is enough; no
  generated client needed for two endpoints.
- If the Python service isn't reachable, show an explicit message naming the run
  command, not a blank screen.

## Documentation — do this before calling the task finished

Write/update these under `CAPRLM/docs/`:

- `CAPRLM/docs/BUILD_LOG.md` — what was built, exact run commands for the Python
  service and the UI, which of the five open decisions in `research/README.md` you had
  to pick a default for (and why) versus which are genuinely still open, and any change
  made to the shared `PrivacyFilter` Protocol (e.g. adding `filter_image`).
- Update `CAPRLM/research/README.md`'s checklist — tick what's now decided and point to
  where it's implemented (file/line), rather than deleting the item outright.

## Boundaries (non-negotiable)

- Model choice, personalization mechanism, and dataset/label protocol are Wijesingha's
  research calls. If you pick a default to get the pipeline working end-to-end, log it
  as a default in `BUILD_LOG.md` — never present it as a settled research decision.
- The fail-closed rule from `PRIVACY_HANDOFF.md` applies to CAPRLMFilter too: if it
  raises, or the API service is down, the caller treats the content as blocked, not
  passed through unfiltered.
- Every new filter must pass `CAPRLM/shared/contract_tests.py` unchanged.
