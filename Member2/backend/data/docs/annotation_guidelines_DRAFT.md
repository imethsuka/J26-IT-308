# CAPRLM Annotation Guidelines — DRAFT v0.2 for Human Review

**Project:** J26-IT-308 · Member 2 · CAPRLM  
**Status:** DRAFT — not approved. All unresolved policy decisions are marked ⚠️ REQUIRES APPROVAL.  
**Version:** 0.2 (revised 2026-10-15 — fixes C1, C2, C3, M1, M3, M5 from review report)  
**Applies to:** `CAPRLM_annotation_50.csv` and any future annotation files.  
**Do not use as a finalised standard until all policy decisions are resolved and a supervisor approves.**

---

## Revision history

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-10-15 | Initial draft |
| 0.2 | 2026-10-15 | Fixed HIGH/CRITICAL contradiction (C1); unified column name to `annotation_notes` (C3); added `01bf28e2` to review queue (C2/M5); corrected DATE-only count to 9 (M1); corrected Section 10.2 arithmetic (M3); added URL-only clarification (M2/requirement 8) |

---

## 1. Purpose

This document defines the labelling rules that human annotators must apply when
assigning `privacy_risk_label` values to screenshot rows. It is intended to:

- Resolve the ambiguities identified in the audit of the existing 50-row dataset.
- Ensure that the feature set actually computed by the pipeline
  (`sensitive_detection.py`, `feature_extraction.py`) corresponds to what
  annotators use to assign labels.
- Support reproducibility: a second annotator following these rules should reach
  the same label on the same screenshot.

**Important:** The annotation `privacy_risk_label` column uses three values:
LOW, MEDIUM, HIGH. The rule-based baseline (`rule_baseline.py`) internally
produces a fourth level, CRITICAL, for the email+phone combination. CRITICAL
does not exist as an annotation label in the current schema. This divergence is
documented in Section 3.4 and Policy 3. Annotators must follow the three-label
schema unless and until a supervisor approves an extension.

---

## 2. System Scope (Read Before Labelling)

The CAPRLM pipeline is **text-only**. EasyOCR extracts text characters from the
screenshot image. All entity detection runs on that extracted text via regex
patterns (`sensitive_detection.py`). Six entity categories are detected:

| Category | Detected by | Example matched text |
|---|---|---|
| EMAIL | Regex | `user@domain.com` |
| PHONE | Regex | `+1 (555) 123-4567` |
| URL | Regex | `https://example.com/path` |
| IP_ADDRESS | Regex | `192.168.1.1` |
| DATE | Regex | `2024-10-15`, `15/10/2024` |
| WINDOWS_PATH | Regex | `C:\Users\name\Documents\file.txt` |

**The DATE regex matches only numeric formats** (`dd/mm/yyyy`, `yyyy-mm-dd`, and
similar numeric separators). Dates written as prose — "October 15, 2026" or
"15 Oct 26" — are **not captured** and will produce `DATE = 0` in the pipeline
output even when a human annotator can see a date in the screenshot.

**What the system cannot detect via OCR:**

- Sensitive data visible as images (e.g. photos of ID cards, screenshots
  of PDFs rendered as images).
- Sensitive UI text rendered as anti-aliased or styled pixels that EasyOCR
  cannot segment (e.g. some CAPTCHA elements, anti-screen-capture overlays).
- Layout-level context: a screenshot where the visible application interface
  implies a sensitive operation even though no matching text is present.
- Prose-format dates, names, or locations that do not match the numeric
  regex patterns above.

These gaps are documented in README.md Limitations 3 and 4. Labels for
OCR-invisible risk must be documented by the annotator (see Sections 6 and 7).

**What this prototype does NOT implement:**
- Computer vision, image classification, or pixel-level layout analysis.
- A trained Named Entity Recognition (NER) model.
- Actual image blurring, blocking, or any pixel-level transformation.
- Persistent reversible tokenization across requests.
- Continual learning or online model updating.

These unimplemented capabilities are documented in README.md. Annotation
guidelines and dataset targets are designed around what the pipeline
**actually does**, not what a future version might do.

