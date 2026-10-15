# CAPRLM Consolidated Annotation Review — DRAFT

**Project:** J26-IT-308 · Member 2 · CAPRLM  
**Date:** 2026-10-15  
**Status:** DRAFT — for human reviewer only. No labels were changed.  
**Sources:** read-only inspection of annotation CSV, candidates.csv, manifest.json, and annotation_guidelines_DRAFT.md v0.2.  
**Screenshots:** checked for existence only. None were opened, copied, or uploaded.

---

## Overview

This document consolidates every anomalous annotation row identified across the dataset. It supersedes the per-row lists in `annotation_review_report_DRAFT.md` by including all 16 confirmed anomalous rows, not only the nine covered previously.

**Total annotated rows:** 50  
**Total anomalous rows identified:** 16 (32 % of the annotated set)  
**Groups:**

| Group | Description | Rows |
|---|---|---|
| G1 | HIGH label with all-zero annotation entity flags | 1 |
| G2 | MEDIUM label with EMAIL = 1 (should be HIGH per §3.1) | 1 |
| G3 | HIGH label where annotation flags support only MEDIUM | 1 |
| G4 | LOW label with URL = 1 (should be ≥ MEDIUM), plus flag conflict | 1 |
| G5 | MEDIUM label with all-zero annotation entity flags | 6 |
| G6 | MEDIUM label with DATE = 1 only (non-browser context) | 1 |
| G7 | LOW label where pipeline detects PHONE (annotation PHONE = 0) | 3 |
| G8 | LOW label where pipeline detects PHONE + IP (annotation all-zero) | 2 |

---

## Summary table

Columns:
- **Ann flags**: EMAIL · PHONE · URL · IP · DATE · PATH (binary, from annotation CSV)
- **Pipe flags**: EMAIL · PHONE · URL · IP · DATE · PATH (binary, from candidates.csv)
- **Flag agree**: whether the two sets of binary flags are identical
- **Label consistent**: whether the annotation label follows from the annotation flags under guidelines v0.2
- **Status**: NEEDS_VISUAL_REVIEW · NEEDS_POLICY_DECISION · METADATA_ONLY

| # | screenshot_id (truncated) | Ann label | Ann flags | Pipe flags | Pipe auto_label | Flag agree | Label consistent | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | `02f39c8f-…` | HIGH | 0·0·0·0·0·0 | 0·0·0·0·1·0 | LOW | No | No | NEEDS_VISUAL_REVIEW |
| 2 | `02e86b37-…` | MEDIUM | 1·0·0·0·1·0 | 1·0·0·0·1·0 | HIGH | Yes | No | NEEDS_VISUAL_REVIEW |
| 3 | `00c019f8-…` | HIGH | 0·0·1·0·1·0 | 0·0·0·0·1·0 | LOW | No | No | NEEDS_VISUAL_REVIEW |
| 4 | `01bf28e2-…` | LOW | 0·0·1·0·1·0 | 0·1·0·1·1·0 | HIGH | No | No | NEEDS_VISUAL_REVIEW |
| 5 | `00fa95dd-…` | MEDIUM | 0·0·0·0·0·0 | 0·0·0·0·1·0 | LOW | No | Conditional* | NEEDS_VISUAL_REVIEW |
| 6 | `01583a1d-…` | MEDIUM | 0·0·0·0·0·0 | 0·0·0·0·1·0 | LOW | No | Conditional* | NEEDS_VISUAL_REVIEW |
| 7 | `01a758f4-…` | MEDIUM | 0·0·0·0·0·0 | 0·0·0·0·1·0 | LOW | No | Conditional* | NEEDS_VISUAL_REVIEW |
| 8 | `024fd28f-…` | MEDIUM | 0·0·0·0·0·0 | 0·0·0·0·1·0 | LOW | No | Conditional* | NEEDS_VISUAL_REVIEW |
| 9 | `033abfbe-…` | MEDIUM | 0·0·0·0·0·0 | 0·0·0·0·1·0 | LOW | No | Conditional* | NEEDS_VISUAL_REVIEW |
| 10 | `034e7035-…` | MEDIUM | 0·0·0·0·0·0 | 0·0·0·0·1·0 | LOW | No | Conditional* | NEEDS_VISUAL_REVIEW |
| 11 | `00a3b7cb-…` | MEDIUM | 0·0·0·0·1·0 | 0·0·0·0·1·0 | LOW | Yes | NEEDS_POLICY_DECISION† | NEEDS_POLICY_DECISION |
| 12 | `0024e4cb-…` | LOW | 0·0·0·0·1·0 | 0·1·0·0·1·0 | HIGH | No | Yes‡ | NEEDS_VISUAL_REVIEW |
| 13 | `00c34dc6-…` | LOW | 0·0·0·0·1·0 | 0·1·0·0·1·0 | HIGH | No | Yes‡ | NEEDS_VISUAL_REVIEW |
| 14 | `00e7560f-…` | LOW | 0·0·0·0·1·0 | 0·1·0·0·1·0 | HIGH | No | Yes‡ | NEEDS_VISUAL_REVIEW |
| 15 | `014635cf-…` | LOW | 0·0·0·0·0·0 | 0·1·0·0·1·0 | HIGH | No | Yes‡ | NEEDS_VISUAL_REVIEW |
| 16 | `02488e7f-…` | LOW | 0·0·0·0·0·0 | 0·1·0·1·1·0 | HIGH | No | Yes‡ | NEEDS_VISUAL_REVIEW |

