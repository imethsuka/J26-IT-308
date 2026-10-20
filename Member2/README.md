# Member 2 — CAPRLM

**Context-Aware Privacy Intelligence and Adaptive Risk Learning Framework**
J26-IT-308 · Research Prototype

---

## Overview

CAPRLM is a desktop privacy-risk analysis prototype. It processes screenshots via OCR, detects sensitive entities (email, phone, URL, IP address, file paths) using rule-based regex pattern matching, classifies privacy risk using a trained Random Forest model, selects a protection action (but does not execute image transformations), and adapts decision thresholds to per-user feedback over time.

The system implements a full research loop: batch OCR pipeline → annotated training data → ML model training → live analysis API → user feedback → personalised threshold recalibration.

**Scope note:** The implemented pipeline is text-based. The OCR stage extracts text from screenshots; all subsequent processing operates on that text. No computer vision, semantic embedding, or trained NER model is integrated. Protection actions (blur, block) are selected and returned by the API but image manipulation is not applied.

---

## Architecture

```
screenshots/            ← raw images (never committed)
    │
    ▼
scripts/process_screenshots.py
    │  EasyOCR (CPU, text extraction only), manifest-based resume,
    │  Windows subprocess timeout
    │
    ▼
services/sensitive_detection.py
    │  Rule-based regex PII detection (EMAIL, PHONE, URL, IP_ADDRESS,
    │  DATE, WINDOWS_PATH) — not a trained NER model
    │
    ▼
data/candidates.csv     ← extracted OCR features + auto-label candidates
data/CAPRLM_annotation_50.csv  ← 50 manually annotated rows (ground truth)
    │
    ▼
services/train_model.py
    │  Random Forest, stratified CV, encoder leakage fix
    ▼
data/caprlm_model.joblib        ← trained model bundle (not committed)
    │
    ▼
services/protection.py
    │  Risk-to-action mapping (MONITOR / BLUR_SENSITIVE / BLUR_AND_ALERT /
    │  BLOCK). Returns action name; does not apply image transformations.
    │
    ▼
main.py  (FastAPI)
    POST /analyze  ←─── frontend/index.html + app.js
    POST /feedback
    POST /train
    GET  /model-info
    │
    ▼
data/caprlm.db  (SQLite, WAL mode)
    ├── user_profile     ← per-user preferences + adaptive thresholds
    ├── feedback_log     ← every user correction (FK → user_profile)
    ├── analysis_log     ← every /analyze call (no raw OCR text)
    └── training_run     ← every /train call + evaluation metrics
```

**Storage**: SQLite (`backend/data/caprlm.db`). Tables are created automatically on first startup via `init_db()`. Existing JSON profiles under `backend/data/profiles/` are migrated non-destructively on startup (originals are preserved). View with DBeaver 26.2.2: *Database → New Connection → SQLite → browse to `caprlm.db`*.

---

## Setup

```
cd Member2
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
```

Train the shared model before starting the API:

```
python -m backend.services.train_model
```

Start the API server:

```
uvicorn backend.main:app --reload
```

Open `frontend/index.html` in a browser.

---

## Running the OCR pipeline

The pipeline processes screenshots from a configured input directory, extracts OCR text, detects sensitive entities, and writes a candidate CSV.

```
python -m backend.scripts.process_screenshots \
    --input-dir path/to/screenshots \
    --output-dir backend/data/my_run \
    --batch-size 10 \
    --ocr-timeout 120
```

- **Resume/retry**: the manifest (`manifest.json`) records per-image status. Re-running picks up where it left off.
- **Windows timeout**: each image is processed in a persistent worker subprocess. If OCR takes longer than `--ocr-timeout` seconds the image is recorded as failed and the worker is restarted (~20 s overhead per event).
- **Never modified**: `CAPRLM_annotation_50.csv` is the protected annotation file. The pipeline writes only to the specified output directory.

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check and model status |
| POST | `/analyze` | Analyse a screenshot (multipart form) |
| POST | `/feedback` | Record a risk-level correction |
| POST | `/train` | Retrain the model from the annotation CSV |
| GET | `/model-info` | Current model bundle metadata |

**`POST /analyze`** — form fields: `image` (file), `application_context` (str), `privacy_preference` (Low/Medium/High), `historical_protection_level` (Low/Medium/High).

Returns: `ocr_summary`, `detection_results`, `extracted_features`, `risk_prediction`, `rule_baseline`, `protection_selection`, `user_profile_summary`, `tokenized_summary`.

---

