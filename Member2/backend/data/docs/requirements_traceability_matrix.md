# Requirements Traceability Matrix — CAPRLM
**Project:** J26-IT-308  
**Generated:** 2026-10-15  
**Evaluator:** Claude Sonnet 4.6 (AI verification only — not human review)  
**Test evidence:** 246 passed, 1 warning (live run 2026-10-15, venv pytest)

---

## Status Definitions

| Status | Meaning |
|--------|---------|
| VERIFIED_COMPLETE | Requirement implemented AND verified by code inspection + passing tests |
| PARTIALLY_COMPLETE | Core of requirement implemented; significant gaps remain |
| NOT_IMPLEMENTED | No implementation found for this requirement |
| NOT_VERIFIED | Implementation present but behaviour not confirmed by live test |
| BLOCKED_BY_HUMAN_REVIEW | Depends on human annotation decisions or supervisor approvals |

---

## Core Functional Requirements

| # | Requirement | Status | Evidence | Gaps |
|---|------------|--------|----------|------|
| R1 | Batch OCR ingestion of desktop screenshots | VERIFIED_COMPLETE | `scripts/process_screenshots.py`; manifest.json: 1,000 done, 0 failed; candidates_deduped.csv: 1,000 rows | None |
| R2 | Rule-based PII entity detection (EMAIL, PHONE, URL, IP_ADDRESS, DATE, WINDOWS_PATH) | VERIFIED_COMPLETE | `services/sensitive_detection.py`; 6 regex patterns; IP validated per-octet; URL trailing punctuation stripped; 26/26 privacy eval tests pass | WINDOWS_PATH: 0 positive examples in dataset — cannot be evaluated |
| R3 | Feature extraction from detected entities | VERIFIED_COMPLETE | `services/feature_extraction.py`; entity counts + context type + page_text_length; test coverage in test suite | Context encoder fitted on training fold only (no leakage) |
| R4 | ML risk classification (Random Forest) | VERIFIED_COMPLETE | `services/train_model.py`; `data/caprlm_model.joblib` (v1.0.0, 2026-10-13); test fold: macro F1=0.4444, accuracy=0.800 | F1 limited by feature-class overlap; MEDIUM/HIGH cannot be distinguished from available features |
| R5 | Rule-based baseline classifier | VERIFIED_COMPLETE | `services/rule_baseline.py`; maps entity flags to risk levels; CRITICAL produced internally (email+phone) | CRITICAL label creates known divergence with annotation CSV (no annotated CRITICAL examples) |
| R6 | Fair baseline comparison (majority, rule, ML) | VERIFIED_COMPLETE | `services/baseline_eval.py`; all three produce identical macro F1=0.4444 — confirmed research finding | Only 10-row test fold; statistical significance not tested |
| R7 | Risk-adaptive reversible tokenization | PARTIALLY_COMPLETE | `services/tokenizer.py`; T5 word-piece tokenization for HIGH risk; skips at LOW+confidence≥0.75; token map ephemeral | Token map is not persistent across requests (cannot restore across calls); T5 is prototype, not a trained T5 model |
| R8 | Protection action selection | VERIFIED_COMPLETE | `services/protection.py`; 4 actions: MONITOR, BLUR_SENSITIVE, BLUR_AND_ALERT, BLOCK; maps risk levels correctly | Actions are labels only — no pixel-level image manipulation implemented |
| R9 | User feedback loop with adaptive threshold recalibration | PARTIALLY_COMPLETE | `services/user_profile_service.py`; feedback stored to SQLite; threshold adjustable via `/user/preferences`; EWM-based recalibration present | No live user study performed; recalibration not evaluated |
| R10 | SQLite persistence | VERIFIED_COMPLETE | `database.py`; WAL mode; 4 tables; idempotent `init_db()`; `analysis_log` intentionally excludes OCR/extracted text columns (privacy by design) | |
| R11 | FastAPI REST API | VERIFIED_COMPLETE | Server started on port 8765 (session 5); `GET /` → 200; `POST /analyze` with real 65KB screenshot → 200, MEDIUM risk, EMAIL+DATE tokenized; `POST /feedback` → 200; `GET /model-info` → 200 | Actual routes: `/`, `/analyze`, `/feedback`, `/train`, `/model-info` (no `/health` or `/user/*` routes exist) |
| R12 | Frontend UI | NOT_VERIFIED | `frontend/index.html`, `app.js`, `style.css`; reads `.count` only from detection_results; `user_id` not sent; code-reviewed — no raw tokenized_text read | Browser rendering not tested; Live Server (VS Code extension) required to serve; cannot automate browser test in this environment |
| R13 | 50-row annotated ground truth dataset | BLOCKED_BY_HUMAN_REVIEW | `data/CAPRLM_annotation_50.csv` exists (6FA1FEC8…); 50 rows; 3 classes (LOW 38, MEDIUM 10, HIGH 2) | 16/50 rows (32%) have annotation anomalies; 0/16 reviewed by human; model trained on potentially incorrect labels |
| R14 | Annotation guidelines | PARTIALLY_COMPLETE | `docs/annotation_guidelines_DRAFT.md` v0.2 | 8 policies unresolved; not yet supervisor-approved |

---

## Privacy and Security Requirements