---

## 3. Risk Labels — Definitions

The annotation schema uses three labels: **LOW**, **MEDIUM**, **HIGH**.

A note on the rule baseline: `rule_baseline.py` produces a fourth internal
level, **CRITICAL** (email AND phone both present). This level has no
corresponding annotation label in the current schema. See Section 3.4 for the
unresolved policy decision.

### 3.1 HIGH

Assign HIGH when the screenshot contains at least one **direct personal
identifier** that, if exposed, could identify a specific individual or enable
direct contact or impersonation:

- An email address (`EMAIL = 1`)
- A phone number (`PHONE = 1`)
- An IP address (`IP_ADDRESS = 1`)

**Email+phone combination:** If both `EMAIL = 1` and `PHONE = 1`, the
rule baseline assigns CRITICAL (not HIGH). Under the current three-label
annotation schema, an annotator must assign **HIGH** for this combination
(there is no CRITICAL label to assign). This creates a deliberate divergence
between annotation labels and baseline outputs on email+phone rows.
⚠️ **See Policy 3** for the decision on whether to add CRITICAL as a
fourth annotation label.

**Annotating HIGH when all OCR entity flags are zero:**
This is permitted only when the annotator can see sensitive content that OCR
failed to extract (e.g. a photo of a credential visible in the screenshot,
a browser URL bar whose text the OCR did not capture). In that case entity
flags will be 0, but the label may still be HIGH. The annotator **must**
document the visual reason in `annotation_notes` (see Section 7).
⚠️ **REQUIRES APPROVAL (Policy 1):** Whether a HIGH label with all-zero
OCR entity flags is valid in this dataset, and how such rows should be handled
during model training (since the feature vector is identical to LOW rows with
all-zero flags).

---

### 3.2 MEDIUM

Assign MEDIUM when the screenshot contains evidence of **elevated but not
directly identifying risk**. Each criterion below is a sufficient condition for
MEDIUM when no HIGH criterion is also met:

- A URL was detected by OCR (`URL = 1`) — the user's browsing activity or
  service is exposed.
- A Windows file path was detected (`WINDOWS_PATH = 1`) — internal directory
  structure or personal folder names are exposed.
- The application context is a browser or content-sharing context (Google
  Chrome, YouTube, Firefox, Edge, Brave) **and** at least one entity of any
  kind is present (`sensitive_entity_present = 1`).
- The annotator judges that the visible visual context implies elevated risk
  even though no OCR entity was extracted (see Section 6).

**Important — these entity flags do not automatically determine the label:**
The criteria above are guidelines, not overrides of human judgement. An
annotator reviewing a screenshot may find that a detected URL is a system
internal link with no privacy exposure, or that application context provides
strong mitigating context. When the annotator departs from a criterion above,
they must record the reason in `annotation_notes`. All such departures and
any resulting anomalies must be flagged for adjudication (Section 8).

⚠️ **REQUIRES APPROVAL (Policy 2 — DATE-only):**
The existing dataset contains **9 rows** where `DATE = 1`, all other flags are
0, and the label is LOW. (These are confirmed from the annotation CSV; rows
where URL or any other flag is also 1 are excluded from this count.) A date
alone (e.g. a calendar visible in a ChatGPT window) implies low direct
identifiability, consistent with those existing LOW labels. The proposed rule:

> **DATE detected alone (`DATE = 1`, all other flags = 0) → LOW.**
> DATE detected alongside any other flag → apply the highest applicable rule
> for the combination.

Adopting this rule preserves all 9 existing DATE-only LOW annotations.
Rejecting it would require re-reviewing those rows.

---

### 3.3 LOW

Assign LOW when none of the HIGH or MEDIUM criteria are met and the annotator
does not identify visual context that warrants elevation:

- All entity flags are 0, no OCR-visible identifiers, and no extraordinary
  visual risk context.
- DATE detected alone (`DATE = 1`, all other flags = 0) — see Policy 2 above.

---

