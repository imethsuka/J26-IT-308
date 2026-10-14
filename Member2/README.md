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
data/profiles/<user_id>.json   ← adaptive per-user thresholds (not committed)
```

**Storage**: entirely file-based. No relational database is used or required. Per-user "adaptive memory" is implemented as JSON profiles with feedback-driven threshold recalibration.

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

36 of 50 rows have no recorded `application_context` (filled as "Unknown" during training). The dataset is heavily imbalanced.

### ML model — Random Forest (v1.0.0)

Training split: 80/20 stratified. Encoder fitted on training fold only (no leakage).

| Metric | Value |
|--------|-------|
| CV folds | 2 (capped by HIGH class size) |
| CV macro F1 (mean ± std) | 0.353 ± 0.075 |
| Test macro F1 (n=10) | 0.444 |
| Test accuracy | 0.80 |
| LOW F1 | 0.889 |
| MEDIUM F1 | 0.000 |
| HIGH support in test | 0 (insufficient minority class) |

### Rule baseline — deterministic classifier

Evaluated on the full 50-row annotation CSV.

| Metric | Value |
|--------|-------|
| Accuracy | 0.78 |
| Macro F1 | 0.39 |
| LOW F1 | 0.89 |
| MEDIUM F1 | 0.29 |
| HIGH F1 | 0.00 |

### Comparison

Both classifiers struggle with MEDIUM and HIGH due to class imbalance and all-zero entity flags in several MEDIUM/HIGH rows. The ML model's advantage (threshold personalisation) is not reflected in static F1 scores but is the primary research contribution.

### Known limitations

1. **n=50 is insufficient for robust generalisation.** All reported metrics have very wide confidence intervals.
2. **CRITICAL class is absent** from the training data; neither classifier can predict it.
3. **Entity columns are binary flags** in the CSV; integer counts from OCR are binarised at inference (`count > 0 → 1`) to match.
4. **Several MEDIUM/HIGH rows have all-zero entity flags**, meaning the rule-based features cannot separate them from LOW. This limits the separability achievable with the current feature set.
5. **Labels are manually annotated**; inter-annotator agreement is unknown.
6. **Personalisation is threshold adjustment**, not per-user model retraining. Insufficient per-user labelled data makes retraining impractical.
7. The **test macro F1 of 0.444 is evaluated on 10 samples** — the result is not statistically stable.

---

## Repository hygiene

Files excluded from version control (`.gitignore`):

- `*.joblib` — trained model (learned patterns from private screenshots)
- `backend/data/profiles/` — per-user JSON profiles
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

107 tests covering OCR pipeline, entity detection, feature extraction, rule baseline, risk prediction, user profiles, model training, and the FastAPI endpoints.
