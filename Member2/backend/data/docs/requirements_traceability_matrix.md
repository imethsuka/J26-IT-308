# Requirements Traceability Matrix — CAPRLM
**Project:** J26-IT-308  
**Generated:** 2026-10-15 · **Updated:** 2026-10-20 (latency results, annotation guidelines v0.3, session 9 re-verification)  
**Evaluator:** Claude Sonnet 4.6 (AI verification only — not human review)  
**Test evidence:** 381 passed, 1 warning (live run 2026-10-20, venv pytest — re-confirmed; matches 2026-10-15 count)  
**Live API evidence:** POST /analyze with entity image → HTTP 200, risk_level=MEDIUM, confidence=0.86, redaction_status=redacted, regions_redacted=2, 2647 changed pixels confirmed, GET /redacted-image → valid PNG; error handling verified (missing field → 4xx, invalid label → 422); all routes verified (2026-10-15)

---

## Verification Level Key

The statuses below distinguish implementation evidence quality:

| Level | Meaning |
|-------|---------|
| Code exists | Function/class present in source |
| Unit tests | Automated tests pass for this component in isolation |
| API integration tested | Automated tests exercise the route via TestClient |
| Live API tested | Real HTTP call confirmed in a running server process |
| Browser tested | Manually confirmed in a browser with VS Code Live Server |
| Research evaluated | Effectiveness measured experimentally on real data |

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
| R8 | Protection action selection with pixel redaction | VERIFIED_COMPLETE | `services/redaction.py`; actual Gaussian blur applied to sensitive text regions (PIL GaussianBlur r=15); BLUR_SENSITIVE targets entity bboxes, BLUR_AND_ALERT blurs all text, BLOCK fills black; integrated into `/analyze`; 15 pixel-level tests pass; **live API test (2026-10-15):** entity image → protection_action=BLUR_SENSITIVE, regions_redacted=2, 2647 changed pixels confirmed, original file unchanged, redacted PNG valid | Redacted image held in process memory; not persisted across restarts; verification levels: code ✓, unit tests ✓, API integration tests ✓, live API test ✓, browser test ✗ |
| R9 | User feedback loop with adaptive threshold recalibration | VERIFIED_COMPLETE | `services/user_profile_service.py`; feedback stored to SQLite; windowed recalibration (last 50 records); poisoning guard (max 3 per screenshot); adaptation_log table; rollback to previous threshold; `/user/rollback` and `/user/calibration-history` routes; **live API test (2026-10-15):** POST /feedback → 200 feedback_count=1; GET /calibration-history → 200; POST /rollback → 200 | No live user study; recalibration evaluated on synthetic test data only; verification levels: code ✓, unit tests ✓, API integration tests ✓, live API test ✓ |
| R10 | SQLite persistence | VERIFIED_COMPLETE | `database.py`; WAL mode; 4 tables; idempotent `init_db()`; `analysis_log` intentionally excludes OCR/extracted text columns (privacy by design) | Verification levels: code ✓, unit tests ✓ |
| R11 | FastAPI REST API | VERIFIED_COMPLETE | Live test (2026-10-15): `GET /` → 200; `POST /analyze` (entity image) → 200, risk_level=MEDIUM, confidence=0.86, PHONE/DATE detected, redaction_status=redacted, regions=2, tokenization_applied=True; `GET /redacted-image?id=<token>` → 200 PNG; `GET /redacted-image?id=badtoken` → 404; missing field → 4xx; invalid feedback label → 422; valid feedback → 200; BUG-01 (PIL Windows file-lock) fixed | Verification levels: code ✓, unit tests ✓, API integration tests ✓, live API test ✓, browser test ✗ |
| R12 | Frontend UI | PARTIALLY_COMPLETE | `frontend/index.html`, `app.js`, `style.css`; code-reviewed: reads `.count` only from detection_results; `user_id` sent to analyze; `redaction_summary` rendered; redacted image fetched via `/redacted-image?id=<token>`; port 8765; no raw tokenized_text read; `resetResults()` clears redaction section | Browser rendering **not tested** — VS Code Live Server required; verification levels: code ✓, code review ✓, API integration tests ✓ (API side), browser test ✗ |
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
| I1 | Test suite with passing tests | VERIFIED_COMPLETE | 381 passed, 1 warning (live run 2026-10-15); includes E2E pipeline tests (E2E-01–08), pixel-redaction tests (REDACT-01–10), NER tests (NER-01–12), CV feature tests (CV-01–11), adaptation tests (ADAPT-01–12), new-route tests (ROUTE-01–12), DATE FP regression (DATE-FP-01, version-number guard), UNI-01–17, AUDIT-01–10 | |
| I2 | Protected files unmodified | VERIFIED_COMPLETE | SHA-256 verified: `CAPRLM_annotation_50.csv`=6FA1FEC8…, `caprlm_model.joblib`=C08E30BC…, `training_log.json`=3EAFB095… | |
| I3 | OCR pipeline reproducible | VERIFIED_COMPLETE | `manifest.json` tracks per-file status; deduplication in `candidates_deduped.csv` | |
| I4 | Model artifact versioned | VERIFIED_COMPLETE | `training_log.json` records version, date, metrics, feature names | |
| I5 | Frontend does not read raw OCR text | VERIFIED_COMPLETE | `frontend/app.js` reads `.count` only from detection_results; `tokenized_summary.tokenized_text` not read | |

