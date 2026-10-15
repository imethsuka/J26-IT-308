# Project Control Center — J26-IT-308

**Research project:** Context-Aware Privacy Intelligence and Adaptive Risk Learning Framework (CAPRLM)
**Repository root:** `C:\Users\ASUS\OneDrive\Desktop\J26-IT-308`
**Active branch:** `sashi` (tracking `origin/sashi`)
**Document status:** Living document. Update after every significant work session.
**Last verified:** 2026-10-15

---

## For future Claude Code sessions — READ THIS FIRST

1. Read this file before making any changes to the project.
2. Verify current state by reading files and running safe read-only commands. Do not trust historical status values in this document without re-checking.
3. Update this document when significant work is completed, blocked, or a decision is made.
4. Record evidence (test output, file path, command result) for every DONE status you assign.
5. Preserve all UNRESOLVED DECISION entries until a human supervisor explicitly approves a resolution.
6. Never mark any item DONE without reproducible evidence.
7. Never stop, restart, or interfere with any running data-processing job (OCR pipeline or otherwise) without explicit user authorization.
8. Never overwrite or modify protected files: `CAPRLM_annotation_50.csv`, `caprlm_model.joblib`, `manifest.json` in any output directory, any `candidates.csv`, any training log.
9. Never commit or push without explicit user authorization per session.
10. The OCR pipeline (`process_screenshots.py`) may be running in the background. Check process list before any operation that could affect data files.

---

## 1. Project Identity and Research Objectives

**Course/project code:** IT-308, group J26
**System name:** CAPRLM — Context-Aware Privacy Risk Learning and Management
**Research question:** Can a desktop privacy monitor automatically classify screenshot privacy risk, adapt to user preferences, and apply reversible tokenization to limit sensitive data exposure in API responses?

**Stated proposal objectives (from README and implementation):**
- Batch OCR ingestion of desktop screenshots
- Rule-based PII entity detection (EMAIL, PHONE, URL, IP_ADDRESS, DATE, WINDOWS_PATH)
- ML risk classification (Random Forest) trained on manual annotations
- Risk-adaptive reversible tokenization (T5 prototype)
- Protection action selection (MONITOR / BLUR_SENSITIVE / BLUR_AND_ALERT / BLOCK)
- User feedback loop with adaptive threshold recalibration
- REST API with FastAPI + SQLite persistence
- Frontend UI (HTML/JS/CSS)

**Proposal requirements that are NOT implemented** (see Section 14 for full list):
- Computer vision / visual feature extraction
- Trained NER model
- Actual image pixel transformations
- Persistent reversible tokenization across requests
- Continual learning
- Re-identification / privacy leakage simulation experiments
- Retrieval utility evaluation

---

## 2. System Scope and Major Components

### Implemented components (Member 2 / CAPRLM)

| Component | File(s) | Status |
|---|---|---|
| OCR batch pipeline | `backend/scripts/process_screenshots.py` | DONE |
| Sensitive entity detection | `backend/services/sensitive_detection.py` | DONE |
| Feature extraction | `backend/services/feature_extraction.py` | DONE |
| Rule-based baseline classifier | `backend/services/rule_baseline.py` | DONE |
| Baseline evaluation (fair comparison) | `backend/services/baseline_eval.py` | DONE |
| Random Forest ML model training | `backend/services/train_model.py` | DONE |
| Risk prediction service | `backend/services/risk_prediction.py` | DONE |
| Reversible tokenization (T5 prototype) | `backend/services/tokenizer.py` | DONE |
| Protection action selection | `backend/services/protection.py` | DONE |
| User profile + adaptive thresholds | `backend/services/user_profile_service.py` | DONE |
| SQLite persistence (WAL) | `backend/database.py` | DONE |
| FastAPI REST API | `backend/main.py` | DONE |
| Frontend UI | `frontend/index.html`, `app.js`, `style.css` | DONE |
| 50-row annotated ground truth | `backend/data/CAPRLM_annotation_50.csv` | DONE |
| Trained model artifact | `backend/data/caprlm_model.joblib` | DONE |
| Annotation guidelines (draft) | `backend/data/docs/annotation_guidelines_DRAFT.md` | IN_PROGRESS |
| Annotation review report | `backend/data/docs/annotation_review_report_DRAFT.md` | IN_PROGRESS |
| Consolidated annotation review | `backend/data/docs/consolidated_annotation_review_DRAFT.md` | IN_PROGRESS |