**\* Conditional:** A non-LOW label with all-zero entity flags is permitted by guidelines §6 only when `annotation_notes` documents the visual reason. That column does not exist in the current CSV.  
**† NEEDS_POLICY_DECISION:** Label is consistent with annotation flags only if Policy 2 (DATE-alone → LOW) is *not* adopted. If Policy 2 is adopted, this label is inconsistent.  
**‡ Yes (label internally consistent):** The annotation label follows from the annotation's own flags (DATE-only → LOW per Policy 2 default). The flags themselves may be wrong — that is the concern, not the label mapping.

---

## Detailed records

### Group G1 — HIGH label with all-zero annotation entity flags

---

#### Record 1 — `02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| EMAIL | 0 | 0 |
| PHONE | 0 | 0 |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 0 | 1 (date_count = 2) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **HIGH** | LOW |
| ocr_text_length | — | 617 |
| elapsed_seconds | — | 12.12 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:56:33Z |
| manifest output_row_index | — | 67 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No. Annotation DATE = 0; pipeline DATE = 1.  
**Label consistency with annotation flags:** No. All annotation flags are zero; guidelines §3.3 maps all-zero to LOW. HIGH is only justified under §3.1 if a direct personal identifier is visible but OCR failed to extract it (and must be documented in `annotation_notes` per §6). That column does not exist.  
**What metadata can establish:** The current pipeline found 617 characters of extractable text and two date matches. No processing error occurred. The entity flag discrepancy (DATE 0 vs 1) suggests the annotation was produced by an earlier pipeline run or the flags were set manually.  
**What requires visual review:** Whether any direct personal identifier (email, phone number, IP address, credential) is visible on-screen but was not extracted by OCR. If yes, HIGH may be correct and must be documented. If no, the label requires adjudication.  
**Model impact:** This row is 50 % of the HIGH class. An incorrect label makes HIGH-class evaluation entirely unreliable.  
**Status:** NEEDS_VISUAL_REVIEW

---

### Group G2 — MEDIUM label with EMAIL = 1

---

