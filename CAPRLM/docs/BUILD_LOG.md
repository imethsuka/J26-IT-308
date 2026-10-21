# CAPRLM build log

Engineering build of the CAPRLM pipeline, comparison harness, local API and comparison UI,
per `CAPRLM_BUILD_PROMPT.md`. Model choice, label protocol, dataset and personalization are
Wijesingha's research calls — everything chosen below to get the pipeline running is a
**default**, listed in [Decisions](#decisions-defaults-picked-vs-genuinely-open).

## What was built

```
CAPRLM/
  shared/privacy_filter.py        + ImageFilter Protocol (only change to shared/ — see below)
  requirements.txt                Python deps
  research/
    detection/                    detection stage: regex + spaCy NER (+ OCR) -> candidate spans
    model/                        features, RiskModel (logreg | random forest), training, selection
    caprlm_filter.py              CAPRLMFilter: detect -> predict tier -> protect (PrivacyFilter + ImageFilter)
    baselines/                    static_masking, anonymization, pseudonymization (+ in-memory key store)
    filters.py                    the five filters under one set of names
    data/                         PLACEHOLDER label table + scenario generator
    eval/run_comparison.py        harness -> eval/results.json
    eval/metrics.py               P/R/F1, leakage, re-identification, similarity
    api/service.py                FastAPI: /compare, /compare-image, /eval-results, /health
    tests/                        63-test suite incl. the shared contract checks (see Testing)
UI/CAPRLMComparison/              WPF + WPF-UI (net8.0-windows) comparison tool
```

`rule_based` is `shared/rule_based_filter.py`, reused unchanged.

### Pipeline

`text` (or screenshot → OCR) → **detection** (regex categories from `rule_based_filter.py`
+ `CREDENTIAL`, plus spaCy NER; regex wins overlaps) → **risk model** (category, detection
confidence, detector, source, app, domain/title buckets, category×context crosses →
tier `none|low|medium|high`) → **protection** (`none` pass-through, `low` `[EMAIL_1]`,
`medium` `[REDACTED]`, `high` removed) → `FilterResult`.

`CAPRLMFilter.filter_version` is `caprlm/<model>-v0.1`, e.g. `caprlm/logreg-v0.1`.

## Run commands

All Python commands run from `CAPRLM/`.

```powershell
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# 1. Generate results.json (also trains and saves the model to research/artifacts/)
python -m research.eval.run_comparison

# 2. Start the API the UI depends on
python -m uvicorn research.api.service:app --port 8787

# 3. Start the UI (separate terminal, from the repo root)
dotnet run --project UI/CAPRLMComparison

# Tests
python -m pytest research shared/contract_tests.py -v
```

If `research/artifacts/caprlm_model.joblib` is missing, the service trains one on the
placeholder dataset at startup and logs a warning. `/health` reports what it was trained on.

**OCR** needs the Tesseract binary (not a pip package): install from
<https://github.com/UB-Mannheim/tesseract/wiki> or set `TESSERACT_CMD`. Without it,
screenshot comparison returns HTTP 503 "OCR unavailable — content blocked".

## API

- `POST /compare` — `{text, context:{app, domain?, url?, title?, source: "uia"|"ocr"}}`
- `POST /compare-image` — `{image_base64, context}`; OCR runs once, then all five filters get the text as `source=ocr`
- `GET /eval-results` — the current `results.json` (404 names the command to generate it)
- `GET /health`

`/compare` and `/compare-image` return `{"results": {caprlm, rule_based, static_masking,
anonymization, pseudonymization}, "ocr_text": ...}`; each entry is `text, blocked,
redactions, filter_version, audit[{category, risk_tier, action}], error`. (Wrapped in
`results` rather than top-level so `ocr_text` has somewhere to live; entries are still keyed by filter name.)

## Change to the shared contract

`shared/privacy_filter.py` gained a **separate `ImageFilter` Protocol**
(`filter_image(image: bytes, ctx) -> FilterResult`), next to `UrlFilter`. It is not a second
method on `PrivacyFilter`: that would break the `isinstance` checks the rule-based and
pass-through filters rely on. Purely additive; `contract_tests.py` is untouched. `CAPRLMFilter`
implements it. PRIVACY_HANDOFF.md §4 (the open `filter_image` question) is updated to point here.

## Decisions: defaults picked vs. genuinely open

The five items in `research/README.md`. None is settled.

| # | Decision | State |
|---|---|---|
| 1 | Label protocol | **Open.** Default: hand-written (category × context-kind) tier table. |
| 2 | Model choice | **Open.** Mechanism built (logreg vs. random forest by CV macro-F1; simplest wins unless beaten by 0.02). On placeholder data they tie → logreg. LightGBM not tried. |
| 3 | Dataset | **Open.** Default: template generator, disjoint train/test value pools. |
| 4 | Key store | **Open.** Default: in-memory per-session store, `pseudonymization` baseline only. CAPRLM's own tokenization is irreversible. |
| 5 | Leakage / re-ID | **Open.** Default: exact-string leakage + token-level re-identification. Crude lexical attacker, not model-based. |

Added while building: **personalization is not implemented** (global model only, results
report `personalization: null`); NER is `en_core_web_sm` with a constant 0.8 confidence; the
tier→action mapping and the ten context keyword buckets are hand-written defaults. All logged
in `research/README.md`.

## Reading the comparison table — what it can and cannot say

`results.json` has `"dataset": "placeholder-not-for-reporting"`, and the UI shows that
prominently. Placeholder labels are a deterministic function of (category, context), so the
model recovers them by construction. **A number from this run is a pipeline check, not a
result**, and in particular does not show CAPRLM beating the baselines.

Last run (250 test scenarios, seed 7, placeholder) — for orientation only:

| filter | P | R | F1 | leakage | re-ID | sim |
|---|---|---|---|---|---|---|
| caprlm | 0.954 | 0.875 | 0.913 | 0.125 | 0.000 | 0.834 |
| rule_based | 1.000 | 0.442 | 0.613 | 0.558 | 0.489 | 0.895 |
| static_masking | 0.825 | 0.904 | 0.863 | 0.096 | 0.000 | 0.809 |
| anonymization | 0.825 | 0.904 | 0.863 | 0.096 | 0.000 | 0.801 |
| pseudonymization | 0.825 | 0.904 | 0.863 | 0.096 | 0.000 | 0.760 |

Things worth knowing when real data replaces it:

- **Compare CAPRLM with `static_masking`, not just `rule_based`.** The three new baselines
  share CAPRLM's regex+NER detection, so that gap isolates the risk model. `rule_based` has
  its own regex-only detection (reused as-is), so a gap to it also includes detection coverage.
- CAPRLM leaks *more* than the redact-everything baselines here (0.125 vs 0.096). That is
  expected — it chooses not to protect low-risk spans and sometimes predicts `none` for a
  sensitive one — and is the privacy/utility trade the research measures. Most of the
  remaining leakage is `en_core_web_sm` missing some money formats, which hits every
  detection-sharing filter equally.
- Precision counts over-redaction: protecting a gold-`none` span is a false positive.
- Semantic similarity is a lexical TF-IDF cosine proxy (`similarity_method` in the JSON).

## Fail-closed

- `CAPRLMFilter` and the three baselines catch any internal failure (model unfitted, spaCy
  model missing, OCR unavailable) and return `blocked=True`, `text=""` — never the input. They
  return rather than raise so a caller without its own try/except still fails closed.
  Tested in `test_filters.py`.
- The API returns a blocked entry (with an `error` field) if a filter raises, and 503 if OCR is
  unavailable. The eval harness scores a raising filter as blocked.
- The UI clears previous cards before each run, so a failed request never leaves a stale
  result looking current, and shows the run command when the service is down.
- There is deliberately no silent regex-only fallback when the NER model is missing.

## UI

Layout: a single project, `UI/CAPRLMComparison/` (leaves room for other tools under `/UI`).
`net8.0-windows`, WPF + WPF-UI 3.0.5 (Fluent/Mica, same as `ContextDashboard`), no
WinForms/tray. Charts: **ScottPlot.WPF 5.0.55**.

- **Live comparison** tab: text box, screenshot picker (OCR path), app/domain/title/source,
  Compare → five side-by-side cards (redacted text, redaction count, `filter_version`, audit).
- **Metrics** tab: `GET /eval-results`, one bar chart per metric with CAPRLM highlighted.
  The `dataset` string is the first thing on the page; a placeholder is a red banner and
  every chart title says "[placeholder data]".
- If the service is unreachable, both tabs show the run command instead of a blank screen.

## Testing and what was verified

- `python -m pytest research shared/contract_tests.py` → **63 passed**. The new filters
  run through the existing contract checks *unchanged*: `contract_tests.py` hard-codes its
  implementation list, so `research/tests/test_contract_new_filters.py` imports its checks
  and applies them to CAPRLM and the three baselines (and picks up any new single-`impl` check).
- Tests caught two real bugs, both fixed: the shared email regex swallows a sentence-final
  period (trimmed in `research/detection/regex_detector.py`; `shared/` itself untouched),
  and an empty `SessionKeyStore` was falsy so a passed-in store was discarded.
- Live run: service started, `/health`, `/compare`, `/eval-results`, `/compare-image` all
  exercised over HTTP. The UI's own `ApiClient`, models and chart code were run against it,
  and the charts rendered to PNG and inspected. The WPF app launches and stays responsive.

**Not verified:**
- OCR against a real Tesseract — it isn't installed on this machine. The OCR path is tested
  with a fake engine and its failure path (unavailable → blocked / 503) with the real one.
- The WPF window's visual layout. I confirmed it builds, starts and responds, but did not
  see it rendered, so spacing/theming are unchecked.
- Anything with `en_core_web_trf`.

## Update: LLM dataset, detector upgrade, context features

- **Data:** `research/data/llm_dataset.py` loads the JSONL files in `CAPRLM Data/` (tolerates
  several objects per line and bad escapes; reports every dropped row). Train/evaluate with
  `python -m research.eval.run_comparison --data`. Dataset tag: `llm-synthetic-unaudited`
  (UI shows it as a warning). 9 files, 529 usable rows at the time of this run; split is a random 80/20
  by row, which is optimistic (all one generator) — split by generator model once there are two.
- **Detectors:** `detection/regex_detector.py` is now a rule table: international dates, currency
  amounts, national IDs (LK NIC, US SSN, UK NI, SG NRIC, IN PAN/Aadhaar, BR CPF, PK CNIC, passports),
  IBAN (mod-97), SWIFT, IFSC, IPv4/IPv6, URL tokens, credentials/OTP/JWT/AWS keys, a general
  medical lexicon, obfuscated emails/phones. Sensitive-span detection coverage on the LLM data went
  from ~61% to ~87% (reported in `results.json` as `detection_coverage_*`).
  Still missed: non-Latin / non-English names and places, OCR-garbled values, some org names.
  Fixing those needs a multilingual transformer NER (spaCy's `xx_ent_wiki_sm` was tried and
  rejected — it labels people as locations).
- **Features:** app names now feed the context buckets (55% of rows previously had none), and the
  words around each span (`Candidate.window`, never the span itself) are features.
- **Result (indicative, unaudited):** CAPRLM P 0.94 / R 0.72 / F1 0.82 vs static_masking
  P 0.82 / R 0.86 / F1 0.84. CAPRLM is more precise but still leaks more (0.28 vs 0.14); it does
  not beat the baseline on F1 here. Not reportable until labels are human-audited.
- Tests: 86 passing (adds `test_detection_international.py`).

## Update: recall tuning + richer features (model v0.2)

- **Features added:** span shape (length / digit-ratio / case buckets, character-class flags) and
  document signals (length, number of candidates, a general keyword lexicon — password, otp,
  account, diagnosis, ...). Buckets and flags only; no span content enters the features or the
  saved model. `detection/detector.py` (`_shape`, `_doc_signals`), `model/features.py`.
- **Tuning (training data only, out-of-fold):** logreg C in {0.3..30} x class weights, vs. random
  forest; plus a **none-threshold** — a span is "none" only if P(none) >= tau, else it gets its best
  non-none tier. tau is chosen to maximise F2 of protect-vs-pass. F2 (recall weighted 2x) is a
  privacy-first default and a research decision for Wijesingha, not a settled one.
  `model/risk_model.py` (`select_model`, `tune_threshold`).
- **Result on the 106 held-out rows (unaudited LLM data, indicative only):**

  | | P | R | F1 | leakage |
  |---|---|---|---|---|
  | v0.1 (old features, untuned) | 0.941 | 0.720 | 0.816 | 0.280 |
  | v0.2 features, untuned | 0.948 | 0.748 | 0.836 | 0.252 |
  | v0.2 tuned (C=3, balanced, tau=0.9) | 0.879 | 0.829 | 0.854 | 0.171 |
  | static_masking | 0.815 | 0.862 | 0.838 | 0.138 |

  Features moved F1 by ~+0.02, tuning by another ~+0.02 (recall up, precision down by design).
  CAPRLM's F1 is now 0.016 above static masking, which on ~100 test rows is within noise — it is
  NOT evidence that CAPRLM beats the baseline. It still leaks more (0.171 vs 0.138).
  `results.json -> model.untuned_on_test` records the untuned row.
- Tests: 91 passing (adds `test_model_tuning.py`).

## Benchmark notebook (`Colab/CAPRLM_benchmark_colab.ipynb`)

Modelled on Member 1's `03_experiments_colab.ipynb`: settings cell, clone/setup, then numbered
experiments, a "how to read this" cell, and CSV export. It runs on the same LLM dataset
(`CAPRLM Data/`, synthetic text, so safe in the repo and in Colab) using `research/eval/benchmark.py`.

1. **Detection ceiling** — share of sensitive spans any detector finds, regex only vs regex + spaCy.
2. **Feature ablations** — same logistic regression with feature groups switched on one at a time
   (`research/model/features.py: ALL_GROUPS`), plus "full minus app/domain/title" as the direct test of
   the context-aware claim. Repeated k-fold, mean +/- std, and a *paired per-fold difference* table so a
   gap can be told apart from fold-to-fold noise.
3. **Unseen context** — leave-one-context-kind-out (kinds under 20 rows skipped).
4. **Per category** — recall by entity category vs the baselines.

Everything is tuned inside each fold from that fold's training rows only. Running it needs the
`CAPRLM/` folder (code and data) on GitHub or `DATA_DIR` pointing at uploaded data; locally it finds the
repo itself. `TUNE=False` is a fast smoke test only — at a fixed threshold the "category only" variant
collapses into static masking, so don't read ablations from it.
Tested: executed end to end locally with `jupyter nbconvert --execute` (not yet run in Colab).

## Privacy Review + Policy & Risk screens (API + `UI/CAPRLMComparison`)

- **API (`research/api/service.py`):** `POST /review` (per-entity risk, decision, reason, redacted text segments,
  residual risk, outbound-payload preview), `POST /corrections`, `GET|DELETE /profile`, `GET|PUT /policy`, `GET /stats`.
  Engine in `research/review.py`; local state (policy, corrections, per-day counts — never content) in
  `research/profile_store.py` (`research/artifacts/profile.json`, or `CAPRLM_STATE_PATH`). Fails closed: a review error is a 503, no text.
- **Decision order per entity:** your override > enforced category policy > learned rule > model (`risk >= tau`).
  `risk = 1 - P(none)` (`RiskModel.risk_scores`). Balanced tau is the trained model's own operating point
  (`1 - none_threshold`), strict halves it, relaxed is at least 0.5 — so the screen's numbers are not the 0.55 of the mockup.
- **"Learned from corrections" is deliberately simple:** a category rule appears once one decision leads the other by 2
  corrections (category only, not yet per-context). It is a mechanism, not a validated result; it has no evaluation yet.
- **Definitions to defend if asked:** *residual risk* = noisy-OR of the risk of entities left visible (0-100);
  *flagged* = masked or risk >= 0.5; *utility kept* = share of non-space characters left unmasked, averaged over reviewed captures
  (a proxy, not the benchmark's semantic similarity).
- **Not built:** "Private messages — blur + summarize" (needs image/message handling); the LLM panel only previews the outbound
  payload, nothing is sent. Tests: `research/tests/test_review.py` (10).
- **UI:** sidebar shell (Privacy review, Policy & risk, Live comparison, Metrics), warm palette in `Themes/Palette.xaml`.

## Unified app and frozen service

- One sidebar app now hosts both members' work: `UI/ContextDashboard/` (shell + Memory module) loads `UI/CAPRLMComparison`
  (Privacy module) through `UI/Suite.Core`'s `IModule`. The privacy module is a library; the standalone comparison exe is gone.
- On start the dashboard launches the CAPRLM service itself (`UI/CAPRLMComparison/Services/CaprlmBackend.cs`): `caprlm\caprlm-service.exe`
  when installed, else `python -m uvicorn` in this folder. It stops the service on exit only if it started it.
- `packaging/` freezes the service with PyInstaller (`build_service.ps1`, `caprlm_service.spec`, `service_entry.py`) and copies the
  Tesseract binary beside it. Installed, the risk model and `profile.json` live in `%LOCALAPPDATA%\context-importance\caprlm`
  (`CAPRLM_MODEL_PATH`, `CAPRLM_STATE_PATH`), never next to the program.