### Member 1 component (separate)

| Component | File(s) | Status |
|---|---|---|
| MobileCLIP-S0 desktop monitor | `Member1/monitor.py` | DONE (prototype) |
| MobileCLIP raw benchmark | `Member1/bench_raw.py` | DONE |
| MobileCLIP feasibility study | `Member1/README.md` | DONE |

### Members 3 and 4

**NEEDS_VERIFICATION:** No `Member3/` or `Member4/` directories exist in the repository root as of 2026-10-15. Either their work is on a separate branch, in a separate repository, or not yet contributed. This must be clarified before any cross-member integration work.

---

## 3. Current Verified Implementation Status

All statuses below have an evidence reference. Re-verify before relying on any DONE status.

| Item | Status | Evidence |
|---|---|---|
| 209 tests pass, 0 failures | DONE | `pytest --collect-only -q` → 209 collected; live run → 209 passed, 1 warning |
| Model trained (v1.0.0, 2026-10-13) | DONE | `backend/data/training_log.json`; `caprlm_model.joblib` exists (145,320 bytes) |
| `baseline_eval.py` compares both classifiers on same test fold | DONE | commit `156a2f3`; reproducible with `python -m backend.services.baseline_eval` |
| README corrected (privacy gap, baseline method, test count) | DONE | commit `156a2f3` diff verified 2026-10-15 |
| OCR pipeline running (limit 1000, ~850 done) | IN_PROGRESS | manifest.json: total=850 done=850; PID 23292 active |
| Annotation guidelines v0.2 draft written | IN_PROGRESS | `docs/annotation_guidelines_DRAFT.md` — not yet supervisor-approved |
| Annotation review (16 anomalous rows) written | IN_PROGRESS | `docs/consolidated_annotation_review_DRAFT.md` — not yet actioned |
| Annotation corrections applied | PENDING | No label changes have been made to `CAPRLM_annotation_50.csv` |
| Protected files unmodified | DONE | `git status` on protected files → clean; sizes/timestamps verified 2026-10-14 |

---

## 4. Current Active Tasks

| Task | Status | Notes |
|---|---|---|
| OCR pipeline run (limit 1000) | IN_PROGRESS | PID 23292; ~850/1000 done as of 2026-10-15 08:42 UTC; do NOT stop |
| Annotation review — human visual inspection of 16 rows | PENDING | Requires human to open each flagged screenshot |
| Annotation policy decisions — 5 unresolved policies | PENDING | Requires supervisor approval before any label changes |
| Model retraining after annotation corrections | PENDING | Blocked on annotation corrections |
| OCR output integrity audit before expanding training data | PENDING | Blocked on OCR completion |

---

## 5. Next Three Recommended Actions (Priority Order)

### Priority 1 — Wait for OCR completion, then audit output integrity

The OCR pipeline (limit 1000) is actively running. When it finishes:
1. Verify `manifest.json` total = 1000, failed = 0 (or document every failed entry).
2. Cross-check `candidates.csv` row count equals manifest `done` count.
3. Check for duplicate `screenshot_id` values in `candidates.csv`.
4. Spot-check 5–10 rows by matching `screenshot_id` against the source image directory.
5. Document the audit result before using `candidates.csv` for any training or annotation expansion.

**Why this is first:** all downstream work (annotation expansion, model retraining, research evaluation) depends on having verified OCR output. Using unaudited candidates introduces silent data quality problems.

### Priority 2 — Human visual review of the 16 flagged annotation rows

Open each of the 16 screenshots listed in `consolidated_annotation_review_DRAFT.md` and:
- For each: confirm or correct the `privacy_risk_label` based on visual content.
- Resolve the 5 unresolved policy decisions in `annotation_guidelines_DRAFT.md`.
- Record decisions in `annotation_notes` or equivalent before modifying `CAPRLM_annotation_50.csv`.