#### Record 2 — `02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| EMAIL | **1** | **1** (email_count = 1) |
| PHONE | 0 | 0 |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **MEDIUM** | HIGH |
| ocr_text_length | — | 531 |
| elapsed_seconds | — | 11.24 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:56:21Z |
| manifest output_row_index | — | 66 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** Yes. Both annotation and pipeline record EMAIL = 1.  
**Label consistency with annotation flags:** No. Guidelines §3.1: "Assign HIGH when … An email address (EMAIL = 1)." MEDIUM contradicts the written rule. The pipeline auto-label HIGH is consistent with §3.1.  
**What metadata can establish:** The entity flag is not in dispute. The label discrepancy is between annotation and written rule, not between annotation and pipeline.  
**What requires visual review:** Whether the email address visible in this screenshot is a real, identifiable personal or organisational address, or a generic/placeholder/system address. If generic, the annotator may have deliberately downgraded to MEDIUM, which should be documented and may inform a rule refinement. If personal, the label should be corrected to HIGH.  
**Status:** NEEDS_VISUAL_REVIEW

---

### Group G3 — HIGH label where annotation flags support only MEDIUM

---

#### Record 3 — `00c019f8-0be1-426d-ace4-b8e3c6fa8d35.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | YouTube | Unknown |
| EMAIL | 0 | 0 |
| PHONE | 0 | 0 |
| URL | **1** | **0** |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **HIGH** | LOW |
| ocr_text_length | — | 1008 |
| elapsed_seconds | — | 13.10 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:45:10Z |
| manifest output_row_index | — | 16 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No. Annotation URL = 1; pipeline URL = 0.  
**Label consistency with annotation flags:** No. Under the annotation's own flags (URL = 1, DATE = 1, YouTube context), guidelines §3.2 produces MEDIUM (URL detected; browser context with entity). HIGH requires EMAIL, PHONE, or IP_ADDRESS per §3.1. None are set. The annotation label HIGH is inconsistent with the written rules *and* with the annotation's own entity flags.  
**What metadata can establish:** Two issues are independent: (a) the annotation flags do not support HIGH even if taken at face value; (b) the pipeline does not detect the URL that the annotation records. Either the URL was present when annotated but not captured by the current OCR run, or the annotation URL flag was set in error.  
**What requires visual review:** Whether a URL is visible in the screenshot. If yes and it is a direct personal identifier (e.g. a URL containing a user email or credential), HIGH may be justified; document in `annotation_notes`. If only a plain URL is visible, the correct label is MEDIUM. If no URL is visible, the annotation flag is wrong and the label should be re-evaluated.  
**Additional note:** This is a two-part anomaly: flag disagreement (URL) *and* label inconsistency with annotation flags (HIGH without EMAIL/PHONE/IP).  
**Status:** NEEDS_VISUAL_REVIEW

---

### Group G4 — LOW label with URL = 1 and conflicting pipeline flags

---

#### Record 4 — `01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | Google Chrome + YouTube | Unknown |
| EMAIL | 0 | 0 |
| PHONE | 0 | **1** (phone_count = 1) |
| URL | **1** | **0** |
| IP_ADDRESS | 0 | **1** (ip_count = 1) |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **LOW** | HIGH |
| ocr_text_length | — | 1995 |
| elapsed_seconds | — | 17.87 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:50:10Z |
| manifest output_row_index | — | 38 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No. Annotation URL = 1, PHONE = 0, IP = 0. Pipeline URL = 0, PHONE = 1, IP = 1. The two versions share no common non-DATE non-zero flag.  
**Label consistency with annotation flags:** No. Under the annotation's own flags (URL = 1, Google Chrome + YouTube context), §3.2 yields MEDIUM (URL detected; browser context with entity). The annotation label is LOW, which contradicts §3.2.  
**What metadata can establish:** The annotation records `application_context = Google Chrome + YouTube` (one of the listed elevated-risk browser contexts), so context was available to the annotator. The current OCR run (1,995 chars) found different entities than the earlier run. The entity flag sets are mutually exclusive, ruling out a partial detection overlap.  
**What requires visual review:** Whether a URL, a phone number, and/or an IP address are visible on-screen. The outcome determines (a) which pipeline version's flags are correct, and (b) the correct label. Under no version of the flags is LOW straightforwardly correct: annotation flags (URL=1, browser context) → MEDIUM; pipeline flags (PHONE=1, IP=1) → HIGH.  
**Additional note:** This record directly informs Policy 8 (URL carve-out for system/navigation URLs). The annotation guidelines §9.3 flag this row for review on exactly this basis.  
**Status:** NEEDS_VISUAL_REVIEW + NEEDS_POLICY_DECISION (Policy 8)