## T5 — Reversible tokenization (prototype)

`/analyze` applies risk-adaptive entity masking to the API response. Detected sensitive values (email, phone, URL, IP address, date, Windows path) in `detection_results` are replaced with opaque placeholder tokens such as `[EMAIL_1]`, `[PHONE_2]`.

**When tokenization is applied:**

| Predicted risk | Behaviour |
|---|---|
| HIGH or CRITICAL | Always tokenize |
| MEDIUM | Always tokenize |
| LOW, confidence < 0.75 | Tokenize (uncertain prediction) |
| LOW, confidence ≥ 0.75 | Skip tokenization; `detection_results.matches` contains the original detected entity strings (raw values present) |
| UNAVAILABLE (model not loaded) | Always tokenize (fail-safe) |
| LOW, confidence = None | Always tokenize (fail-safe) |

The 0.75 confidence threshold is a design choice and has **not** been empirically optimised. When tokenization is skipped, `detection_results.matches` lists contain the original detected entity values. **This is a known privacy limitation of the prototype**: on screenshots predicted LOW with high confidence, the raw JSON API response includes unmasked entity strings (email addresses, phone numbers, etc.) in `detection_results[category]["matches"]`. The frontend (`app.js`) does not display `matches` to users (it reads only `.count`), but callers reading the raw response receive unmasked values.

**Token-to-value restoration map**: the `token_map` is ephemeral. It is created per-request, used within the same request to mask entity values in the response, and then discarded. It is never returned to the client, logged, or stored. Cross-request restoration is not supported.

**Known limitation**: if OCR text already contains a literal string matching the token format (e.g. a UI element reading `[EMAIL_1]`), that string is indistinguishable from a genuine token and restoration will produce incorrect output at that position. This edge case is not handled at runtime.

---

## Research evaluation

### Dataset

| | Count |
|-|-------|
| Total annotated rows | 50 |
| LOW | 38 (76%) |
| MEDIUM | 10 (20%) |
| HIGH | 2 (4%) |
| CRITICAL | 0 |

36 of 50 rows have no recorded `application_context` (filled as "Unknown" during training). The dataset is severely class-imbalanced.

### Sensitive entity detection

Entities are detected by deterministic regex patterns in [services/sensitive_detection.py](backend/services/sensitive_detection.py). This is **rule-based PII pattern matching**, not a trained Named Entity Recognition (NER) model. Six entity categories are covered: EMAIL, PHONE, URL, IP\_ADDRESS, DATE, WINDOWS\_PATH. No NLP model, tokeniser, or trained classifier is involved in this stage.

### ML model — Random Forest (v1.0.0)

Split: 80/20 stratified, `random_state=42`. Encoder fitted on training fold only (leakage fix applied: `context_encoder` is fit on the training rows and only transformed on the test rows; unseen categories at inference map to −1).

| Metric | Value |
|--------|-------|
| Training rows | 40 (LOW=30, MEDIUM=8, HIGH=2) |
| Test rows | 10 (LOW=8, MEDIUM=2, HIGH=0) |
| CV folds | 2 (capped by smallest training-class count) |
| CV macro F1 (mean ± std) | 0.353 ± 0.075 |
| Test macro F1 (n=10) | 0.444 |
| Test accuracy | 0.80 |
| LOW F1 | 0.889 |
| MEDIUM F1 | 0.000 |
| HIGH support in test | 0 |

**Confusion matrix** (rows = true label, columns = predicted label):

```
             HIGH  LOW  MEDIUM
true HIGH       0    0       0   (0 HIGH rows in test set)
true LOW        0    8       0
true MEDIUM     0    2       0   (both MEDIUM rows predicted LOW)
```

The model predicted only LOW across all 10 test rows. No MEDIUM or HIGH predictions were produced.

### Rule baseline — deterministic classifier

Evaluated on the same held-out 10-row test fold as the ML model, using `services/baseline_eval.py`. The split is deterministic (`random_state=42`, 80/20 stratified) and matches the split used for ML model training, so both classifiers are evaluated on identical test rows.

| Metric | Value |
|--------|-------|
| Test rows | 10 (same fold as ML model) |
| Accuracy | 0.80 |
| Macro F1 | 0.444 |
| LOW F1 | 0.889 |
| MEDIUM F1 | 0.000 |
| HIGH support in test | 0 |

The rule baseline predicted only LOW across all 10 test rows — the same outcome as the ML model. Both misclassified the 2 MEDIUM test rows as LOW.

To reproduce: `python -m backend.services.baseline_eval`