**Why this is second:** 32% of the ground-truth dataset has identified inconsistencies. One HIGH-labelled row (`02f39c8f`) has all-zero entity flags and constitutes 50% of the HIGH class — this makes HIGH-class evaluation entirely unreliable until corrected. Model retraining before these corrections wastes time.

### Priority 3 — Retrain model after annotation corrections

After corrections to `CAPRLM_annotation_50.csv` are approved:
1. Run `python -m backend.services.train_model` from `Member2/`.
2. Re-run `python -m backend.services.baseline_eval` to regenerate the comparison table.
3. Update `Member2/README.md` evaluation section with new metrics.
4. Commit with evidence of the new `training_log.json` values.

**Why this is third:** the current model was trained on a dataset with known labelling errors. Any evaluation numbers in the research report should reflect the corrected dataset.

---

## 6. Blockers and Unresolved Issues

| Blocker | Affects | Resolution |
|---|---|---|
| OCR pipeline still running | Cannot audit candidates.csv yet | Wait for completion; do not stop the process |
| 16 annotation rows require human visual review | Model quality, evaluation validity | Human must open screenshots; cannot be automated |
| 5 annotation policy decisions unresolved | Annotation guidelines validity | Supervisor approval required |
| Members 3 and 4 directories absent from repo | Cross-member integration unknown | Clarify with team |
| No inter-annotator agreement metric | Research validity | Requires second annotator or explicit scope limitation in report |
| `annotation_notes` column missing from CSV | Cannot document visual-reason labels | Schema change needed before correcting all-zero MEDIUM/HIGH rows |
| HIGH class: 1 of 2 rows is anomalous | HIGH-class F1 unreliable | Blocked on annotation review |

---

## 7. Technical Decisions and Their Rationale

| Decision | Rationale | Status |
|---|---|---|
| Random Forest over neural model | Dataset too small (n=50) for reliable neural training; RF with stratified CV is appropriate at this scale | DONE — locked in v1.0.0 |
| 80/20 stratified split, random_state=42 | Ensures reproducibility and keeps minority classes in both folds | DONE |
| Context encoder fitted on training fold only | Prevents label leakage from test-fold context strings into feature encoding | DONE — verified in code |
| Tokenization threshold 0.75 | Design choice; not empirically optimised | PENDING empirical optimisation |
| CV folds capped at 2 | Only 2 HIGH training examples; more folds would leave some folds with 0 HIGH | DONE — expected behaviour |
| Protection module returns action name only (no pixel ops) | Image manipulation out of scope for prototype | DONE — documented in README |
| SQLite WAL mode | Avoids write-lock contention during concurrent `/analyze` + `/feedback` calls | DONE |
| `token_map` not returned to client | Privacy design: restoration mapping must not be exposed | DONE |
| Rule baseline evaluated on same test fold as ML model | Ensures fair comparison; prior evaluation on full 50 rows was misleading | DONE — commit 156a2f3 |

### Unresolved policy decisions (from annotation_guidelines_DRAFT.md v0.2)

| Policy | Description | Status |
|---|---|---|
| Policy 1 | Whether CRITICAL is a valid annotation label (currently not used) | UNRESOLVED |
| Policy 2 | Whether DATE-alone detection (no other entities) triggers MEDIUM or stays LOW | UNRESOLVED |
| Policy 3 | How to handle MEDIUM labels when `annotation_notes` column is absent | UNRESOLVED |
| Policy 4 | Whether to add `annotation_notes` column to `CAPRLM_annotation_50.csv` | UNRESOLVED |
| Policy 5 | Whether to expand ground truth beyond 50 rows before or after resolving current anomalies | UNRESOLVED |

---

## 8. Dataset and Annotation Status

### Ground-truth annotation file

| Property | Value | Verified |
|---|---|---|
| File | `Member2/backend/data/CAPRLM_annotation_50.csv` | Yes — 3,298 bytes, last modified 2026-10-14 03:56 |
| Total rows | 50 | Yes — from training_log.json and README |
| LOW | 38 (76%) | Yes — training_log.json |
| MEDIUM | 10 (20%) | Yes — training_log.json |
| HIGH | 2 (4%) | Yes — training_log.json |
| CRITICAL | 0 | Yes — training_log.json |
| Single annotator | Yes | Stated in README Limitation 5 |
| Inter-annotator agreement | Not computed | Not verifiable from existing data |
| Anomalous rows | 16 of 50 (32%) | Yes — consolidated_annotation_review_DRAFT.md |
| Labels modified | None | Yes — git shows file clean |