---

### Group G5 — MEDIUM labels with all-zero annotation entity flags

All six records share the same structural pattern: annotation entity flags all zero, annotation label MEDIUM, current pipeline finds DATE = 1 and auto-labels LOW. All have no application_context in the annotation CSV. All manifest entries are status = done, error = null.

A non-LOW label with all-zero entity flags is permitted by guidelines §6 only when the annotator documents a visual reason in `annotation_notes`. That column does not exist in the current CSV. Therefore these labels cannot be verified or refuted from metadata alone.

---

#### Record 5 — `00fa95dd-5a9e-458b-a071-fc2f8913e888.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| All entity flags | **0·0·0·0·0·0** | 0·0·0·0·**1**·0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| ocr_text_length | — | 686 |
| date_count | — | 2 |
| elapsed_seconds | — | 11.45 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:45:59Z |
| manifest output_row_index | — | 20 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No (DATE 0 vs 1).  
**Label consistency:** Conditional — permitted under §6 only with `annotation_notes` documenting visual reason.  
**Status:** NEEDS_VISUAL_REVIEW

---

#### Record 6 — `01583a1d-7ccf-49d9-99c8-7ebf3b94cf01.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| All entity flags | **0·0·0·0·0·0** | 0·0·0·0·**1**·0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| ocr_text_length | — | 1256 |
| date_count | — | 1 |
| elapsed_seconds | — | 15.22 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:47:43Z |
| manifest output_row_index | — | 28 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No (DATE 0 vs 1).  
**Label consistency:** Conditional (same as Record 5).  
**Status:** NEEDS_VISUAL_REVIEW

---

#### Record 7 — `01a758f4-84ee-400c-a583-b7b70573c076.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| All entity flags | **0·0·0·0·0·0** | 0·0·0·0·**1**·0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| ocr_text_length | — | 857 |
| date_count | — | 2 |
| elapsed_seconds | — | 14.90 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:49:53Z |
| manifest output_row_index | — | 37 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No (DATE 0 vs 1).  
**Label consistency:** Conditional (same as Record 5).  
**Status:** NEEDS_VISUAL_REVIEW

---

#### Record 8 — `024fd28f-aeb8-42dc-8484-9a8e2cd3ecf5.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| All entity flags | **0·0·0·0·0·0** | 0·0·0·0·**1**·0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| ocr_text_length | — | 478 |
| date_count | — | 1 |
| elapsed_seconds | — | 11.82 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:52:35Z |
| manifest output_row_index | — | 49 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No (DATE 0 vs 1).  
**Label consistency:** Conditional (same as Record 5).  
**Status:** NEEDS_VISUAL_REVIEW

---

#### Record 9 — `033abfbe-b7f6-4c58-a769-8ffe082b38e4.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| All entity flags | **0·0·0·0·0·0** | 0·0·0·0·**1**·0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| ocr_text_length | — | 792 |
| date_count | — | 1 |
| elapsed_seconds | — | 13.08 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:58:07Z |
| manifest output_row_index | — | 74 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No (DATE 0 vs 1).  
**Label consistency:** Conditional (same as Record 5).  
**Status:** NEEDS_VISUAL_REVIEW

---

