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

---

## 10. End-to-End Processing Latency (2026-10-20)

Script: `measure_latency.py` (scratchpad)  
Output: `backend/data/latency_results.json`  
Method: 50 runs per stage on synthetic text input (email + phone + date + IP + URL entities). EasyOCR excluded — takes 30–90 s on CPU and dominates real-world latency. All other pipeline stages measured independently.

| Stage | Mean (ms) | Std (ms) |
|-------|-----------|----------|
| 1. Entity detection (regex) | 0.03 | <0.01 |
| 2. Feature extraction | <0.01 | <0.01 |
| 3. ML risk prediction (Random Forest) | 5.49 | 1.02 |
| 4. Rule-based prediction | <0.01 | <0.01 |
| 5. Protection selection | <0.01 | <0.01 |
| 6. Tokenization | 0.01 | <0.01 |
| 7. Contextual NER | 0.06 | 0.01 |
| 8. CV feature extraction | 0.02 | 0.01 |
| 9. Pixel redaction (BLUR_SENSITIVE) | 0.57 | 0.05 |
| **Full non-OCR pipeline** | **6.57** | **1.08** |

**Key finding**: the Random Forest `predict_proba` call (5.49 ms) is the single largest non-OCR latency. Total non-OCR processing is **~7 ms per image**, making EasyOCR the overwhelming production bottleneck.

**sklearn feature-name warning**: the RF model was fitted with a pandas DataFrame; at inference, features are passed as a numpy array without column names. sklearn emits a `UserWarning` per call. This is cosmetic only — predictions are correct. Fix: wrap the inference array in a named DataFrame matching the training feature order.

---

## 11. Session 9 Re-verification (2026-10-20)

### 11.1 Protected File Integrity

| File | SHA-256 | Status |
|------|---------|--------|
| `CAPRLM_annotation_50.csv` | `6FA1FEC8…` | UNMODIFIED — matches session 8 |
| `caprlm_model.joblib` | `C08E30BC…` | UNMODIFIED — matches session 8 |
| `training_log.json` | `3EAFB095…` | UNMODIFIED — matches session 8 |

### 11.2 Test Suite

381 passed, 1 warning (httpx deprecation, non-functional), 0 failures — confirmed 2026-10-20.

### 11.3 Detection Evaluation Re-run

`backend/scripts/detection_eval.py` re-executed on unchanged CSV. Results identical to session 8:

| Entity type | Screenshots with pipeline detection (on 28 LOW-zero-annotation rows) | Rate |
|---|---|---|
| DATE | 26 / 28 | 92.9% |
| PHONE | 2 / 28 | 7.1% |
| IP_ADDRESS | 1 / 28 | 3.6% |
| EMAIL | 0 / 28 | 0% |
| URL, WINDOWS_PATH | 0 / 28 | 0% |

Results are reproducible and consistent.

### 11.4 Annotation Guidelines Stale Content — Fixed

`annotation_guidelines_DRAFT.md` Section 2 previously listed pixel redaction, CV feature extraction, contextual NER, and continual learning as "NOT implemented." These are all now implemented as research extensions. A correction note was added to Section 2 and the document bumped to v0.3 (2026-10-20). Labelling policy itself is unchanged.

---

## 12. Session 10 — Privacy and Security Finalisation (2026-10-20)

### 12.1 Baseline

| Run | Tests Passed | Failures | Warnings |
|-----|-------------|---------|---------|
| Pre-session (session 10 start) | 410 | 0 | 1 |
| Post-session (session 10 end) | **416** | **0** | 1 |

### 12.2 Security Findings

The full pipeline was reviewed as a senior security engineer, tracing data from screenshot upload through OCR, detection, feature extraction, risk prediction, protection selection, redaction, storage, and API response.

| ID | Finding | Class | Status |
|----|---------|-------|--------|
| F1 | No image upload size limit — arbitrarily large payload accepted, causing downstream OOM risk | A — Confirmed defect | **Fixed** |
| F2 | No image format validation — malformed bytes proceeded to PIL (redaction step), returning 503 (server error) instead of 400 (client error) | A — Confirmed defect | **Fixed** |
| F3 | `FeedbackRequest.screenshot_id` and `user_id` had no length cap — storage abuse via extremely long strings | A — Confirmed defect | **Fixed** |
| F4 | `RollbackRequest.user_id` had no length cap | A — Confirmed defect | **Fixed** |
| F5 | `_sanitise_user_id` stripped chars but did not cap length — very long user_id could pass Pydantic validators and reach DB | A — Confirmed defect | **Fixed** |
| F6 | `user_id` is entirely client-controlled; no authentication prevents one user impersonating another | C — Requires deployment infrastructure | Not addressed (documented below) |
| F7 | Redacted image store is not user-scoped; any caller with a valid token can retrieve any image | B — Mitigated by `secrets.token_hex(16)` (256-bit brute-force infeasible) | Partially mitigated |
| F8 | No rate limiting on `/feedback` — adversarial threshold manipulation possible despite per-screenshot poisoning guard | C — Requires deployment infrastructure | Not addressed (documented below) |
| F9 | Residual OCR text in redacted image — Gaussian blur does not guarantee unreadable text | Inherent limitation | Documented (see §12.4) |

### 12.3 Fixes Implemented

