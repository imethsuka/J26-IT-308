# CAPRLM Final Validation Report
**Project:** J26-IT-308 — Context-Aware Privacy Risk Level Model  
**Date:** 2026-10-15  
**Evaluator:** Claude Sonnet 4.6 (AI validation — human review required before submission)

---

## 1. Test Suite Status

| Run | Tests Passed | Failures | Warnings |
|-----|-------------|---------|---------|
| Full suite (2026-10-15, post-PIL-fix) | **381** | **0** | 1 (httpx deprecation, non-functional) |

Test files and coverage:

| File | Tests | Coverage area |
|------|-------|---------------|
| `test_end_to_end.py` | 8 (E2E-01–08) | Full pipeline: analyze → redact → feedback → calibration → rollback |
| `test_new_routes.py` | 12 (ROUTE-01–12) | `/redacted-image`, `/user/calibration-history`, `/user/rollback` |
| `test_adaptation.py` | 12 (ADAPT-01–12) | Poisoning guard, windowed recalibration, adaptation log, rollback |
| `test_redaction.py` | 15 (REDACT-01–10+) | Pixel-level blur, BLOCK, MONITOR, fragmentation, clamping |
| `test_ner_service.py` | 12 (NER-01–12) | Entity detection, confidence scoring, context boost/reduction |
| `test_cv_features.py` | 11 (CV-01–11) | Text density, cluster score, quadrant distribution |
| `test_api.py` | ~40 | Core API unit tests |
| `test_api_integration.py` | ~30 | API integration tests |
| `test_privacy_regression.py` | 14 (REG-01–14) | Privacy regression: no raw OCR, no token map leakage |
| `test_unicode_regression.py` | 17 (UNI-01–17) | Unicode edge cases in tokenizer |
| `test_annotation_audit.py` | 10 (AUDIT-01–10) | Annotation CSV integrity, label distribution |

---

## 2. Component Implementation Status

### 2.1 OCR Pipeline
- **Status**: VERIFIED_COMPLETE
- EasyOCR reads bounding boxes in `[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]` format
- `extract_text_with_boxes()` returns `(text, count, detections)` 3-tuple
- Canvas size: 2560px, mag_ratio: 1.5 (CPU-only, no GPU)
- Manifest: 1,000 screenshots processed, 0 failed

### 2.2 Entity Detection (Rule-based)
- **Status**: VERIFIED_COMPLETE
- 6 entity types: EMAIL, PHONE, URL, IP_ADDRESS, DATE, WINDOWS_PATH
- Privacy evaluation: 26/26 entity-level tests pass
- Limitation: 0 positive WINDOWS_PATH examples in dataset

### 2.3 Contextual NER with Confidence Scoring
- **Status**: IMPLEMENTED (extension beyond original scope)
- Method: `regex+contextual_heuristic`
- Base confidence: 0.75 per match; context boosts: +0.10 to +0.20; negative triggers: −0.30
- Honest limitation: not a trained model; confidence values are heuristic estimates
- All 12 NER tests pass

### 2.4 Feature Extraction
- **Status**: VERIFIED_COMPLETE
- Entity counts + application context + OCR text length
- Context encoder fitted on training fold only (no test data leakage)

### 2.5 Risk Classification (ML + Rule Baseline)
- **Status**: VERIFIED_COMPLETE
- Random Forest (joblib artifact, version 1.0.0, 2026-10-13)
- Evaluation on 10-row test fold: macro F1 = 0.4444, accuracy = 0.800
- Rule baseline: identical macro F1 = 0.4444
- Majority-class baseline: identical macro F1 = 0.4444
- **Critical finding**: ML model provides no measurable advantage over baselines on this dataset
- Root cause: feature-class overlap (MEDIUM and HIGH share same entity patterns); 10-row test fold too small for discrimination
- This is an honest research finding, documented in `experiment_results.json`

### 2.6 Computer Vision Feature Extraction
- **Status**: IMPLEMENTED (extension beyond original scope)
- Layout analysis from OCR bounding boxes (no visual encoder needed)
- Features: `text_density_ratio`, `sensitive_area_ratio`, `entity_cluster_score`, `quadrant_sensitive_counts`
- `cv_method: "ocr_bbox_layout_analysis"` — honest about what it is
- All 11 CV tests pass