### 3.4 CRITICAL — Not a current annotation label

The rule-based baseline (`rule_baseline.py`) produces CRITICAL when both
`email >= 1` and `phone >= 1`. CRITICAL does not exist in the current
annotation schema (`CAPRLM_annotation_50.csv`) and must not be written into
the `privacy_risk_label` column.

Under the current schema, annotators assign **HIGH** for email+phone rows
(see Section 3.1). This means that if `rule_baseline.py` is evaluated against
annotations, email+phone rows will show a label of HIGH in the annotation CSV
and CRITICAL in the baseline output — a known, documented divergence.

⚠️ **REQUIRES APPROVAL (Policy 3):** Whether CRITICAL should be added as a
fourth annotation label before annotation expansion. Adding it requires:
(a) a schema change to the annotation CSV,
(b) a decision on whether existing HIGH rows should be retrospectively split,
(c) updating `train_model.py` and evaluation scripts.
Until a supervisor approves this change, annotators use the three-label schema.

---

## 4. `application_context` — Recording Rules

The `application_context` field records the application or context visible
in the screenshot.

### 4.1 Permitted values

Use the most specific single application name visible in the screenshot. If
multiple applications are clearly visible (split-screen, overlay, or
picture-in-picture), use the format `"App1 + App2"`. Examples from the
existing dataset:

- `ChatGPT`
- `VS Code`
- `VS Code + ChatGPT`
- `Google Chrome`
- `Google Chrome + YouTube`
- `YouTube`
- `Word`
- `Desktop`

For screenshots where no identifiable application is visible, use `Unknown`.
**Do not leave the field blank.** Blank values in the pipeline are treated as
"Unknown" during label encoding, but blank and Unknown produce different
downstream behaviour if the encoding is case-sensitive or if future versions
distinguish them.

### 4.2 Backfilling existing empty rows

36 of the 50 existing annotation rows have empty `application_context`. These
should be reviewed and filled by a human annotator who reviews each screenshot.

⚠️ **REQUIRES APPROVAL (Policy 4):** Whether to backfill these 36 rows by
modifying `CAPRLM_annotation_50.csv` directly, or by creating a separate
versioned CSV (e.g. `CAPRLM_annotation_50_v2.csv`) that includes corrected
context values. The research team must decide before any model retraining that
uses `application_context` as a feature.

---

## 5. Entity Flag Columns — Annotation Convention

The entity flag columns (EMAIL, PHONE, URL, IP_ADDRESS, DATE, WINDOWS_PATH)
and `sensitive_entity_present` are **derived from OCR pipeline output**.
They are not filled manually by human annotators.

When adding a new screenshot to the annotation dataset:
1. Run the image through the OCR pipeline (`process_screenshots.py`) to
   generate a `candidates.csv` row with pipeline-computed flags.
2. Copy the row's flag values into the annotation CSV without modification.
3. Assign the `privacy_risk_label` based on those flags plus visual review.

If a human annotator reviewing a row believes the pipeline-computed flags are
incorrect (e.g. OCR missed an email address visible in the screenshot):
- Do **not** edit the flag values in `candidates.csv` or the annotation CSV.
- Record the discrepancy in `annotation_notes`.

⚠️ **REQUIRES APPROVAL (Policy 5):** Whether a `human_corrected_flags`
column or equivalent mechanism should be added to record cases where the
human reviewer disagrees with pipeline-computed flags.

---

## 6. Screenshots With No OCR-Detectable Entities

A screenshot may warrant a MEDIUM or HIGH label even when all entity flags
are zero. This is expected; it is documented as a known limitation in
README.md Limitation 4 and is the confirmed cause of MEDIUM F1 = 0.000 in
the current evaluation (README Limitation 3).

When assigning a non-LOW label to a row with all-zero flags:

1. Assign the appropriate risk label based on what you can see in the
   screenshot.
2. Set `annotation_notes` to a brief description of the visual content that
   justifies the label (e.g. `"Browser URL bar visible but not extracted by
   OCR"`, `"Visible employee names rendered as image text"`). This field is
   required whenever the label cannot be fully justified by the entity flags.