### Comparison

Both classifiers are evaluated on the same held-out 10-row test set via `services/baseline_eval.py`:

| Classifier | Accuracy | Macro F1 |
|---|---|---|
| Rule baseline | 0.80 | 0.444 |
| Random Forest (ML) | 0.80 | 0.444 |

The identical scores reflect that both classifiers predict only LOW on this test set: the 8 LOW rows are correct and the 2 MEDIUM rows are wrong for both. This result is a consequence of the feature-class overlap documented in Limitation 3 below, not a coincidence. Neither result is statistically reliable at n=10.

**Macro F1 calculation note**: sklearn `f1_score(average="macro")` without explicit `labels` averages over only the classes present in predictions and true labels (LOW and MEDIUM), not over all three classes including HIGH. The reported 0.444 = (0.889 + 0.000) / 2. The classification report's "macro avg" row uses all three classes including HIGH (0 support) and reports 0.296.

The rule-based classifier is the only conventional baseline evaluated. Comparisons against masking, anonymization, or pseudonymization pipelines are not implemented.

### Risk-adaptive reversible tokenization (T5)

[services/tokenizer.py](backend/services/tokenizer.py) implements the T5 proposal requirement.

**What it does:** detected sensitive entity values in the OCR text and detection results are replaced with category-scoped placeholder tokens (`[EMAIL_1]`, `[PHONE_2]`, etc.) before the API response is assembled. The restoration mapping (token → original value) is held in memory for the duration of the request and then discarded; it is never returned to the client, logged, or stored.

**Risk-adaptive gating:** the tokenizer inspects the ML model's `risk_level` and `confidence` to decide whether to apply tokenization:

| Condition | Decision |
|---|---|
| risk_level = HIGH or CRITICAL | Always tokenize |
| risk_level = MEDIUM | Always tokenize |
| risk_level = UNAVAILABLE (model not loaded) | Tokenize (fail-safe) |
| risk_level = LOW, confidence = None | Tokenize (fail-safe) |
| risk_level = LOW, confidence < 0.75 | Tokenize (uncertain prediction) |
| risk_level = LOW, confidence ≥ 0.75 | Skip (confident safe prediction) |

The threshold 0.75 is a design choice and is **not empirically optimised**. It is configurable via `min_confidence_to_skip` in `tokenize()`.

**API response fields:**
- `detection_results[category]["matches"]` — contains token strings (e.g. `["[EMAIL_1]"]`) instead of original values when tokenization is applied.
- `tokenized_summary.tokenized_text` — OCR text with entities replaced by tokens.
- `tokenized_summary.entity_token_count` — number of unique entities tokenized.
- `tokenized_summary.tokenization_applied` — bool.

**What is never in the response:** the `token_map` (the restoration mapping). It is intentionally excluded.

**Security limitations:**
- Restoration is valid only within the same request. There is no mechanism to restore tokens from a later request.
- The token format `[CATEGORY_N]` is distinctive but not cryptographically protected. This is a prototype demonstration, not a production privacy primitive.
- If OCR text naturally contains the literal string `[EMAIL_1]` (extremely unlikely in typical screenshots), restoration would corrupt that position.

### Protection action selection

[services/protection.py](backend/services/protection.py) maps the predicted risk level to a named action:

| Risk level | Action | Reversible |
|---|---|---|
| LOW | MONITOR | Yes |
| MEDIUM | BLUR\_SENSITIVE | Yes |
| HIGH | BLUR\_AND\_ALERT | Yes |
| CRITICAL | BLOCK | No |

**Important:** the `/analyze` endpoint returns the action name and affected entity categories. **No image transformation is applied** — blurring or blocking of the screenshot is not implemented in this prototype. The selection logic specifies what a downstream system should do; it does not modify the image bytes.

### OCR resize benchmark

EasyOCR is slow on full-resolution 1920×1080 screenshots. The benchmark
(`scripts/benchmark_resize.py`, results in `data/resize_eval/results.json`)
evaluated the effect of downscaling to 1600×900 before OCR.

| Metric | Value |
|--------|-------|
| Images evaluated | 20 |
| Input resolution | 1920×1080 → 1600×900 |
| Mean baseline time (full res) | 81.4 s / image |
| Mean resized time | 13.1 s / image |
| Mean speedup | **5.94×** |
| Total baseline detections | 1484 |
| Total resize detections | 1410 |
| Detection loss | 74 (5.0%) |
| Images with detection mismatch | 18 / 20 |
| Images with net detection loss | 15 / 20 |
| Max single-image detection loss | 18 |