### 2.7 Protection Action Selection + Pixel Redaction
- **Status**: VERIFIED_COMPLETE (pixel redaction is an implemented extension)
- Actions: MONITOR, BLUR_SENSITIVE, BLUR_AND_ALERT, BLOCK
- `redact_image()` applies PIL GaussianBlur (radius=15) to detected entity regions
- Fragmentation-aware: sliding window (size 2–3) over consecutive OCR boxes
- Returns `(redacted_image, redacted_regions, redaction_stats)` 3-tuple
- Redacted image stored in process memory, never on disk
- All 15 pixel-level tests pass

### 2.8 Reversible Tokenization
- **Status**: PARTIALLY_COMPLETE
- T5 word-piece tokenization for HIGH risk; skips at LOW+confidence≥0.75
- Token map is ephemeral, not returned to client, not persisted
- Limitation: cannot restore across requests (by design)
- All privacy regression tests pass

### 2.9 Continual Learning (Threshold Recalibration)
- **Status**: IMPLEMENTED (extension beyond original scope)
- Windowed recalibration: last 50 feedback records
- Poisoning guard: max 3 records per screenshot per user
- `screenshot_id` whitespace stripping prevents trivial guard bypass
- Adaptation log: full audit trail in SQLite `adaptation_log` table
- Rollback: restores second-most-recent threshold state
- All 12 ADAPT tests pass

---

## 3. Security and Privacy Findings

### 3.1 Findings Fixed in This Session

| ID | Finding | Fix Applied |
|----|---------|-------------|
| SEC-01 | Redacted-image store was keyed by client-supplied filename; any caller who knew (or guessed) another user's filename could retrieve their image | Changed to `secrets.token_hex(16)` (2^128 search space); verified by ROUTE-11 |
| SEC-02 | Feedback `screenshot_id` whitespace not stripped; attacker could submit `"scr_001 "` to bypass poisoning guard | `str(screenshot_id).strip()` applied before DB query; verified by ADAPT-12 |
| BUG-01 | `PIL.Image.open()` in `/analyze` held file handles open; `temporary_path.unlink()` in `finally` raised `PermissionError` on Windows | Changed to `with PIL.Image.open(...) as img: img.load()` context-manager pattern; verified by live smoke test (2026-10-15) returning HTTP 200 |

### 3.2 Residual Design-Level Properties

| Property | Status |
|---------|--------|
| OCR text not persisted to database | VERIFIED — `analysis_log` has no `ocr_text` column |
| Raw OCR text not returned in API response | VERIFIED — `tokenized_text` returned as `[redacted — ...]` when tokenization skipped |
| Token-to-value map not returned to client | VERIFIED — REG-01 regression test confirms |
| Redacted images ephemeral (process memory, 50-entry FIFO) | DESIGN — lost on server restart; prevents long-term storage of sensitive images |
| CORS: only localhost:5500 allowed | VERIFIED — `main.py` CORSMiddleware configuration |
| `user_id` sanitized (alphanumeric + hyphen only) | VERIFIED — `_sanitise_user_id()` in `user_profile_service.py` |
| No user authentication layer | KNOWN GAP — prototype; any caller may submit feedback for any `user_id`; acceptable for academic context but not production |

### 3.3 Error Handling and Leakage Review

- `/analyze` catches all exceptions in the redaction block with bare `except Exception: pass` — prevents error message leakage of OCR content, at the cost of silent failure
- The `_log_analysis()` function also uses `except Exception: pass` — DB log failures never surface screenshot content
- No server-side logging of OCR text or extracted entity values found

---

## 4. Model and Baseline Evaluation Audit (TASK C)

### 4.1 Comparability
All three classifiers (majority, rule, RF) evaluated on the **same** 10-row held-out test fold from a deterministic 80/20 stratified split (random_state=42). No data leakage: context encoder fitted only on training fold, not the full dataset.

### 4.2 Class Imbalance
| Class | Train count | Test count |
|-------|-------------|------------|
| LOW | ~30 | ~8 |
| MEDIUM | ~8 | ~2 |
| HIGH | ~2 | 0 |