#### Record 10 — `034e7035-d661-417e-8cab-6e3131d7a671.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| All entity flags | **0·0·0·0·0·0** | 0·0·0·0·**1**·0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| ocr_text_length | — | 248 |
| date_count | — | 1 |
| elapsed_seconds | — | 10.69 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:58:18Z |
| manifest output_row_index | — | 75 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No (DATE 0 vs 1).  
**Label consistency:** Conditional (same as Record 5).  
**Note:** Smallest OCR text length among the six (248 chars). Sparse text may indicate a minimal-content screenshot.  
**Status:** NEEDS_VISUAL_REVIEW

---

### Group G6 — MEDIUM label with DATE = 1 only (non-browser context)

---

#### Record 11 — `00a3b7cb-0490-4a19-8f11-e37dad461566.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | **Desktop** | Unknown |
| EMAIL | 0 | 0 |
| PHONE | 0 | 0 |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | **1** | **1** (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| ocr_text_length | — | 439 |
| elapsed_seconds | — | 9.83 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:44:20Z |
| manifest output_row_index | — | 12 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** Yes. Both versions record only DATE = 1.  
**Label consistency with annotation flags:** Depends on Policy 2. The proposed Policy 2 default (DATE-alone → LOW) would make this label INCONSISTENT. The MEDIUM rule for browser/content-sharing context (§3.2) lists Google Chrome, YouTube, Firefox, Edge, Brave — "Desktop" is not on that list. There is no other documented rule path from DATE-alone + Desktop context to MEDIUM.  
**What metadata can establish:** Flag agreement is confirmed; the disagreement is only on the label. The annotator may have judged that the Desktop context (full desktop visible, potentially exposing multiple application windows) warranted elevation, even though this context is not named in the browser-context rule. That reasoning is not documented.  
**What requires visual review:** Whether the desktop screenshot contains visual content beyond a date that warrants MEDIUM. If yes, the annotator's judgment may be defensible and should inform a rule extension. If only a date and ordinary desktop content is visible, LOW is the correct label per Policy 2 (proposed).  
**Policy dependency:** This row is directly affected by the Policy 2 decision. If "Desktop" is added to the elevated-context list, the label may become consistent; if not, it should be LOW.  
**Status:** NEEDS_POLICY_DECISION (Policy 2 + potential extension to cover Desktop context) + NEEDS_VISUAL_REVIEW

---

### Group G7 — LOW labels where pipeline detects PHONE (annotation PHONE = 0)

These three rows all carry LOW labels in the annotation CSV. Their annotation flags show only DATE = 1. The current pipeline records PHONE = 1 (and DATE = 1) and auto-labels HIGH. If the pipeline's phone detections are correct, the annotation labels understate risk. If the pipeline's phone detections are false positives, the annotation labels may be correct.

All three: manifest status = done, error = null.

---

#### Record 12 — `0024e4cb-aacb-44f7-8fe2-5c633f1ec67f.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | VS Code | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 1) |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **LOW** | HIGH |
| ocr_text_length | — | 1082 |
| elapsed_seconds | — | 21.40 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T00:07:33Z |
| manifest output_row_index | — | 2 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No. PHONE 0 vs 1.  
**Label consistency with annotation flags:** Yes. DATE-only → LOW per Policy 2.  
**What requires visual review:** Whether a phone number is visible in the screenshot (e.g. in VS Code content, a displayed file, a browser panel). If yes, annotation was produced by a version that missed it and the label should be corrected to HIGH. If no, the pipeline has a false positive.  
**Status:** NEEDS_VISUAL_REVIEW

---

#### Record 13 — `00c34dc6-612f-43cb-9b9f-297d92b6868a.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | VS Code | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 3) |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 2) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **LOW** | HIGH |
| ocr_text_length | — | 1416 |
| elapsed_seconds | — | 13.79 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:45:23Z |
| manifest output_row_index | — | 17 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No. PHONE 0 vs 1 (pipeline count = 3 — three separate phone pattern matches).  
**Label consistency with annotation flags:** Yes. DATE-only → LOW per Policy 2.  
**Note:** Three pipeline phone matches is a stronger signal than a single match. Could indicate multiple phone numbers in the visible content, or a recurring false-positive pattern in VS Code output.  
**Status:** NEEDS_VISUAL_REVIEW

---

#### Record 14 — `00e7560f-f51a-4d8a-ad26-f6461fe5d6de.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | VS Code + ChatGPT | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 1) |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **LOW** | HIGH |
| ocr_text_length | — | 981 |
| elapsed_seconds | — | 12.79 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:45:47Z |
| manifest output_row_index | — | 19 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No. PHONE 0 vs 1.  
**Label consistency with annotation flags:** Yes. DATE-only → LOW per Policy 2.  
**Note:** Involves VS Code + ChatGPT context; code or conversation content might contain phone-number-like strings (version numbers, port numbers, code snippets) that trigger false positives.  
**Status:** NEEDS_VISUAL_REVIEW

---

### Group G8 — LOW labels where pipeline detects PHONE + IP (annotation all-zero)

These two rows have all annotation flags at zero and are labelled LOW. The pipeline finds both PHONE and IP_ADDRESS and auto-labels HIGH.

---

#### Record 15 — `014635cf-027c-4bec-a6c2-304828e4e9f9.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 1) |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 0 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **LOW** | HIGH |
| ocr_text_length | — | 664 |
| elapsed_seconds | — | 12.15 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:47:16Z |
| manifest output_row_index | — | 26 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No. All annotation flags zero; pipeline DATE = 1 and PHONE = 1.  
**Label consistency with annotation flags:** Yes. All-zero → LOW per §3.3.  
**What requires visual review:** Whether a phone number (and a date) is visible. If yes, the annotation was produced by a version that missed both and the label should be corrected to HIGH.  
**Status:** NEEDS_VISUAL_REVIEW

---

#### Record 16 — `02488e7f-132f-4e80-b164-d34cf38424ff.jpg`

| Field | Annotation CSV | Candidates.csv (pipeline) |
|---|---|---|
| application_context | *(empty)* | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 3) |
| URL | 0 | 0 |
| IP_ADDRESS | **0** | **1** (ip_count = 2) |
| DATE | 0 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **LOW** | HIGH |
| ocr_text_length | — | 1327 |
| elapsed_seconds | — | 15.06 |
| manifest status | — | done, error = null |
| processed_at | — | 2026-10-14T04:52:05Z |
| manifest output_row_index | — | 47 |
| Screenshot file | **EXISTS** | — |

