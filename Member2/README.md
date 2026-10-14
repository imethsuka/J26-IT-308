# Member 2 — CAPRLM

**Context-Aware Privacy Intelligence and Adaptive Risk Learning Framework**
J26-IT-308 · Research Prototype

---

## Overview

CAPRLM is a desktop privacy-risk analysis prototype. It processes screenshots via OCR, detects sensitive entities (email, phone, URL, IP address, file paths), classifies privacy risk using a trained Random Forest model, selects a protection action, and adapts decision thresholds to per-user feedback over time.

The system implements a full research loop: batch OCR pipeline → annotated training data → ML model training → live analysis API → user feedback → personalised threshold recalibration.

---

## Architecture

```
screenshots/            ← raw images (never committed)
    │
    ▼
scripts/process_screenshots.py
    │  EasyOCR (CPU), manifest-based resume, Windows subprocess timeout
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

Returns: `ocr_summary`, `detection_results`, `extracted_features`, `risk_prediction`, `protection_selection`, `user_profile_summary`.

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

Evaluated on the full 50-row annotation CSV (not a held-out split).

| Metric | Value |
|--------|-------|
| Accuracy | 0.78 |
| Macro F1 | 0.39 |
| LOW F1 | 0.89 |
| MEDIUM F1 | 0.29 |
| HIGH F1 | 0.00 |

Note: rule-baseline metrics are computed on the same data used to design the rules; they are not held-out results and overestimate generalisation performance.

### Comparison

The ML model achieves macro F1 = 0.444 on a 10-row test set; the rule baseline achieves macro F1 = 0.39 on the full 50-row set under non-holdout conditions. These figures are not directly comparable and neither is statistically reliable at these sample sizes.

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

## Research limitations

The following limitations are stated as confirmed observations from the actual evaluation, not post-hoc rationalisations.

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

124 tests covering OCR pipeline, entity detection, feature extraction, rule baseline, risk prediction, user profiles, model training, database schema/migration, and the FastAPI endpoints.