**`Member2/backend/main.py`**
- Added `_MAX_IMAGE_BYTES = 20 * 1024 * 1024` (20 MB) constant.
- Added `_VALID_IMAGE_MAGIC` tuple containing PNG, JPEG, BMP, GIF, TIFF-LE, and TIFF-BE file signatures.
- Added `_is_valid_image_bytes(data) → bool` helper (magic-bytes prefix check).
- In `_parse_analyze_request`: size check → 413 (Request Entity Too Large); format check → 400 (Bad Request) before the image is written to any temp file or passed to OCR.
- Added `Field(max_length=128)` to `FeedbackRequest.user_id` and `Field(max_length=512)` to `FeedbackRequest.screenshot_id`.
- Added `Field(max_length=128)` to `RollbackRequest.user_id`.
- Added `Field` import.

**`Member2/backend/services/user_profile_service.py`**
- In `_sanitise_user_id`: added `if len(cleaned) > 128: raise ValueError(...)` as a defence-in-depth guard covering the `/analyze` form-data path (not covered by Pydantic models).

### 12.4 Tests Added or Updated

**`Member2/backend/tests/test_api_integration.py`** (26 tests, up from 20):
- `test_int02_non_image_bytes_rejected_before_ocr` — updated from 503 to 400; added detail-message assertion. The Phase 4 fix replaces PIL failure (503) with early rejection (400).
- `test_int13_oversized_image_upload_returns_413` — 21 MB payload with valid PNG magic bytes → 413; "20 MB" in detail.
- `test_int14_invalid_image_format_returns_400` — arbitrary non-image bytes → 400; "recognised image format" in detail.
- `test_sec01_feedback_user_id_too_long_returns_422` — 129-char user_id → 422 from Pydantic.
- `test_sec02_feedback_screenshot_id_too_long_returns_422` — 513-char screenshot_id → 422 from Pydantic.
- `test_sec03_redacted_image_unknown_token_returns_404` — random token → 404; empty id → 400.
- `test_sec04_distinct_user_profiles_are_isolated` — user A's calibration not visible in user B's fresh profile (DB-level row isolation confirmed).

**`Member2/backend/tests/test_redaction.py`** (54 tests, up from 46 in session 9):
- STAGE3-PIX-01: BLUR_SENSITIVE pixel change on nonuniform gradient image.
- STAGE3-PIX-02: Adjacent-boundary benign box unchanged (0-px gap from entity box).
- STAGE3-PIX-03: Partial mapping pixel test — mapped region changes, benign row unchanged.
- FRAG-PIX: Both fragmented boxes show pixel changes on gradient image.
- STATS-INVAR: `entities_detected = entities_mapped + entities_unmapped` for BLUR_SENSITIVE.

### 12.5 Residual OCR Limitation

**Gaussian blur does not guarantee unreadable text.** A blur with radius 15 applied to a small OCR bounding box may still leave readable characters for a human or OCR engine, depending on font size, contrast, and blur kernel coverage. Evaluating residual readability requires re-running EasyOCR on each blurred region (30–90 s per image on CPU), which is impractical in automated tests. This limitation is not addressed by the current test suite.

Claim: BLUR_SENSITIVE applies a pixel-level transformation to detected entity regions. **It does not guarantee that the blurred text is unreadable or that OCR recovery is infeasible.**

### 12.6 Deployment Prerequisites (Routes Unsafe Without Authentication)

The following routes accept client-supplied `user_id` with no authentication:
- `POST /analyze` — reads and creates user profiles by client-supplied `user_id`
- `POST /feedback` — records corrections for any `user_id`; influences threshold calibration
- `GET /user/calibration-history?user_id=...` — returns calibration history for any `user_id`
- `POST /user/rollback` — rolls back calibration for any `user_id`

Without a session-bound identity layer these routes cannot prevent impersonation. Implementing authentication requires deployment infrastructure outside this prototype's scope (OAuth2 / session tokens / API keys). These routes must be protected before any production or multi-user deployment.

### 12.7 Protected File Integrity (Session 10)

| File | SHA-256 | Status |
|------|---------|--------|
| `CAPRLM_annotation_50.csv` | `6FA1FEC86EF40FFE9B93A0A9AF40EDD69F15997AA838AF3C64D1025A75C1708B` | UNMODIFIED |
| `caprlm_model.joblib` | `C08E30BC92632C41D64CA66197A6105B6D4F2E17AE51A3599A972D574907E3F4` | UNMODIFIED |
| `training_log.json` | `3EAFB095BAB9C3941E5C76380D4276704AC36A2211E2DB9D49A2E4764A0E4C52` | UNMODIFIED |

---

## 9. Summary

The CAPRLM backend is functionally complete with **416 passing tests and 0 failures** (confirmed 2026-10-20, session 10). Core proposal requirements (R1–R14) are verified complete or partially complete. Four research extensions (CV features, contextual NER, pixel redaction, continual learning with rollback) are implemented and tested. Security hardening in session 10 added image upload size limits (413), early image format validation (400 replacing downstream 503), and field-length caps on all user-controlled string inputs. Redaction pixel-change validation was strengthened in session 9 (pixel assertions on nonuniform synthetic images, fragmentation pixel tests, statistics invariant). Detection evaluation confirmed 92.9% DATE false-positive rate on annotated-clean rows — documented annotation-policy gap. Non-OCR pipeline latency: 6.57 ms ±1.08 ms. EasyOCR (30–90 s/image) is the production bottleneck. Critical submission blockers remain in the annotation review process and require human action. Authentication infrastructure is required before any production deployment.

---

*This report was produced by AI (Claude Sonnet 4.6). All findings are based on code inspection and automated tests. The annotation anomaly findings require human expert review before final submission.*