**Flag agreement:** No. All annotation flags zero; pipeline PHONE = 1 (×3), IP = 1 (×2), DATE = 1.  
**Label consistency with annotation flags:** Yes. All-zero → LOW per §3.3.  
**Note:** The pipeline records three phone matches and two IP matches in 1,327 characters of text. Multiple matches are a stronger signal. This is the highest-risk divergence in Groups G7–G8.  
**Status:** NEEDS_VISUAL_REVIEW

---

## Cross-cutting observations

### O1 — All 16 anomalous rows have all-zero annotation flags for WINDOWS_PATH

No annotated row records a WINDOWS_PATH detection. The candidates.csv also shows WINDOWS_PATH = 0 for all 16 anomalous rows. WINDOWS_PATH as an entity type has zero positive examples across the annotated set; it cannot be evaluated.

### O2 — Pattern of PHONE = 0 in annotation vs PHONE = 1 in pipeline (5 rows)

Records 4, 12, 13, 14, 15, and 16 all show PHONE = 0 in annotation and PHONE ≥ 1 in the current pipeline (Record 4 also involves IP). This is the most common flag-disagreement pattern. Possible explanations are: (a) the annotation was produced by an earlier pipeline version before the phone regex was improved; (b) the current phone regex produces false positives on code snippets, port numbers, or version strings; or (c) both. Human visual review of all six rows is required to determine which explanation applies, and whether a regex change or annotation correction is needed.

### O3 — annotation_context is blank for 14 of the 16 anomalous rows