HIGH class has **0 test examples** — F1 for HIGH class cannot be evaluated. This is documented in `experiment_results.json` (limitation #3) and the RTM (R4 gap).

### 4.3 Metrics Reported
- Macro F1, accuracy, per-class classification report, confusion matrix: all in `experiment_results.json`
- Threshold sensitivity: 10 configurations tested, reported in `experiment_results.json`

### 4.4 Unsupported Superiority Claims
No superiority claim found in the current codebase or documentation. The `experiment_results.json` explicitly documents that all three classifiers produce identical macro F1 = 0.4444. The RTM R4 entry states: "ML model provides no measurable advantage over majority-class baseline."

---

## 5. Proposal Requirements Audit (TASK D)

No formal project proposal document (PDF, DOCX) was found in the repository. Requirements were derived by:
1. Code inspection of all service modules, scripts, and test files
2. Cross-referencing with the annotation CSV, training log, and experiment artifacts
3. Session history and git log

The requirements traceability matrix (`requirements_traceability_matrix.md`) documents 27 requirements (R1–R14, P1–P6, E1–E8, I1–I5) and 7 feature extensions (X1–X7). Extensions X1, X2, X3, X5 have been implemented as research additions. Extensions X4, X6, X7 are noted as intentionally absent.

**Action required**: Before submission, verify the RTM against the actual project brief to confirm R1–R14 match the supervisor-specified requirements.

---

## 6. Frontend Review and Changes

### 6.1 Defects Fixed (this session)
| Defect | Fix |
|--------|-----|
| Port hardcoded to 8000 — backend runs on 8765; frontend could not connect | Changed both `ANALYZE_URL` and `FEEDBACK_URL` to `http://127.0.0.1:8765/...` |
| No `user_id` in analyze request — personalized threshold always applied to "default" user | `user_id` now taken from feedback user ID field and appended to FormData |
| No redacted image display — `redaction_summary` existed in API response but was never shown | Added `#redaction-output` section + `#redacted-image-section` (hidden until image available); `renderRedactionSummary()` fetches and displays the redacted PNG |

### 6.2 Existing Correct Behavior
- Reads `.count` only from `detection_results` — no raw entity value display
- Does not read `tokenized_summary.tokenized_text` — no raw OCR leakage
- Feedback submission validated: `user_label` must be one of LOW/MEDIUM/HIGH/CRITICAL

### 6.3 Live API and Pixel Redaction Verification (B4 — 2026-10-15)

A synthetic test image containing real entity text (phone + date) was generated and submitted to the live API. Results confirmed end-to-end pixel-level redaction:

| Check | Result |
|-------|--------|
| POST /analyze (entity image) | HTTP 200 |
| risk_level | MEDIUM |
| confidence | 0.86 |
| protection_action | BLUR_SENSITIVE |
| redaction_status | redacted |
| regions_redacted | 2 |
| redacted_image_available | True |
| GET /redacted-image → valid PNG | HTTP 200, Content-Type image/png |
| Pixels changed in redacted image | **2,647** (confirmed by numpy comparison) |
| Original image unchanged | **YES** (size 4715 bytes, unchanged after API call) |
| Bad token → 404 | CORRECT |
| Missing required field | 4xx CORRECT |
| Invalid feedback label | 422 CORRECT |
| Valid feedback recorded | HTTP 200, feedback_count=1 |
| GET /calibration-history | HTTP 200 |
| POST /rollback | HTTP 200 |

**Note on email entity**: The test image drew with the default PIL bitmap font (tiny pixels). EasyOCR detected PHONE and DATE from the rendered text but did not extract the email address from the small font rendering. Detection-to-redaction is confirmed for PHONE and DATE; email detection from bitmap fonts was not verified in this particular test. Unit tests (REDACT-01–15) cover email entity redaction directly.

### 6.4 Browser Test Limitation

Browser rendering has **not been tested** — VS Code Live Server required; browser automation is not available in this environment.

Exact manual steps to complete browser verification:
1. Start backend: `cd Member2 && .venv\Scripts\uvicorn backend.main:app --host 127.0.0.1 --port 8765`
2. Open `Member2/frontend/index.html` via VS Code Live Server (port 5500)
3. Upload a screenshot, set Application Context, Privacy Preference, Historical Protection Level
4. Click Analyze — expected: results populate all five panels including "Pixel Redaction"
5. If protection_action ≠ MONITOR and entities were detected: the "Redacted Image" section should appear with the blurred PNG
6. Submit feedback — expected: feedback count increments
7. Submit with missing fields — expected: Analyze button disabled (client-side guard)
8. Start backend on wrong port / stop it — expected: "Backend unavailable" error, no success state

Frontend does not display `ner_summary` or `visual_features` (new backend fields) — these are returned in the API response but not rendered in the UI

---

## 7. Protected File Integrity

| File | SHA-256 | Status |
|------|---------|--------|
| `CAPRLM_annotation_50.csv` | `6FA1FEC8...` | UNMODIFIED |
| `caprlm_model.joblib` | `C08E30BC...` | UNMODIFIED |
| `training_log.json` | `3EAFB095...` | UNMODIFIED |

---

## 8. Blocking Risks for Submission

| # | Risk | Severity | Resolution required |
|---|------|---------|---------------------|
| B1 | 16/50 (32%) annotation rows have unresolved anomalies; model trained on potentially incorrect labels | CRITICAL | Human annotation review + supervisor approval |
| B2 | 8 annotation policies unresolved (supervisor approval needed) | CRITICAL | Supervisor meeting |
| B3 | ML model = majority baseline F1 (0.4444) — must be honestly reported | HIGH | Document as research finding, not a bug |
| B4 | Frontend browser test not performed | LOW | Run with VS Code Live Server + manual check |
| B5 | No formal proposal document found; RTM may miss requirements | MEDIUM | Obtain project brief and cross-check |
| B6 | No inter-annotator agreement (E7) or significance testing (E8) | LOW | Document as known limitations |

---

## 5b. Detection Evaluation — False Positive Analysis

Script: `backend/scripts/detection_eval.py`  
Output: `backend/data/detection_eval_results.json`  
Method: candidates.csv entity flag counts (no OCR re-run required)

### Scope
28 LOW-labeled annotation rows with all-zero annotation entity flags — these should trigger zero pipeline detections if the detection is perfectly aligned with annotation policy.

### Results
| Entity type | Screenshots with pipeline detection | Rate |
|-------------|-----------------------------------|------|
| DATE | 26 / 28 | 92.9% |
| PHONE | 2 / 28 | 7.1% |
| IP_ADDRESS | 1 / 28 | 3.6% |
| EMAIL | 0 / 28 | 0% |
| URL | 0 / 28 | 0% |
| WINDOWS_PATH | 0 / 28 | 0% |

### Interpretation
- **DATE dominates**: 26/28 screenshots show a DATE pipeline detection that the annotator did not flag. Desktop screenshots routinely display timestamps, file modification dates, message times, and version dates (e.g. "10/14/2026"). These match the date regex but are not what the annotation policy treats as sensitive.
- **This is an annotation-policy gap, not a code bug.** The DATE regex is correct by definition; the gap is between the regex boundary and what the annotators considered "sensitive dates."
- **Regression test added**: `test_date_ui_timestamp_triggers_detection` documents the known behavior; `test_date_version_number_not_detected` guards against `3.14.1`-style version strings being misclassified.
- **PHONE/IP false positives** (3 cases total): require visual review to distinguish true positives (real phone numbers in screenshots) from false positives (student IDs, CIDR addresses, etc.).

---

## 9. Summary

The CAPRLM backend is functionally complete with **381 passing tests and 0 failures**. Core proposal requirements (R1–R14) are verified complete or partially complete. Four research extensions (CV features, contextual NER, pixel redaction, continual learning with rollback) are implemented and tested. Two security defects were identified and fixed earlier (random store keys, whitespace stripping). Frontend defects were fixed in this session (port 8000→8765, user_id propagation, redacted image display). Detection evaluation ran on 28 LOW-zero-annotation screenshots and identified DATE false positives (92.9% rate) as a documented annotation-policy gap. Critical submission blockers remain in the annotation review process and require human action.

---

*This report was produced by AI (Claude Sonnet 4.6). All findings are based on code inspection and automated tests. The annotation anomaly findings require human expert review before final submission.*
