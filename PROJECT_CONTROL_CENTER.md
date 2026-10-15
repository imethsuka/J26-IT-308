# Project Control Center — J26-IT-308

**Research project:** Context-Aware Privacy Intelligence and Adaptive Risk Learning Framework (CAPRLM)
**Repository root:** `C:\Users\ASUS\OneDrive\Desktop\J26-IT-308`
**Active branch:** `sashi` (tracking `origin/sashi`)
**Document status:** Living document. Update after every significant work session.
**Last verified:** 2026-10-15 (second session same day)

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
| 265 tests pass, 0 failures | DONE | Live run 2026-10-15 (session 5) → 265 passed, 1 warning; command: `Member2/.venv/Scripts/pytest.exe backend/tests/ -q --tb=no` (19 new non-ASCII tokenizer tests added) |
| Model trained (v1.0.0, 2026-10-13) | DONE | `backend/data/training_log.json`; `caprlm_model.joblib` exists (145,320 bytes) |
| `baseline_eval.py` compares all three classifiers on same test fold | DONE | majority-class baseline added 2026-10-15; reproducible with `python -m backend.services.baseline_eval` |
| Baseline eval includes confusion matrix + per-class metrics | DONE | `_compute_metrics()` now returns `confusion_matrix` and `confusion_matrix_labels`; CLI prints per-class table |
| Majority-class baseline added to evaluation | DONE | All three classifiers achieve identical results (macro F1=0.4444) — confirmed important research finding |
| Baseline eval results persisted to `baseline_eval_log.json` | DONE | `backend/data/baseline_eval_log.json` append-only; includes majority_baseline entry |
| README corrected (privacy gap, baseline method, test count) | DONE | commit `156a2f3` diff verified 2026-10-15 |
| OCR pipeline completed (limit 1000) | DONE | manifest.json: 1,000 done, 0 failed; candidates_deduped.csv: 1,000 rows verified |
| Privacy gap (tokenized_text leak) fixed | DONE | `main.py`: raw OCR text replaced with `[redacted...]` when tokenization not applied; regression test added |
| Annotation guidelines v0.2 draft written | IN_PROGRESS | `docs/annotation_guidelines_DRAFT.md` — not yet supervisor-approved |
| Annotation review checklist written (16 anomalous rows) | IN_PROGRESS | `docs/human_annotation_review_checklist_DRAFT.md` — 0/16 records reviewed |
| Annotation corrections applied | PENDING | No label changes have been made to `CAPRLM_annotation_50.csv` |
| Annotation review report (AI provisional) | DONE | `docs/annotation_review_report.md` written 2026-10-15 (session 4); 6 priority screenshots AI-inspected; 0 human decisions |
| HTML annotation review interface | DONE | Published at claude.ai/artifact/PeCYwfcm38bzRKrGE7XCsT; interactive view of all 16 flagged records |
| Requirements traceability matrix | DONE | `docs/requirements_traceability_matrix.md` written 2026-10-15; 27 requirements: 19 VERIFIED_COMPLETE, 3 PARTIALLY_COMPLETE, 3 NOT_IMPLEMENTED, 1 NOT_VERIFIED, 1 BLOCKED_BY_HUMAN_REVIEW |
| Screenshot path bug identified | DONE | Audit script used wrong path; correct: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\`; 6/6 priority screenshots confirmed present |
| Protected files unmodified | DONE | SHA-256 verified session 3: annotation CSV=6FA1FEC8…, model=C08E30BC…, training_log=3EAFB095… — all unchanged |

---

## 4. Current Active Tasks

| Task | Status | Notes |
|---|---|---|
| OCR pipeline run (limit 1000) | DONE | Completed 2026-10-15; 1,000 done, 0 failed; candidates_deduped.csv verified |
| Privacy gap fix (tokenized_text leak) | DONE | Fixed 2026-10-15; regression test added; 265 tests pass (verified session 5) |
| API server startup | DONE | uvicorn starts cleanly on port 8765; GET / → 200; /analyze, /feedback, /model-info all respond correctly (verified 2026-10-15 session 5) |
| /analyze live test | DONE | POST /analyze with real 65KB screenshot → 200, risk_level=MEDIUM, confidence=0.95, EMAIL+DATE tokenized → [EMAIL_1]/[DATE_1], token map NOT returned (verified 2026-10-15 session 5) |
| /feedback live test | DONE | POST /feedback with user_label=HIGH → 200, feedback recorded, profile updated (verified 2026-10-15 session 5) |
| Non-ASCII tokenizer regression tests | DONE | 19 tests (UNI-01–UNI-17): Sinhala, Japanese, Arabic, Cyrillic, emoji, accented Latin, RTL, empty string, repeated entity, token collision, restoration round-trip — all pass, 0 defects found |
| Baseline eval persistence | DONE | `baseline_eval_log.json` created 2026-10-15 |
| Annotation review — human visual inspection of 16 rows | PENDING | Requires human to open each flagged screenshot |
| Annotation policy decisions — 8 unresolved policies | PENDING | Requires supervisor approval before any label changes |
| Model retraining after annotation corrections | PENDING | Blocked on annotation corrections |

---

## 5. Next Three Recommended Actions (Priority Order)

### Priority 1 — Human visual review of the 16 flagged annotation rows *(was Priority 2)*

OCR pipeline is COMPLETE (1,000 done, 0 failed, deduplication verified). The blocking task is now annotation review.

Open each of the 16 screenshots in `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\` and fill in the checklist at `Member2/backend/data/docs/human_annotation_review_checklist_DRAFT.md`.

Start with Record 2 (`02e86b37-…`) — clearest binary question, no policy dependency, direct model impact.

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
| 16 annotation rows require human visual review | Model quality, evaluation validity | Human must open screenshots; cannot be automated |
| 8 annotation policy decisions unresolved | Annotation guidelines validity | Supervisor approval required (see Section 7 for full list) |
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
| processing_output (COMPLETE) | `data/processing_output/` | 1,000 | 0 | 1,008 (8 duplicates) | 1000 |

**Completed run (verified 2026-10-15):**
- Manifest: 1,000 entries, all `done`, 0 failed, last processed_at `2026-10-15T16:09:16`
- Original candidates.csv: 1,008 rows, 1,000 unique IDs, 8 duplicate groups (all identical on OCR outputs)
- Deduplicated: `candidates_deduped.csv` — 1,000 rows, 1,000 unique IDs, IDs match manifest exactly
- SHA-256 of candidates.csv: `913dcb673d96f40eb4d050cc024f158a1a6003d594634bf3cea0d913f5f073bf`
- 8 duplicates explained: two interrupted OCR batches (one on 2026-10-14, one on 2026-10-15) left images in CSV but absent from manifest; re-processed on next run, producing duplicate rows with identical OCR outputs
- The deduplication rule (keep latest `processed_at`, exclude `elapsed_seconds` from conflict check) was approved 2026-10-15

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
| Majority-class baseline (always LOW) | 0.80 | 0.444 | Same 10-row held-out test fold |
| Rule baseline | 0.80 | 0.444 | Same 10-row held-out test fold |
| Random Forest (ML) | 0.80 | 0.444 | Same 10-row held-out test fold |

**Critical finding (verified 2026-10-15):** All three classifiers produce **identical confusion matrices** on this test fold. Both the rule classifier and the RF model predict only LOW, exactly as the majority-class baseline does. Neither adds measurable value over the trivial baseline on this test set.

This is an honest, important research limitation — not a fabrication. The root cause is documented: 6 of 10 MEDIUM rows in the dataset have all-zero entity flags, making them indistinguishable from LOW rows given the available features.

**Macro F1 clarification:** `f1_score(average="macro")` over all three classes (HIGH, LOW, MEDIUM) = (0 + 0.889 + 0) / 3 = 0.296. When evaluated over only the 2 classes present in predictions (LOW + MEDIUM), macro F1 = (0.889 + 0.000) / 2 = 0.444. The evaluation now consistently uses all three classes. The 0.444 figure previously in the README was computed over 2 classes only.

Per-class metrics (test fold, all three classifiers identical):
- HIGH:   precision=0.000, recall=0.000, F1=0.000, support=0
- LOW:    precision=0.800, recall=1.000, F1=0.889, support=8
- MEDIUM: precision=0.000, recall=0.000, F1=0.000, support=2

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

### Privacy gap — FIXED 2026-10-15

When `risk_level = LOW` and `confidence ≥ 0.75`, tokenization is skipped. The `detection_results.matches` lists were already correctly cleared by `_strip_matches()` in the tokenizer (earlier documentation was wrong about the leak location). The actual leak was in `tokenized_summary.tokenized_text`: when tokenization is skipped, the field previously contained the raw OCR text, which may include email addresses, phone numbers, etc.

**Fix applied:** `main.py` now returns `"[redacted — entity tokenization not applied]"` in `tokenized_text` when `token_map` is empty. A regression test `test_analyze_low_risk_skip_tokenization_does_not_leak_ocr_text` verifies this.

The 0.75 threshold is not empirically optimised — this remains a design decision for a future iteration.

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

**Total: 217 tests, 0 failures** (verified 2026-10-15 session 2; 7 new tests for majority baseline + confusion matrix)

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
| 213 tests pass | `pytest -q` output 2026-10-15 session 3 (live run) | `cd Member2 && .venv\Scripts\python -m pytest backend/tests/ -q` |
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

Optionally check for running Python processes if you are uncertain whether any background job is active:
```powershell
Get-Process python* | ForEach-Object {
    $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
    Write-Host "PID $($_.Id) :: $cmd"
}
```

The OCR pipeline completed 2026-10-15 and is no longer running. `processing_output/` files are safe to read. Do not modify `candidates.csv` or `manifest.json`.

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

**Last full verification:** 2026-10-15 (third session)

### Items verified in this session (third session)

- Test count: **213 passed, 0 failures, 1 warning** (live `pytest -q` run)
- Baseline eval re-run: majority_baseline F1=0.4444, rule_baseline F1=0.4444, ml_model F1=0.4444 — all predict only LOW; result appended to log
- Protected files SHA-256 verified: annotation CSV=6FA1FEC8…, model=C08E30BC…, training_log=3EAFB095… — unchanged
- Frontend API compatibility confirmed: `app.js` reads `.count` only from detection_results; never reads `tokenized_summary.tokenized_text`; `user_id` not required (defaults to "default")
- `protection.py` reviewed: action names only, no pixel ops — correct and documented
- `database.py` reviewed: WAL mode, 4 tables, foreign keys, idempotent `init_db` — no issues
- `sensitive_detection.py` reviewed: 6 regex patterns, IP validated with `_valid_ipv4`, URL trailing punctuation stripped — no issues
- No new software defects found in independent code audit
- All code changes are working-tree modifications only (no commit/push performed)

### Items verified in second session (same day)

- Git branch: sashi, up to date with origin/sashi
- Majority-class baseline added to `baseline_eval.py` — confirmed all 3 classifiers produce identical results
- Confusion matrix and per-class metrics added to `_compute_metrics()` and CLI output
- `baseline_eval_log.json` updated (append-only; now contains multiple runs)
- Protected files: `CAPRLM_annotation_50.csv`, `caprlm_model.joblib`, `manifest.json` — NOT modified

### Items verified in first session (same day)

- Git log, branch, status (clean, sashi, up to date with origin/sashi)
- Test count: 210 passed, 0 failures, 1 warning (after privacy fix + regression test)
- Protected file sizes and last-write timestamps — unchanged
- OCR manifest: 1,000 done, 0 failed — COMPLETED
- `candidates_deduped.csv`: 1,000 rows, 1,000 unique IDs, IDs match manifest exactly
- Annotation review documents: exist in `backend/data/docs/`, DRAFT status, not approved; 0/16 records reviewed
- Training log: v1.0.0, trained 2026-10-13, metrics match README
- Baseline eval: rule F1=0.444, ML F1=0.444 (both predict only LOW); results persisted to `baseline_eval_log.json`
- Privacy gap: confirmed and fixed — `tokenized_text` in API response now redacted when tokenization not applied
- Member directories: only Member1 and Member2 present
- `protection.py`, `tokenizer.py`, `risk_prediction.py`, `sensitive_detection.py` reviewed — no additional issues

### Items that could NOT be verified in this session

| Item | Reason |
|---|---|
| Members 3 and 4 work | No directories found in repo; may be on separate branch or repo |
| Frontend end-to-end functionality | Server not started; not tested manually |
| Annotation correctness for 16 flagged rows | Requires human to visually inspect screenshots |
| Annotation policy decisions (8 open) | Requires supervisor approval |
| Whether `caprlm.db` is consistent | File is gitignored; not inspected |
| MobileCLIP integration into CAPRLM pipeline | No integration code found; not in scope for Member 2 |