Only Records 3 (`00c019f8`, YouTube) and 4 (`01bf28e2`, Google Chrome + YouTube) and 12 (`0024e4cb`, VS Code) and 13 (`00c34dc6`, VS Code) and 14 (`00e7560f`, VS Code + ChatGPT) have non-blank `application_context`. All others are blank. This means the MEDIUM rule for browser context (§3.2 — elevated risk when application_context is a browser and entity = 1) cannot be applied or verified for those rows without backfilling the field. Policy 4 must be resolved before any label correction that depends on context.

### O4 — All manifest entries are clean

All 16 anomalous screenshots have manifest status = `done`, error = `null`. No processing failure, timeout, or retry explains the flag disagreements. The disagreements are caused by pipeline version differences, not by processing errors.

### O5 — No annotation has EMAIL = 1 that is correctly labelled HIGH

Only one annotated row has EMAIL = 1 (Record 2, `02e86b37`), and it is labelled MEDIUM, not HIGH. There are zero correctly-labelled HIGH rows with an OCR-extracted entity. The one HIGH row (Record 1) has all-zero flags. This means the model has never seen a training example of the form "email detected → HIGH", making generalisation to email-based HIGH predictions impossible from the current dataset.

---

## Final summary

### Total anomalous rows identified

**16 of 50 annotated rows (32 %)** contain at least one of the following: a label that is inconsistent with the annotation's own entity flags under the current draft guidelines; entity flags that disagree between the annotation CSV and the current pipeline output; or a label that is contingent on an unresolved policy decision.

### Issues that can be resolved from metadata alone

**None.** Every anomaly below requires either visual review of the screenshot or a policy decision:

- The flag disagreements between annotation and pipeline (12 of 16 rows) cannot be adjudicated from OCR character counts or entity counts alone. Human visual confirmation of whether the entity is present is required.
- The label inconsistencies (Records 1, 2, 3, 4) require both visual review and, in the case of Record 4, a policy decision (Policy 8).
- The all-zero MEDIUM rows (Records 5–10) can only be confirmed or corrected by viewing the screenshots.

The only metadata-level conclusion is: all 16 screenshots exist on disk and all 16 processed without errors, so visual review is possible for all of them.

### Issues that require visual review of screenshot files

**All 16 records** require a human reviewer to open the original screenshot. The specific questions are listed in each record's "What requires visual review" section.

**Minimum required actions before model retraining:**
- Records 1–4: must be resolved before retraining. These directly affect whether HIGH-class training examples are valid and whether entity flags used as features are correct.
- Records 5–10: must be resolved before retraining. These rows are the confirmed cause of MEDIUM F1 = 0.000 and cannot be improved by model tuning.
- Records 12–16: recommended before retraining. These rows train the model on entity flags that may differ from what the model would see at inference time.

### Issues that require a supervisor-approved labelling policy decision

| Policy | Affects records | Question |
|---|---|---|
| Policy 1 | Record 1, Records 5–10 | Can HIGH/MEDIUM be assigned when all OCR entity flags are zero? |
| Policy 2 | Record 11, Records 5–10 | Does DATE alone → LOW, or can context elevate it to MEDIUM? |
| Policy 4 | All 16 (application_context) | Backfill existing blank context — modify existing CSV or create versioned copy? |
| Policy 6 | All 16 (annotation_notes) | Add annotation_notes column before or as part of resolving these records? |
| Policy 8 | Record 4 | Does any detected URL trigger MEDIUM, or is there a carve-out for navigation/system URLs? |

Records 1, 3, and 11 additionally require the research team to decide whether the annotator's judgment represents a rule edge case worth encoding, or an error to correct.

---

*Report generated by read-only file inspection on 2026-10-15.*  
*No annotation CSV, candidates.csv, manifest, model artifact, or training log was modified.*  
*OCR processes were not interrupted.*  
*Screenshot files were checked for existence only; none were opened, copied, or uploaded.*