3. Set `ocr_detection_gap = 1` (see Section 7 for the proposed column).

This information will allow the research team to:
- Quantify how many cases are invisible to the current text-only feature set.
- Decide in a future iteration whether to augment features or treat
  OCR-gap rows as a separate annotation tier excluded from model training.

---

## 7. Proposed Annotation Schema Extension

The current schema has 10 columns. The following additional columns are
proposed to support quality control and gap documentation. All column names
below use the consistent name `annotation_notes` for the free-text field.

⚠️ **REQUIRES APPROVAL (Policy 6):** All columns below are proposals. None
should be added to `CAPRLM_annotation_50.csv` until approved. If approved,
they should be added to a new versioned annotation file, not to the protected
original.

| Column | Type | Description |
|---|---|---|
| `annotation_notes` | string | Free-text explanation; **required** when `privacy_risk_label` cannot be fully justified by the entity flags alone, or when the annotator departs from the label criteria in Section 3 |
| `ocr_detection_gap` | 0/1 | 1 when the label was influenced by visual content not captured by OCR |
| `annotator_id` | string | Identifier of the annotator (enables inter-annotator agreement analysis) |
| `annotation_date` | ISO-8601 date | Date the row was labelled |
| `adjudicated` | 0/1 | 1 when a second reviewer has confirmed or changed the label |
| `adjudicated_label` | string | Final label after adjudication (blank if not yet adjudicated) |
| `adjudicated_by` | string | Identifier of the adjudicating reviewer |

---

## 8. Uncertain and Borderline Cases

When an annotator is uncertain about the correct label:

1. Assign the label that best fits the written criteria in Section 3.
2. Set `annotation_notes` to explain the uncertainty concisely.
3. Flag the row for adjudication by a second reviewer: set `adjudicated = 0`
   with a note; the adjudicator sets `adjudicated = 1` after completing their
   review and records the outcome in `adjudicated_label` and `adjudicated_by`.

Do not skip a row or leave `privacy_risk_label` blank.

---

## 9. Human-Review Queue — Eight Anomalous Samples

The following eight rows from `CAPRLM_annotation_50.csv` were flagged by
automated data audit. Their counts and IDs are **verified directly from the
annotation CSV** as of 2026-10-15. **Their existing labels must not be changed
by this document.** Each requires a human reviewer to examine the original
screenshot and either confirm the label or escalate for adjudication.

### 9.1 One HIGH row with all OCR entity flags = 0

| screenshot_id | Current label | Entity flags | application_context | Issue |
|---|---|---|---|---|
| `02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg` | HIGH | All 0 | (empty) | Highest-risk label, no OCR evidence, no context. Cannot be verified without visual review. |

**Review action required:**
- Examine the original screenshot.
- If confirming HIGH: record the visual reason in `annotation_notes`.
- If changing the label: document the reason and flag as adjudicated.

This row constitutes 50% of the HIGH class in the dataset. An incorrect label
has a material effect on model evaluation. The current evaluation cannot
assess HIGH-class performance regardless (0 HIGH rows in the test fold —
README Limitation 2).

---

### 9.2 Six MEDIUM rows with all OCR entity flags = 0

| screenshot_id | Current label | Entity flags | application_context |
|---|---|---|---|
| `00fa95dd-5a9e-458b-a071-fc2f8913e888.jpg` | MEDIUM | All 0 | (empty) |
| `01583a1d-7ccf-49d9-99c8-7ebf3b94cf01.jpg` | MEDIUM | All 0 | (empty) |
| `01a758f4-84ee-400c-a583-b7b70573c076.jpg` | MEDIUM | All 0 | (empty) |
| `024fd28f-aeb8-42dc-8484-9a8e2cd3ecf5.jpg` | MEDIUM | All 0 | (empty) |
| `033abfbe-b7f6-4c58-a769-8ffe082b38e4.jpg` | MEDIUM | All 0 | (empty) |
| `034e7035-d661-417e-8cab-6e3131d7a671.jpg` | MEDIUM | All 0 | (empty) |