---

## Features Beyond Original Proposal Scope (Implemented as Extensions)

These features were not required by the original proposal (as inferred from code and documentation — no formal proposal document was found in the repository). They have been implemented as research extensions.

| # | Item | Status | Evidence |
|---|------|--------|----------|
| X1 | Computer vision / visual feature extraction | IMPLEMENTED | `services/cv_features.py`; layout analysis from OCR bboxes (text density, cluster score, quadrant distribution); 11 passing tests (CV-01–11) |
| X2 | Contextual NER with confidence scoring | IMPLEMENTED | `services/ner_service.py`; regex + contextual heuristics; per-match confidence 0.75 base ±0.30; 12 passing tests (NER-01–12) |
| X3 | Pixel-level image transformations (actual blur) | IMPLEMENTED | `services/redaction.py`; PIL GaussianBlur r=15; fragmentation-aware entity mapping; 3-tuple return; 15 passing tests (REDACT-01–10) |
| X4 | Persistent reversible tokenization across requests | NOT_IMPLEMENTED | By design — ephemeral per-request; token map not stored |
| X5 | Continual learning / online threshold adaptation | IMPLEMENTED | `services/user_profile_service.py`; windowed recalibration (last 50); poisoning guard; adaptation_log; rollback; 12 passing tests (ADAPT-01–12) |
| X6 | Re-identification simulation experiments | NOT_IMPLEMENTED | Outside scope |
| X7 | Retrieval utility evaluation | NOT_IMPLEMENTED | Outside scope |

**Note on proposal document**: No formal project proposal (PDF, DOCX, or similar) was found in the repository. Requirements R1–R14 were derived by code inspection and cross-referenced against the annotation CSV, experiment artifacts, session history, and the `PROJECT_CONTROL_CENTER.md` stated objectives. The closest formal source is `PROJECT_CONTROL_CENTER.md` §1 ("Stated proposal objectives"). Gaps in proposal-scope coverage should be verified against the actual project brief before final submission.

**Note on annotation_guidelines_DRAFT.md**: v0.3 (2026-10-20) corrects the stale Section 2 — the "NOT implemented" list for pixel redaction, CV features, contextual NER, and continual learning has been replaced with a correction note listing their actual implementation status. Labelling policy is unchanged. Persistent reversible tokenization across requests remains intentionally absent.

---

## Summary Count (B5 updated 2026-10-15)

| Status | Count |
|--------|-------|
| VERIFIED_COMPLETE | 20 |
| PARTIALLY_COMPLETE | 2 |
| NOT_IMPLEMENTED | 3 |
| BLOCKED_BY_HUMAN_REVIEW | 1 |
| NOT_VERIFIED | 1 |
| **Total requirements** | **27** |

*Note: R12 moved from NOT_VERIFIED to PARTIALLY_COMPLETE because code review and API integration confirmed correct behaviour; only browser rendering remains unconfirmed.*

---

## Processing Latency (measured 2026-10-20, 50 runs, synthetic input)

| Stage | Mean (ms) |
|-------|-----------|
| Entity detection | 0.03 |
| ML risk prediction (RF) | 5.49 |
| Pixel redaction (BLUR_SENSITIVE) | 0.57 |
| **Full non-OCR pipeline** | **6.57** |
| EasyOCR (per-image, CPU-only) | 30 000–90 000 (measured separately) |

Full results: `backend/data/latency_results.json`. EasyOCR is the production bottleneck.

---

## Critical Submission Risks

1. **R13 BLOCKED**: 16/50 (32%) annotation rows have unresolved anomalies. Model was trained on potentially incorrect labels. All evaluation numbers are provisional until corrected.
2. **R4 gap**: ML model provides no measurable advantage over majority-class baseline (identical F1=0.4444). This is an important honest research finding, not a bug — but must be clearly documented.
3. **R12 PARTIALLY_COMPLETE**: Frontend UI browser rendering not tested — VS Code Live Server required. Code and API integration verified.
4. **E7/E8 NOT_IMPLEMENTED**: No inter-annotator agreement or statistical significance — standard academic expectations for a dataset paper.
5. **annotation_guidelines_DRAFT.md**: v0.3 corrects stale capability claims — resolved 2026-10-20.

---

*Matrix produced by AI (Claude Sonnet 4.6). All VERIFIED_COMPLETE statuses are based on code inspection and passing automated tests. Human review required before treating this matrix as a final submission artefact.*