**Caveat — extreme outlier dominates the mean**: one image took 1337.6 s at full
resolution but only 14.0 s after resize (95.7× speedup). Removing that image,
the remaining 19 images show a mean speedup of approximately **1.17×** and a
mean baseline time of approximately **15.3 s**. The reported 5.94× mean is not
representative of typical images. The resize optimisation reliably eliminates
catastrophic stalls (likely caused by EasyOCR's internal tiling on high-detail
regions) but provides only modest improvement on typical screenshots.

The 5% detection loss is consistent across images (15 of 20 showed losses, max
18 detections lost on one image). Whether this affects downstream entity
detection depends on whether the lost detections include sensitive entities; that
follow-up analysis was not performed.

---

## Research limitations and unimplemented requirements

The following limitations are stated as confirmed observations from the actual evaluation, not post-hoc rationalisations.

### Proposal requirements not implemented in this prototype

The following requirements appear in the project proposal (TAF J26-IT-308, Member 2 tasks) but are **not implemented** in the current codebase. They are listed here so the research report does not inadvertently claim them.

- **Computer vision integration** — the pipeline processes OCR text only. No image classification, layout analysis, or visual feature extraction is applied to the screenshot pixels.
- **Semantic analysis** — `application_context` is label-encoded as a single integer. No word embeddings, sentence encoders, TF-IDF, topic models, or semantic similarity measures are used.
- **Trained Named Entity Recognition (NER)** — entity detection uses deterministic regex patterns, not a trained NER model (e.g. spaCy, HuggingFace). The pipeline detects structured PII patterns reliably but cannot identify entity types that require contextual understanding.
- **Actual image transformations** — the protection module returns a named action (BLUR\_SENSITIVE, BLOCK, etc.) but does not apply any pixel-level operation to the screenshot.
- **Reversible tokenization framework** — implemented as a prototype in [services/tokenizer.py](backend/services/tokenizer.py). Restoration is ephemeral (same request only); persistent reversible restoration is not supported.
- **Privacy leakage simulation / re-identification attack experiments** — no adversarial or re-identification evaluation was performed.
- **Retrieval utility evaluation** — no downstream retrieval component exists; the claim that privacy protection preserves retrieval utility is not experimentally evaluated.
- **Continual learning** — the personalisation mechanism is feedback-driven threshold recalibration (see Limitation 7 below), not a continual learning algorithm. No EWC, replay buffer, or online model update is implemented.
- **Masking, anonymization, and pseudonymization baselines** — only the rule-based risk scoring baseline has been implemented and compared.

### 1. Dataset size is insufficient for reliable evaluation

The entire annotated dataset contains 50 rows. An 80/20 stratified split produces a 10-row test set. All reported test metrics — accuracy, F1, confusion matrix — are computed over 10 samples. At this scale, a single misclassification changes macro F1 by approximately 0.044. No confidence intervals can be computed with a single train/test split. The reported metrics describe the behaviour of one trained instance on one specific 10-row subset; they cannot support claims about generalisation.

### 2. Severe class imbalance, compounded by insufficient minority-class representation

The label distribution is LOW=38 (76%), MEDIUM=10 (20%), HIGH=2 (4%), CRITICAL=0. With stratified 80/20 splitting:

- Training set: LOW=30, MEDIUM=8, HIGH=2
- Test set: LOW=8, MEDIUM=2, **HIGH=0**

Both HIGH-labelled rows were allocated to the training fold. The test set contains zero HIGH examples. HIGH-class performance cannot be evaluated from this split. Cross-validation is capped at 2 folds because the training fold contains only 2 HIGH rows; with 2-fold CV, each validation fold has 1 HIGH example, making per-fold HIGH metrics unreliable.

### 3. Feature overlap between LOW and MEDIUM renders the classes unseparable by the current feature set

The model uses seven binary entity-presence flags (EMAIL, PHONE, URL, IP\_ADDRESS, DATE, WINDOWS\_PATH, sensitive\_entity\_present) and an encoded application\_context field. Inspection of the 10 MEDIUM-labelled rows reveals that **6 of 10 have all-zero entity flags and no application context** (filled as "Unknown"). These rows have feature vectors identical to 28 of 38 LOW-labelled rows. The two MEDIUM rows in the test set were precisely these all-zero, Unknown-context rows.

The classifier correctly learned that the all-zero, Unknown-context region is predominantly LOW (28 LOW rows vs 6 MEDIUM rows share this feature vector). When confronted with two MEDIUM test rows in that region, it predicted LOW for both. This is not a model failure; it is the expected rational behaviour given a training set where identical feature vectors map to different labels.