### Annotation inconsistencies (from consolidated_annotation_review_DRAFT.md)

| Group | Issue | Rows | Impact |
|---|---|---|---|
| G1 | HIGH label with all-zero annotation flags | 1 (`02f39c8f`) | 50% of HIGH class; makes HIGH-class F1 unreliable |
| G2 | MEDIUM label with EMAIL=1 (should be HIGH per guidelines §3.1) | 1 (`02e86b37`) | Potential mislabelled MEDIUM |
| G3 | HIGH label where annotation flags support only MEDIUM | 1 (`00c019f8`) | Conflated MEDIUM/HIGH boundary |
| G4 | LOW label with URL=1 (should be ≥ MEDIUM) plus flag conflict | 1 (`01bf28e2`) | Mislabelled LOW |
| G5 | MEDIUM with all-zero annotation flags (no notes column) | 6 rows | Core source of MEDIUM F1 = 0.000 |
| G6 | MEDIUM with DATE=1 only, non-browser context | 1 | Policy 2 dependency |
| G7 | LOW where pipeline detects PHONE (annotation PHONE=0) | 3 rows | Flag discrepancy; annotation may be wrong |
| G8 | LOW where pipeline detects PHONE+IP (annotation all-zero) | 2 rows | Flag discrepancy; annotation may be wrong |

All 16 require human visual review. No labels should be changed by an automated process.

### OCR pipeline and candidates

| Run | Output dir | Images done | Images failed | Candidates CSV rows | Target |
|---|---|---|---|---|---|
| pilot_10 | `data/pilot_10/` | 10 | 0 | 9 | 10 |
| pilot_final_20 | `data/pilot_final_20/` | 20 | 0 | 20 | 20 |
| processing_output (ACTIVE) | `data/processing_output/` | 850 | 0 | 861 (incl header) | 1000 |