**Review action required:**
- Examine each original screenshot.
- If confirming MEDIUM: record the visual reason in `annotation_notes` for
  each row.
- If changing to LOW: document the reason and flag as adjudicated.

These six rows are the confirmed cause of MEDIUM F1 = 0.000 in the current
evaluation. They share a feature vector (all-zero flags, Unknown context)
with the majority-class LOW rows, making them unseparable by the current
feature set regardless of model choice (README Limitation 3). Resolving
whether the MEDIUM labels are correct is the single highest-priority action
before any annotation expansion or model retraining.

---

### 9.3 One URL=1 row labelled LOW

| screenshot_id | Current label | URL | DATE | sensitive_entity_present | application_context | Issue |
|---|---|---|---|---|---|---|
| `01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg` | LOW | 1 | 1 | 1 | Google Chrome + YouTube | URL detected; rule baseline predicts MEDIUM; annotator assigned LOW. |

**Context:** This row has `URL = 1`, `DATE = 1`, `sensitive_entity_present = 1`,
and `application_context = 'Google Chrome + YouTube'` (a browser context that
is in the rule baseline's elevated-risk set). The rule baseline
(`rule_baseline.py`) would predict MEDIUM for this row via both the `URL >= 1`
rule and the browser-context-with-entity rule. The existing annotation is LOW.

**This is not automatically an error.** An annotator may have judged that the
detected URL was a system or navigation link with no meaningful privacy
exposure. However, the label directly contradicts the annotation criterion in
Section 3.2 as currently drafted.

**Review action required:**
- Examine the original screenshot.
- If the LOW label was deliberate (the URL posed no identifiable privacy risk
  in context): confirm LOW and record the rationale in `annotation_notes`.
  The research team should then decide whether the URL criterion in Section 3.2
  needs a qualifier (e.g. "exclude system/navigation URLs").
- If LOW was an annotation error: correct to MEDIUM and flag as adjudicated.

⚠️ **REQUIRES APPROVAL (Policy 8 — new):** Whether the URL criterion in
Section 3.2 should include a carve-out for system or navigation URLs, or
whether URL detection should always trigger MEDIUM regardless of context.
This policy decision cannot be resolved without human review of this
screenshot.

---

## 10. Annotation Expansion — Recommended Target

### 10.1 Current state (verified from annotation CSV)

| Class | Count | Share |
|---|---|---|
| LOW | 38 | 76% |
| MEDIUM | 10 | 20% |
| HIGH | 2 | 4% |
| CRITICAL | 0 | n/a (not a current label) |
| **Total** | **50** | |

### 10.2 Research-design recommendation

The following targets are a **research-design recommendation based on
practical minimum class sizes for exploratory evaluation**. They are
**not statistically guaranteed sample requirements**. Statistical guarantees
for classifier performance require power analysis based on the expected effect
size and variance — which cannot be computed without a prior study. With small
datasets, confidence intervals on F1 are wide regardless of the sample count;
the targets below are chosen to move the evaluation from the current regime
(where a single misclassification changes macro F1 by approximately 0.044 —
confirmed from README.md) toward a regime where per-class metrics are at
least minimally interpretable.

**Minimum practical target — for any meaningful per-class evaluation:**

A 20% held-out test split gives: test rows ≈ total × 0.20. To achieve
approximately 8–10 test rows per class, the total per class should be
**40–50**.

| Class | Current | Recommended minimum | Expected test rows at 20% |
|---|---|---|---|
| LOW | 38 | 80–100 | 16–20 |
| MEDIUM | 10 | **40–50** | 8–10 |
| HIGH | 2 | **40–50** | 8–10 |
| CRITICAL | 0 | 20–25 (if adopted — see Policy 3) | 4–5 |

This implies adding approximately **110–160 new annotated rows**, bringing
the total to ~160–210.

**Preferred target — for reliable stratified k-fold cross-validation (k=5):**

k-fold CV with k=5 requires at least k examples in the smallest class
(one per fold). For reliable per-fold metrics, 5–10 examples per fold per
class is the practical minimum, implying 25–50 total per class.

| Class | Preferred target | Per-fold examples at k=5 |
|---|---|---|
| LOW | 120–150 | 24–30 |
| MEDIUM | 60–80 | 12–16 |
| HIGH | 40–50 | 8–10 |
| CRITICAL | 20–30 (if adopted) | 4–6 |

Total: ~240–310 annotated rows.

⚠️ **REQUIRES APPROVAL (Policy 7):** The actual expansion target is a
research decision that must be agreed with the project supervisor before
annotation work begins. The numbers above are a starting point, not a mandate.

### 10.3 Sample selection strategy

When selecting rows from `candidates.csv` for annotation expansion:

1. **Prioritise underrepresented entities.** The existing 50-row dataset has
   zero positive examples for PHONE, IP_ADDRESS, and WINDOWS_PATH, and only
   one for EMAIL. Use the entity flag columns in `candidates.csv` to find
   rows where these flags = 1 and prioritise them for annotation.

2. **Stratify by `auto_label_candidate`.** Draw proportionally more rows
   predicted MEDIUM or HIGH by the rule baseline than LOW rows, to reduce
   class imbalance in the expanded dataset. `auto_label_candidate` is a
   pipeline output, not ground truth — every selected row requires
   independent human verification before the label is accepted.

3. **Include application-context diversity.** Use `application_context` in
   `candidates.csv` to ensure the expanded dataset covers the range of
   applications present in the screenshot collection.

4. **Exclude existing annotations.** Before selecting, filter out all
   screenshot IDs already present in `CAPRLM_annotation_50.csv`.

5. **Human verification is mandatory for every row.** No row may enter the
   annotation CSV using the pipeline's `auto_label_candidate` value as its
   final label. Every row must be reviewed by a human annotator following
   these guidelines.

---

## 11. Policy Decisions Requiring Supervisor or Research-Team Approval

| # | Policy question | Proposed default in this draft | Impact if changed |
|---|---|---|---|
| 1 | Can HIGH be assigned when all OCR entity flags are zero? | Yes, with mandatory `annotation_notes` | Affects 1 existing row (`02f39c8f`) and all future HIGH/no-entity cases; model cannot separate these from LOW/no-entity rows without additional features |
| 2 | DATE detected alone (`DATE=1`, all other flags=0) → LOW or MEDIUM? | LOW (preserves 9 existing DATE-only LOW labels) | Changing to MEDIUM would reclassify 9 existing rows |
| 3 | Add CRITICAL as a fourth annotation label? | No — use HIGH for email+phone under current schema | Adding it requires annotation CSV schema change, retrospective label review, and model/evaluation script updates |
| 4 | Backfill `application_context` for the 36 empty rows — modify existing CSV or create versioned copy? | Create versioned copy; do not modify protected original | Modifying the original risks the protected file; a versioned copy requires updating all references |
| 5 | Add `human_corrected_flags` column for annotator flag overrides? | Deferred — not needed before expansion begins | Needed if annotators will document OCR-missed entities systematically |
| 6 | Add the 7 proposed schema extension columns (annotation_notes, ocr_detection_gap, annotator_id, etc.)? | Add to any new annotation file; do not add to existing 50-row CSV | Enables inter-annotator agreement analysis and systematic OCR-gap tracking |
| 7 | Expansion target (annotated rows)? | 160–210 minimum; 240–310 preferred | Determines annotation effort, timeline, and evaluation reliability |
| 8 | URL criterion — does any detected URL trigger MEDIUM, or should system/navigation URLs be excluded? | Unresolved pending review of row `01bf28e2` | Affects the MEDIUM rule definition and several future annotation decisions |

---

*Document prepared: 2026-10-15. Version 0.2.*  
*All fact counts verified directly from `CAPRLM_annotation_50.csv` on 2026-10-15.*  
*This is a proposal only. No annotation data, model files, pipeline outputs, or*  
*application code were modified in its preparation.*