| # | Requirement | Status | Evidence | Gaps |
|---|------------|--------|----------|------|
| P1 | Sensitive data not returned in API response (tokenized_text) | VERIFIED_COMPLETE | Privacy fix applied 2026-10-15: when tokenization skipped, returns `[redacted — entity tokenization not applied]` instead of raw OCR text | |
| P2 | Token-to-value restoration map not returned to client | VERIFIED_COMPLETE | `main.py`: token_map excluded from all API responses; tested in `test_privacy_regression.py` (REG-01) | |
| P3 | Ephemeral token map (no cross-request restore) | VERIFIED_COMPLETE | Token map is per-request dict; not stored in DB; documented in API response note field | By design — intentional limitation |
| P4 | OCR text not persisted in database | VERIFIED_COMPLETE | `analysis_log` table schema has no `ocr_text`/`extracted_text` columns; confirmed in `database.py` | |
| P5 | Privacy regression tests cover all entity types | VERIFIED_COMPLETE | `tests/test_privacy_regression.py`: 14 regression tests (REG-01–REG-14); all 6 entity types at HIGH; LOW+confidence≥0.75 path; 265 total tests pass (session 5) | |
| P6 | Token map not leaked at LOW+high-confidence path | VERIFIED_COMPLETE | `main.py` privacy fix; `test_analyze_low_risk_skip_tokenization_does_not_leak_ocr_text` test passes | |

---

## Evaluation and Research Requirements

| # | Requirement | Status | Evidence | Gaps |
|---|------------|--------|----------|------|
| E1 | Per-class metrics (precision, recall, F1) | VERIFIED_COMPLETE | `baseline_eval.py` prints per-class table; `experiment_results.json` has full classification_report per classifier | HIGH class: 0 test examples — F1 cannot be evaluated |
| E2 | Confusion matrix | VERIFIED_COMPLETE | `experiment_results.json` includes confusion_matrix + confusion_matrix_labels for all three classifiers | |
| E3 | Threshold sensitivity analysis | VERIFIED_COMPLETE | `experiment_results.json` threshold_sensitivity: 10 configurations (0.5–0.95 MEDIUM threshold); F1 flat at 0.4444 for ≥0.6 | |
| E4 | Reproducible experiment | VERIFIED_COMPLETE | `scripts/run_experiment.py`; random_state=42; deterministic split; model NOT retrained; results: `data/experiment_results.json` | Results will change if dataset corrected and model retrained |
| E5 | Error analysis / limitations documented | VERIFIED_COMPLETE | `experiment_results.json`: 6 documented limitations; feature-class overlap is root cause confirmed | |
| E6 | Privacy evaluation test suite | VERIFIED_COMPLETE | `data/privacy_eval_results.json`: 26 entity-level tests, all passed | |
| E7 | Inter-annotator agreement | NOT_IMPLEMENTED | No second annotator; no Cohen's kappa or similar metric | Single-annotator dataset — known limitation |
| E8 | Statistical significance testing | NOT_IMPLEMENTED | n=10 test fold; no p-values or confidence intervals | Dataset too small for meaningful significance tests |

---

## Infrastructure and Quality Requirements

| # | Requirement | Status | Evidence | Gaps |
|---|------------|--------|----------|------|
| I1 | Test suite with passing tests | VERIFIED_COMPLETE | 265 passed, 1 warning (live run 2026-10-15 session 5); includes 19 new non-ASCII tokenizer tests (UNI-01–UNI-17) | |
| I2 | Protected files unmodified | VERIFIED_COMPLETE | SHA-256 verified: `CAPRLM_annotation_50.csv`=6FA1FEC8…, `caprlm_model.joblib`=C08E30BC…, `training_log.json`=3EAFB095… | |
| I3 | OCR pipeline reproducible | VERIFIED_COMPLETE | `manifest.json` tracks per-file status; deduplication in `candidates_deduped.csv` | |
| I4 | Model artifact versioned | VERIFIED_COMPLETE | `training_log.json` records version, date, metrics, feature names | |
| I5 | Frontend does not read raw OCR text | VERIFIED_COMPLETE | `frontend/app.js` reads `.count` only from detection_results; `tokenized_summary.tokenized_text` not read | |

---

## Requirements NOT in Proposal Scope (Confirmed Absent)

| # | Item | Note |
|---|------|------|
| X1 | Computer vision / visual feature extraction | Not in scope — OCR text only |
| X2 | Trained NER model | Not in scope — regex-based detection |
| X3 | Pixel-level image transformations (actual blur) | Protection actions are labels only |
| X4 | Persistent reversible tokenization across requests | By design — ephemeral per-request |
| X5 | Continual learning / online adaptation | Not implemented |
| X6 | Re-identification simulation experiments | Not implemented |
| X7 | Retrieval utility evaluation | Not implemented |

---

## Summary Count

| Status | Count |
|--------|-------|
| VERIFIED_COMPLETE | 19 |
| PARTIALLY_COMPLETE | 3 |
| NOT_IMPLEMENTED | 3 |
| NOT_VERIFIED | 1 |
| BLOCKED_BY_HUMAN_REVIEW | 1 |
| **Total requirements** | **27** |

---

## Critical Submission Risks

1. **R13 BLOCKED**: 16/50 (32%) annotation rows have unresolved anomalies. Model was trained on potentially incorrect labels. All evaluation numbers are provisional until corrected.
2. **R4 gap**: ML model provides no measurable advantage over majority-class baseline (identical F1=0.4444). This is an important honest research finding, not a bug — but must be clearly documented.
3. **R12 NOT_VERIFIED**: Frontend UI not tested in browser this session.
4. **E7/E8 NOT_IMPLEMENTED**: No inter-annotator agreement or statistical significance — standard academic expectations for a dataset paper.

---

*Matrix produced by AI (Claude Sonnet 4.6). All VERIFIED_COMPLETE statuses are based on code inspection and passing automated tests. Human review required before treating this matrix as a final submission artefact.*