**Currently active run (as of 2026-10-15 ~09:00 local):**
- Command: `process_screenshots --limit 1000 --batch-size 10 --ocr-timeout 120`
- PID: 23292 (main), PID 1252 (worker subprocess, ~17,385 CPU-seconds)
- Source screenshots: 5,653 files in `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\`
- Manifest last processed_at: `2026-10-15T08:42:46Z`
- **Do not stop this process.**

**Important:** The prior run (`run_3000.log`) was configured with `--limit 3000` but was replaced or restarted with `--limit 1000`. The current manifest contains 850 done entries. Verify the final count once the process exits.

---

## 9. Model Training and Evaluation Status

### Trained model

| Property | Value | Evidence |
|---|---|---|
| Version | 1.0.0 | `training_log.json` |
| Trained at | 2026-10-13T23:08:37Z | `training_log.json` |
| Algorithm | Random Forest | `train_model.py`, README |
| Training rows | 40 (LOW=30, MEDIUM=8, HIGH=2) | `training_log.json` |
| Test rows | 10 (LOW=8, MEDIUM=2, HIGH=0) | `training_log.json` |
| CV folds | 2 | `training_log.json` |
| CV macro F1 | 0.353 ± 0.075 | `training_log.json` |
| Test macro F1 | 0.444 | `training_log.json` |
| Test accuracy | 0.80 | `training_log.json` |
| LOW F1 | 0.889 | `training_log.json` |
| MEDIUM F1 | 0.000 | `training_log.json` — confirmed; caused by feature-class overlap |
| HIGH support in test | 0 | `training_log.json` — both HIGH rows in training fold |

### Baseline comparison

| Classifier | Accuracy | Macro F1 | Evaluation set |
|---|---|---|---|
| Rule baseline | 0.80 | 0.444 | Same 10-row held-out test fold |
| Random Forest (ML) | 0.80 | 0.444 | Same 10-row held-out test fold |

Both predict only LOW on the test set. The identical scores reflect feature-class overlap, not coincidence. Reproducible with `python -m backend.services.baseline_eval`.

**Macro F1 clarification:** sklearn `f1_score(average="macro")` over only observed classes (LOW + MEDIUM) = 0.444 = (0.889 + 0.000) / 2. The classification report's "macro avg" includes HIGH (0 support) and reports 0.296. The 0.444 figure is the one used in the README comparison table.

### Outstanding evaluation gaps

- HIGH class cannot be evaluated (0 test examples)
- CRITICAL class cannot be evaluated (0 annotated examples)
- No confidence intervals (single split, n=10 test)
- No evaluation after annotation corrections (PENDING)
- No evaluation on OCR-derived candidates beyond the 50-row ground truth

---

## 10. Privacy and Security Verification Status

| Feature | Status | Evidence / Notes |
|---|---|---|
| PII tokenization applied at HIGH/MEDIUM | DONE | `tokenizer.py`; `test_tokenizer.py` (20,897 bytes, comprehensive tests) |
| `token_map` excluded from API response | DONE | Code review + test coverage |
| Tokenization skipped at LOW + confidence ≥ 0.75 | DONE — with known gap | See privacy gap below |
| Fail-safe tokenization (model unavailable) | DONE | `tokenizer.py` |
| Raw OCR text not stored in `analysis_log` | DONE | `database.py` schema — column not present |
| `caprlm.db` excluded from git | DONE | `.gitignore` verified |
| Model artifact excluded from git | DONE | `.gitignore` — `*.joblib` excluded |

### Known privacy gap (documented in README)

When `risk_level = LOW` and `confidence ≥ 0.75`, tokenization is skipped. The raw `detection_results.matches` lists in the API JSON response contain unmasked entity strings (email addresses, phone numbers, etc.). The frontend (`app.js`) does not display `matches` to end users (reads only `.count`), but any API caller reading the raw JSON response receives unmasked values.

This is a prototype limitation. The 0.75 threshold is not empirically optimised. Mitigation options (always tokenize, raise threshold, add a separate masking pass) are design decisions for a future iteration.

### Unimplemented privacy features

- No actual image pixel transformation (blur/block)
- No persistent reversible tokenization across requests
- No privacy leakage simulation or re-identification experiments
- No retrieval utility evaluation after privacy protection

---

## 11. Backend, Frontend, and Integration Status

### Backend API

| Endpoint | Status | Notes |
|---|---|---|
| `GET /` | DONE | Health check + model status |
| `POST /analyze` | DONE | Full pipeline; OCR → detection → risk → tokenize → protect |
| `POST /feedback` | DONE | Records to `feedback_log`; triggers threshold recalibration |
| `POST /train` | DONE | Retrains from annotation CSV; writes to `training_run` table |
| `GET /model-info` | DONE | Returns bundle metadata |

### SQLite schema

Tables: `user_profile`, `feedback_log`, `analysis_log`, `training_run`. Created automatically on startup via `init_db()`. WAL mode enabled. Legacy JSON profiles under `backend/data/profiles/` migrated non-destructively on startup.

### Frontend

Files: `frontend/index.html`, `frontend/app.js`, `frontend/style.css`. Reads only `.count` from `detection_results`; does not display raw `matches`. Integration with `/analyze`, `/feedback`, `/model-info` endpoints.

**NEEDS_VERIFICATION:** Frontend has not been manually tested in this session. Verify end-to-end flow with `uvicorn backend.main:app --reload` before marking integration DONE.

---

## 12. Testing and Performance Status

### Test suite

| Test file | Tests (approx) | Coverage area |
|---|---|---|
| `test_api.py` | ~30 | FastAPI endpoints, full request/response cycle |
| `test_baseline_eval.py` | ~25 | Baseline comparison logic, split consistency |
| `test_database.py` | ~20 | SQLite schema, migration, WAL |
| `test_feature_extraction.py` | ~10 | Feature vector construction |
| `test_process_screenshots.py` | ~35 | OCR pipeline, manifest, resume, timeout |
| `test_protection.py` | ~10 | Risk-to-action mapping |
| `test_risk_prediction.py` | ~15 | ML prediction, confidence, fallback |
| `test_rule_baseline.py` | ~10 | Rule-based classifier |
| `test_sensitive_detection.py` | ~15 | Regex patterns, edge cases |
| `test_tokenizer.py` | ~40 | Token generation, risk gating, restoration |
| `test_train_model.py` | ~10 | Training pipeline, encoder leakage |
| `test_user_profile_service.py` | ~20 | Threshold recalibration, feedback |

**Total: 209 tests, 0 failures** (verified 2026-10-15 via `pytest --collect-only -q`)

### OCR performance benchmark (from README)

| Metric | Value |
|---|---|
| Images evaluated | 20 |
| Input resolution | 1920×1080 → 1600×900 |
| Mean speedup (with outlier) | 5.94× |
| Mean speedup (without 1-image outlier) | ~1.17× |
| Detection loss from resize | ~5% |

One extreme outlier (1337.6 s → 14.0 s) dominates the mean. The resize optimisation reliably eliminates catastrophic stalls but provides modest improvement on typical images.

### MobileCLIP-S0 benchmark (Member 1)

| Device | Median encode time |
|---|---|
| RTX 2050 (back-to-back) | 14.5 ms |
| CPU | 41.0 ms |
| RTX 2050 (500 ms pause, idle GPU) | 76.6 ms |

Idle GPU clocks dominate latency when sampling at low frequency.

---

## 13. Member Responsibilities and Dependencies

| Member | Role | Key deliverable | Status |
|---|---|---|---|
| Member 1 (contributor unknown) | MobileCLIP-S0 feasibility study | `Member1/monitor.py`, `bench_raw.py`, benchmarks | DONE (prototype) |
| Member 2 (SashiniWijesingha) | CAPRLM backend, ML pipeline, API, frontend | Full CAPRLM system | DONE (prototype); annotation corrections PENDING |
| Member 3 | UNKNOWN | UNKNOWN | NEEDS_VERIFICATION — no directory in repo |
| Member 4 | UNKNOWN | UNKNOWN | NEEDS_VERIFICATION — no directory in repo |

### Inter-member dependencies

- Member 1 → Member 2: MobileCLIP visual encoding could replace or augment OCR-only feature extraction, but no integration exists currently.
- Member 2 → Members 3/4: Unknown — repo shows no Member3/Member4 work. Must be clarified.
- All members → research report: evaluation metrics must be consistent across the report. Member 2's metrics are documented here; others are unknown.

---

## 14. Research Limitations and Unimplemented Requirements

### Confirmed unimplemented proposal requirements

| Requirement | Status | Note |
|---|---|---|
| Computer vision / pixel-level feature extraction | NOT IMPLEMENTED | Pipeline is text-only (OCR) |
| Semantic analysis (embeddings, topic models) | NOT IMPLEMENTED | `application_context` is label-encoded as a single integer |
| Trained NER model | NOT IMPLEMENTED | Entity detection uses regex patterns only |
| Actual image transformation (blur, block) | NOT IMPLEMENTED | Protection module returns action name only |
| Persistent reversible tokenization | NOT IMPLEMENTED | Token map is ephemeral (per-request only) |
| Privacy leakage simulation / re-identification experiments | NOT IMPLEMENTED | No adversarial evaluation performed |
| Retrieval utility evaluation | NOT IMPLEMENTED | No downstream retrieval component |
| Continual learning | NOT IMPLEMENTED | Personalisation = threshold recalibration only |
| Masking / anonymization / pseudonymization baselines | NOT IMPLEMENTED | Only rule-based risk scoring baseline implemented |

### Fundamental evaluation limitations

1. **n=50 ground truth** — a 10-row test set; one misclassification changes macro F1 by ≈0.044.
2. **Class imbalance** — LOW=76%, CRITICAL=0%; HIGH has 0 test examples.
3. **Feature-class overlap** — 6 of 10 MEDIUM rows have all-zero entity flags identical to LOW rows; MEDIUM F1 = 0.000 is expected, reproducible, and documented.
4. **OCR-invisible risk** — several MEDIUM/HIGH labels were assigned for visual reasons that OCR cannot encode as features.
5. **Single annotator** — no inter-annotator agreement metric computed.
6. **No generalisation** — test set not withheld before any design decisions; no external validation set.
7. **Personalisation = threshold adjustment** — cannot correct fundamental feature-class mismatches per user.

---

## 15. Evidence and Verification References

| Claim | Evidence source | How to verify |
|---|---|---|
| 209 tests pass | `pytest --collect-only -q` output 2026-10-15 | `cd Member2 && .venv\Scripts\python -m pytest backend/tests/ -q` |
| Model version 1.0.0, trained 2026-10-13 | `Member2/backend/data/training_log.json` | Read file directly |
| Test macro F1 = 0.444 | `training_log.json` → `test_macro_f1` | Read file directly |
| MEDIUM F1 = 0.000 | `training_log.json` → classification report | Read file directly |
| Baseline eval uses same fold | commit `156a2f3`, `baseline_eval.py` lines 120–126 | `git show 156a2f3 --stat` |
| OCR: 850 done, 0 failed | `processing_output/manifest.json` parsed 2026-10-15 | Read manifest, count by status |
| 16 anomalous annotation rows | `docs/consolidated_annotation_review_DRAFT.md` | Read document summary table |
| Protected files unmodified | `git status` on specific paths 2026-10-14 | `git diff HEAD -- Member2/backend/data/CAPRLM_annotation_50.csv` |
| Privacy gap (LOW high-conf leaks matches) | `Member2/README.md` T5 section + `tokenizer.py` | Read README + search `tokenizer.py` for skip condition |
| Annotation guidelines not yet approved | `annotation_guidelines_DRAFT.md` header | Read document status field |

---

## 16. Session Handover Instructions

### Before starting work

```
cd C:\Users\ASUS\OneDrive\Desktop\J26-IT-308
git status
git log --oneline -10
```

Check for running Python processes before touching data files:
```powershell
Get-Process python* | ForEach-Object {
    $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
    Write-Host "PID $($_.Id) :: $cmd"
}
```

If the OCR pipeline is running, do not stop it. Do not write to `processing_output/`.

### Activating the environment

```powershell
cd Member2
.\.venv\Scripts\Activate.ps1   # or activate
```

### Running the test suite (safe, read-only)

```powershell
cd Member2
.\.venv\Scripts\python -m pytest backend/tests/ -q
```

### Running baseline evaluation (safe, read-only)

```powershell
cd Member2
.\.venv\Scripts\python -m backend.services.baseline_eval
```

### Starting the API server (only when explicitly needed)

```powershell
cd Member2
uvicorn backend.main:app --reload
```

Then open `Member2/frontend/index.html` in a browser.

### Retraining the model (only after annotation corrections are approved)

```powershell
cd Member2
.\.venv\Scripts\python -m backend.services.train_model
```

This overwrites `caprlm_model.joblib` and appends to `training_log.json`. Do not run without explicit authorization.

---

## 17. Last Verified Date and Outstanding Verification Needs

**Last full verification:** 2026-10-15

### Items verified in this session

- Git log, branch, status (clean, sashi, up to date with origin/sashi)
- Test count: 209 collected, 209 passed
- Protected file sizes and last-write timestamps
- OCR manifest: 850 done, 0 failed, last processed 2026-10-15T08:42:46Z
- OCR process: running (PIDs 23292, 1252)
- Annotation review documents: exist in `backend/data/docs/`, DRAFT status, not approved
- Training log: v1.0.0, trained 2026-10-13, metrics match README
- `baseline_eval.py`: read-only confirmed (no write operations)
- Member directories: only Member1 and Member2 present

### Items that could NOT be verified in this session

| Item | Reason |
|---|---|
| Members 3 and 4 work | No directories found in repo; may be on separate branch or repo |
| Frontend end-to-end functionality | Server not started; not tested manually |
| Annotation correctness for 16 flagged rows | Requires human to visually inspect screenshots |
| Annotation policy decisions | Requires supervisor approval |
| Final OCR manifest integrity (post-completion) | OCR still running; audit blocked |
| Whether `caprlm.db` is consistent | File is gitignored; not inspected |
| MobileCLIP integration into CAPRLM pipeline | No integration code found; not in scope for Member 2 |

### Next verification checkpoint

When the OCR process exits (PID 23292), re-read `processing_output/manifest.json` and `candidates.csv` to confirm:
- Total entries = target (1000)
- All entries status = "done" (or document failures)
- `candidates.csv` row count = done count
- No duplicate `screenshot_id` values
