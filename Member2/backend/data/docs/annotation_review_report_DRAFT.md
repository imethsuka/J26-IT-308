# CAPRLM Annotation Review Report — DRAFT

**Project:** J26-IT-308 · Member 2 · CAPRLM  
**Report date:** 2026-10-15  
**Status:** DRAFT — for human reviewer. No labels were changed by this report.  
**Prepared from:** read-only inspection of annotation CSV, candidates.csv, manifest.json, and annotation_guidelines_DRAFT.md.  
**Screenshot files:** inspected for existence only; none were opened, copied, or uploaded.

---

## How to use this report

Each section below covers one or more anomalous records. For each record the report provides:

- The complete `screenshot_id` (UUID + `.jpg`)
- The annotation CSV flags and label (what a human wrote)
- The current-pipeline candidates.csv flags and `auto_label_candidate` (what the running OCR pipeline computed on 2026-10-14)
- The manifest entry (processing status, elapsed seconds, any errors)
- Whether the screenshot file is present on disk
- What can be concluded from metadata alone
- Exactly what a human reviewer must do

**The report does not assert that either the annotation label or the pipeline label is correct.**  
Every item below requires a human to open the screenshot and make a determination.

---

## Data sources

| File | Role |
|---|---|
| `Member2/backend/data/CAPRLM_annotation_50.csv` | Human-annotated ground truth (50 rows) |
| `Member2/backend/data/processing_output/candidates.csv` | Current-pipeline OCR output (805+ rows, OCR may still be running) |
| `Member2/backend/data/processing_output/manifest.json` | Per-image processing log |
| `Member2/backend/data/docs/annotation_guidelines_DRAFT.md` | Labelling rules (DRAFT v0.2, not yet approved) |
| `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\` | Screenshot image files |

---

## Part A — Priority 1: Records that materially affect model evaluation

### A1 — `02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg`
**Issue: HIGH label with all annotation entity flags = 0. Constitutes 50 % of the HIGH class.**

**Annotation CSV:**
```
screenshot_id:          02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg
application_context:    (empty)
EMAIL:                  0
PHONE:                  0
URL:                    0
IP_ADDRESS:             0
DATE:                   0
WINDOWS_PATH:           0
sensitive_entity_present: 0
privacy_risk_label:     HIGH
```

**Candidates.csv (current pipeline, row index 67, processed 2026-10-14T04:56:33Z):**
```
application_context:    Unknown
EMAIL:                  0
PHONE:                  0
URL:                    0
IP_ADDRESS:             0
DATE:                   1   ← date_count = 2
WINDOWS_PATH:           0
sensitive_entity_present: 1
auto_label_candidate:   LOW
ocr_text_length:        617
elapsed_seconds:        12.12
```

**Manifest:** status = `done`, error = `null`, elapsed = 12.12 s, output_row_index = 67.  
No processing error was recorded.

**Screenshot file:** EXISTS at `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg`

**What metadata tells us:**
- The current pipeline found extractable text (617 characters) and detected two date matches.
- The annotation CSV records all entity flags as 0, including DATE. This means the annotation was produced by a different pipeline run or the flags were entered manually without running OCR.
- The pipeline auto-labelled this LOW (DATE-only per the proposed Policy 2 default). The annotation assigned HIGH.
- There is no `application_context` recorded in the annotation; the pipeline defaulted to `Unknown`.
- The disagreement could indicate: (a) a high-risk visual element that OCR could not extract (e.g. an email address rendered as an image, a visible credential), justifying HIGH per annotation_guidelines §3.1; or (b) an annotation error.
- This row is 50 % of the HIGH class. If the label is wrong, no valid HIGH training example remains.

**Required human action:**
1. Open `02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg`.
2. Look for any direct personal identifier (email, phone, IP) visible on-screen that OCR may have failed to extract.
3. If such content is visible: confirm HIGH, record the visual reason in `annotation_notes` (e.g. `"email address visible in browser address bar, not captured by OCR"`), and set `ocr_detection_gap = 1` when those columns are added.
4. If no such content is visible and only a date or generic content is present: the label should be reviewed for correction to LOW or MEDIUM (escalate for adjudication per §8 of the guidelines). Document the reason.
5. Record `application_context`.

---

### A2 — `02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg`
**Issue: MEDIUM label despite EMAIL = 1. EMAIL should trigger HIGH per annotation_guidelines §3.1.**

**Annotation CSV:**
```
screenshot_id:          02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg
application_context:    (empty)
EMAIL:                  1
PHONE:                  0
URL:                    0
IP_ADDRESS:             0
DATE:                   1
WINDOWS_PATH:           0
sensitive_entity_present: 1
privacy_risk_label:     MEDIUM
```

**Candidates.csv (current pipeline, row index 66, processed 2026-10-14T04:56:21Z):**
```
application_context:    Unknown
EMAIL:                  1   ← email_count = 1
PHONE:                  0
URL:                    0
IP_ADDRESS:             0
DATE:                   1   ← date_count = 1
WINDOWS_PATH:           0
sensitive_entity_present: 1
auto_label_candidate:   HIGH
ocr_text_length:        531
elapsed_seconds:        11.24
```

**Manifest:** status = `done`, error = `null`, elapsed = 11.24 s, output_row_index = 66.  
No processing error was recorded.

**Screenshot file:** EXISTS at `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg`

**What metadata tells us:**
- Both the annotation CSV and the current pipeline agree: EMAIL = 1. The entity flag is not in dispute.
- The annotation_guidelines §3.1 states: "Assign HIGH when the screenshot contains at least one direct personal identifier … An email address (EMAIL = 1)."
- The annotation label is MEDIUM, which contradicts the written rule as of guidelines v0.2.
- The pipeline auto-labelled HIGH, consistent with the rule.
- This is either (a) an annotation error (human assigned one level too low), or (b) a deliberate annotator judgment that this particular email carried negligible privacy risk (e.g. a generic system address, a display placeholder, or a test address), which the annotator did not document.
- There is no `annotation_notes` column in the current CSV to record such justification.

**Required human action:**
1. Open `02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg`.
2. Examine the visible email address. If it is a real personal or organisational email that could identify an individual, correct the label to HIGH and flag as adjudicated.
3. If it is a generic, system, or clearly non-personal address (e.g. `noreply@example.com`, a template placeholder), and the annotator intentionally chose MEDIUM, that judgment should be documented in `annotation_notes` and flagged for a second reviewer. The research team must then decide whether the EMAIL rule in §3.1 needs a qualifier.
4. Do not leave the label as MEDIUM without documentation regardless of the outcome.
5. Record `application_context`.

---

### A3 — `01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg`
**Issue: Annotation and current-pipeline entity flags disagree entirely. Annotation: URL=1; pipeline: PHONE=1, IP_ADDRESS=1, URL=0.**

**Annotation CSV:**
```
screenshot_id:          01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg
application_context:    Google Chrome + YouTube
EMAIL:                  0
PHONE:                  0
URL:                    1   ← annotation detects URL
IP_ADDRESS:             0
DATE:                   1
WINDOWS_PATH:           0
sensitive_entity_present: 1
privacy_risk_label:     LOW
```

**Candidates.csv (current pipeline, row index 38, processed 2026-10-14T04:50:10Z):**
```
application_context:    Unknown
EMAIL:                  0
PHONE:                  1   ← phone_count = 1
URL:                    0   ← no URL detected
IP_ADDRESS:             1   ← ip_count = 1
DATE:                   1   ← date_count = 1
WINDOWS_PATH:           0
sensitive_entity_present: 1
auto_label_candidate:   HIGH
ocr_text_length:        1995
elapsed_seconds:        17.87
```

**Manifest:** status = `done`, error = `null`, elapsed = 17.87 s, output_row_index = 38.  
No processing error was recorded.

**Screenshot file:** EXISTS at `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg`

**What metadata tells us:**
- The annotation CSV records URL=1, application_context = `Google Chrome + YouTube`, label = LOW.
- The current pipeline records PHONE=1, IP_ADDRESS=1, URL=0, auto_label = HIGH.
- The entity flags are entirely different between the two versions. This is the largest flag disagreement of any annotated record.
- Possible explanations: the annotation was produced by a significantly different pipeline version whose URL regex matched something the current version does not; or the current pipeline's phone/IP regexes match something that was not present (or not visible) when annotation was done.
- The annotation label is LOW. Under the annotation_guidelines §3.2, even URL=1 alone (the annotation version) should yield MEDIUM. The annotator assigned LOW, which the guidelines flag as requiring documentation and a policy decision (Policy 8 — URL carve-out). Under the current-pipeline flags (PHONE=1, IP=1), the label would be HIGH.
- The `application_context` was correctly recorded in the annotation CSV (`Google Chrome + YouTube`) but shows as `Unknown` in the candidates output (the pipeline does not infer application context from screen content).

**Required human action:**
1. Open `01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg`.
2. Check whether a URL, a phone number, and/or an IP address are visible in the screenshot.
3. Determine which pipeline version's entity flags are correct, or whether both versions partially captured the content.
4. Assign the correct label based on what is actually visible, following annotation_guidelines §3 (HIGH if phone or IP is present; MEDIUM if only URL; LOW only if no flag is warranted).
5. Document findings in `annotation_notes` (once that column exists).
6. This record directly informs Policy 8 (URL carve-out) — the research team should note the outcome.

---

## Part B — Six MEDIUM records with all-zero entity flags

These six records share the annotation label MEDIUM and have all entity flags set to 0 in the annotation CSV. They also have DATE=1 in the candidates pipeline output. They are the confirmed cause of MEDIUM F1 = 0.000 in the current model evaluation: their feature vectors are indistinguishable from LOW rows with all-zero flags, so no classifier can separate them using the current feature set.

**Summary table:**

| screenshot_id | Ann. flags | Ann. label | Pipeline DATE | Pipeline date_count | Pipeline auto_label | OCR text len | Screenshot |
|---|---|---|---|---|---|---|---|
| `00fa95dd-5a9e-458b-a071-fc2f8913e888.jpg` | All 0 | MEDIUM | 1 | 2 | LOW | 686 | EXISTS |
| `01583a1d-7ccf-49d9-99c8-7ebf3b94cf01.jpg` | All 0 | MEDIUM | 1 | 1 | LOW | 1256 | EXISTS |
| `01a758f4-84ee-400c-a583-b7b70573c076.jpg` | All 0 | MEDIUM | 1 | 2 | LOW | 857 | EXISTS |
| `024fd28f-aeb8-42dc-8484-9a8e2cd3ecf5.jpg` | All 0 | MEDIUM | 1 | 1 | LOW | 478 | EXISTS |
| `033abfbe-b7f6-4c58-a769-8ffe082b38e4.jpg` | All 0 | MEDIUM | 1 | 1 | LOW | 792 | EXISTS |
| `034e7035-d661-417e-8cab-6e3131d7a671.jpg` | All 0 | MEDIUM | 1 | 1 | LOW | 248 | EXISTS |

All six: manifest status = `done`, error = `null`, no processing errors recorded.

### Individual records

---

#### B1 — `00fa95dd-5a9e-458b-a071-fc2f8913e888.jpg`

**Annotation CSV:** all entity flags = 0, sensitive_entity_present = 0, application_context = (empty), label = MEDIUM.  
**Candidates.csv (row 20, processed 2026-10-14T04:45:59Z):** DATE=1 (date_count=2), sensitive_entity_present=1, ocr_text_length=686, auto_label=LOW.  

The current pipeline found text (686 chars) and two date matches. The annotation recorded all flags as zero. Either the annotation was produced before the DATE regex was introduced/corrected, or the annotator observed non-OCR visual content that warranted MEDIUM.

**Required human action:** Open the screenshot. If a URL, browser activity, sensitive visual context, or non-extracted identifier is visible, confirm MEDIUM and record the visual reason in `annotation_notes`. If only a date or generic content is visible, review whether LOW is correct and adjudicate.

---

#### B2 — `01583a1d-7ccf-49d9-99c8-7ebf3b94cf01.jpg`

**Annotation CSV:** all entity flags = 0, sensitive_entity_present = 0, application_context = (empty), label = MEDIUM.  
**Candidates.csv (row 28, processed 2026-10-14T04:47:43Z):** DATE=1 (date_count=1), sensitive_entity_present=1, ocr_text_length=1256, auto_label=LOW.  

1,256 characters of OCR text extracted but no non-date entity detected. MEDIUM label is unexplained by any flag.

**Required human action:** Same as B1.

---

#### B3 — `01a758f4-84ee-400c-a583-b7b70573c076.jpg`

**Annotation CSV:** all entity flags = 0, sensitive_entity_present = 0, application_context = (empty), label = MEDIUM.  
**Candidates.csv (row 37, processed 2026-10-14T04:49:53Z):** DATE=1 (date_count=2), sensitive_entity_present=1, ocr_text_length=857, auto_label=LOW.  

**Required human action:** Same as B1.

---

#### B4 — `024fd28f-aeb8-42dc-8484-9a8e2cd3ecf5.jpg`

**Annotation CSV:** all entity flags = 0, sensitive_entity_present = 0, application_context = (empty), label = MEDIUM.  
**Candidates.csv (row 49, processed 2026-10-14T04:52:35Z):** DATE=1 (date_count=1), sensitive_entity_present=1, ocr_text_length=478, auto_label=LOW.  

**Required human action:** Same as B1.

---

#### B5 — `033abfbe-b7f6-4c58-a769-8ffe082b38e4.jpg`

**Annotation CSV:** all entity flags = 0, sensitive_entity_present = 0, application_context = (empty), label = MEDIUM.  
**Candidates.csv (row 74, processed 2026-10-14T04:58:07Z):** DATE=1 (date_count=1), sensitive_entity_present=1, ocr_text_length=792, auto_label=LOW.  

**Required human action:** Same as B1.

---

#### B6 — `034e7035-d661-417e-8cab-6e3131d7a671.jpg`

**Annotation CSV:** all entity flags = 0, sensitive_entity_present = 0, application_context = (empty), label = MEDIUM.  
**Candidates.csv (row 75, processed 2026-10-14T04:58:18Z):** DATE=1 (date_count=1), sensitive_entity_present=1, ocr_text_length=248, auto_label=LOW.  

This record has the smallest OCR text length of the six (248 chars). The short text may indicate a screenshot with sparse visible content, which could support a visual-context MEDIUM judgment (e.g. a browser open to a sensitive page where only minimal text is present).

**Required human action:** Same as B1; the short text length warrants extra scrutiny.

---

## Part C — Additional anomalies from annotation_guidelines_DRAFT.md §9

The guidelines identify one further record in their review queue beyond Parts A and B.

### C1 — `01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg` (already covered as A3)

The guidelines identify this row as the one URL=1/LOW anomaly in §9.3. Full detail is in Part A3 above.

---

## Part D — Cross-cutting issues identified during inspection

### D1 — Entity flags in annotation CSV diverge from current-pipeline output on at least four annotated rows

Beyond the records in Parts A–B, the following annotated rows were spot-checked against candidates.csv and show flag disagreement (not detailed individually in this report, but noted for completeness):

| screenshot_id | Annotation PHONE | Pipeline PHONE | Annotation label | Pipeline auto_label |
|---|---|---|---|---|
| `0024e4cb-aacb-44f7-8fe2-5c633f1ec67f.jpg` | 0 | 1 | LOW | HIGH |
| `00c34dc6-612f-43cb-9b9f-297d92b6868a.jpg` | 0 | 1 (count=3) | LOW | HIGH |
| `00e7560f-f51a-4d8a-ad26-f6461fe5d6de.jpg` | 0 | 1 | LOW | HIGH |

These rows carry LOW labels in the annotation CSV. If the current pipeline is correct that they contain phone numbers, the labels may understate their risk. Human review of these three screenshots is also recommended before any retraining that uses the annotation CSV entity flags as features.

### D2 — application_context is blank for 36 of 50 annotation rows

The annotation_guidelines §4.1 require `Unknown` (not blank) for unidentified context. Blank and `Unknown` will encode differently in the pipeline. This affects all six Part B records and row A1 and A2 above. Backfilling requires Policy 4 decision (versioned copy vs. in-place edit).

### D3 — Candidates pipeline uses two forms of Unknown

Rows processed in the first batch use `UNKNOWN` (all caps); later rows use `Unknown` (title case). If the candidates.csv is ever joined to the annotation CSV or used directly in training, these must be normalised to one form before label-encoding.

### D4 — No `annotation_notes` column exists in the annotation CSV

Seven of the nine records reviewed in this report require `annotation_notes` per the annotation_guidelines. The column does not exist. It should be added to the next annotation file version (pending Policy 6 approval) before any new rows are added.

---

## Part E — Summary: what must be resolved before model retraining

| Priority | Item | Blocker for retraining? |
|---|---|---|
| 1 | Visual review and label determination for `02f39c8f` (A1) | Yes — 50 % of HIGH class; HIGH F1 cannot be computed without this |
| 1 | Visual review and label determination for `02e86b37` (A2) | Yes — EMAIL=1/MEDIUM contradicts the labelling rule |
| 1 | Visual review and flag/label determination for `01bf28e2` (A3) | Yes — entity flags entirely disagree between annotation and pipeline |
| 1 | Visual review for all six MEDIUM/all-zero records (B1–B6) | Yes — these are the direct cause of MEDIUM F1 = 0.000 |
| 2 | Visual review of `0024e4cb`, `00c34dc6`, `00e7560f` (D1) | Recommended before retraining if entity flags are used as features |
| 2 | Policy 4 decision on application_context backfill | Recommended before retraining if application_context is used as a feature |
| 3 | Policy 6 decision on annotation_notes column | Required before expanding the annotation set |
| 3 | Normalise `Unknown`/`UNKNOWN`/blank across both CSVs | Required if candidates.csv is used for training without preprocessing |

---

*This report was generated by read-only file inspection on 2026-10-15.*  
*No annotation CSV, candidates.csv, manifest, model artifact, or training log was modified.*  
*OCR processes were not interrupted.*  
*Screenshot files were checked for existence only; none were opened, copied, or uploaded.*