**MEDIUM F1 = 0.000 is a confirmed result, reproducible with `random_state=42`.** It reflects a fundamental mismatch between the available features and the labelling criteria, not a bug in the code.

### 4. OCR-derived features do not capture the visual context used during manual labelling

The six all-zero MEDIUM rows were labelled MEDIUM through visual inspection of the screenshot content — context that a human reviewer could observe (e.g., layout, application branding, visible but OCR-unextracted elements) but that OCR entity detection did not capture as structured features. The entity-count feature set assumes that privacy risk correlates with the presence of machine-readable sensitive entities. Several screenshots in this dataset contradict that assumption: they were judged risky by a human annotator for reasons the feature set cannot encode.

This gap between visual interpretation and OCR extraction is an inherent limitation of the current pipeline architecture. Addressing it would require either richer OCR post-processing (e.g., layout analysis, visual classification) or additional annotation columns recording the human-readable reason for each label.

### 5. Annotation quality and independence are unknown

All 50 rows were labelled by a single annotator. No inter-annotator agreement metric (e.g., Cohen's kappa) was computed. The labelling criteria for the MEDIUM class — particularly for rows with no detectable sensitive entities — are not formally specified. It is possible that a second annotator would label some of the all-zero MEDIUM rows as LOW, which would eliminate the feature-space contradiction entirely. This uncertainty cannot be resolved from the existing data.

### 6. No generalisation claim is supported

The prototype was trained and evaluated on screenshots drawn from a single collection process. The test set was not withheld before any model or rule design decisions were made. The reported accuracy of 0.80 reflects performance on a 10-row sample from the same distribution as the training data. No evidence is available about performance on screenshots from different users, applications, or time periods.

### 7. Personalisation is threshold adjustment, not model adaptation

The adaptive component adjusts the model's decision thresholds based on user feedback — it does not retrain the model or learn new feature representations. This design is appropriate given the data volume (per-user labelled examples are sparse) but means the system cannot correct for fundamental feature-class mismatches on a per-user basis. A user who consistently labels all-zero-feature screenshots as MEDIUM will receive threshold adjustments, but the model's underlying inability to separate those rows from LOW will persist.

---

## Repository hygiene

Files excluded from version control (`.gitignore`):

- `*.joblib` — trained model (learned patterns from private screenshots)
- `backend/data/caprlm.db` — SQLite database (user data, analysis history)
- `backend/data/profiles/` — legacy JSON profiles (preserved for migration reference)
- `backend/data/training_log.json` — label distributions
- `backend/data/screenshots/` and image files — raw screenshots
- All pilot and processing output directories

The annotation CSV (`CAPRLM_annotation_50.csv`) is intentionally tracked.

---

## Tests

```
cd Member2
python -m pytest backend/tests/ -v
```

209 tests covering OCR pipeline, entity detection, feature extraction, rule baseline, baseline comparison evaluation, risk prediction, reversible tokenization (T5), user profiles, model training, database schema/migration, and the FastAPI endpoints.

## Native desktop model workbench

The PySide6 app under `desktop_app/` inspects the existing serialized model and calls `backend.services.risk_prediction` directly for feature-based predictions. It does not make local HTTP requests, retrain the model, or process screenshots. The evaluation page reads the latest recorded held-out evaluation in `backend/data/training_log.json`; it does not calculate or substitute training metrics. A Random Forest tree view shows one estimator at a time and truncates the display at depth 4.

From a Windows terminal in this `Member2` directory, install the project dependencies and launch:

```
.venv\Scripts\python.exe -m pip install -r requirements.txt
run_desktop_app.bat
```

The launcher only uses `.venv\Scripts\python.exe` and gives an error if that project interpreter is absent. If the checked-in environment does not work on the machine, create a project environment with Python 3.14 and install `requirements.txt` into it before launching. The current repository environment could not be executed in the implementation shell because its base Python path was inaccessible.

Run the full backend and desktop integration tests from `Member2` with:

```
.venv\Scripts\python.exe -m pytest backend/tests/ -v
```

Research caution: the saved model was trained from 50 imbalanced annotations (LOW 38, MEDIUM 10, HIGH 2); the recorded 10-row held-out split contains no HIGH examples. `CRITICAL` is not a trained class. Feature importance is not causal evidence, and the displayed probability estimates have not been evaluated for calibration. Manually entered desktop features do not constitute screenshot analysis.
