# CAPRLM Human Annotation Review Checklist — DRAFT

**Project:** J26-IT-308 · Member 2 · CAPRLM  
**Prepared:** 2026-10-15  
**Status:** DRAFT — awaiting reviewer completion  
**Applies to:** `CAPRLM_annotation_50.csv` (50 rows; 16 flagged for review)

---

## Instructions for the reviewer

1. Work through each record in order. Do not skip records.
2. Open the screenshot file from:  
   `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\<screenshot_id>`
3. **Do not** rely on the pipeline `auto_label_candidate` as ground truth. It is a rule-based estimate, not verified ground truth.
4. **Do not** change the annotation CSV directly. Record your determination in the **Reviewer decision** field of this checklist only. A separate versioning step applies labels to the CSV after all reviews are complete.
5. For records marked **NEEDS_POLICY_DECISION**, do not assign a final label until the relevant policy (identified per record) has been approved by a supervisor. You may record a provisional determination and flag it.
6. Fill in every field: **Reviewer decision**, **Reviewer notes**, **Review status**, and **Reviewed by / date**.

### Review status values

| Value | Meaning |
|---|---|
| `CONFIRMED` | Reviewer has examined the screenshot and confirms the existing annotation label is correct. |
| `CORRECTED` | Reviewer has examined the screenshot and determined the existing label should be changed. New label recorded in Reviewer decision. |
| `ESCALATED` | Reviewer is uncertain; flagged for a second reviewer or supervisor. |
| `POLICY_BLOCKED` | Cannot determine final label until the referenced policy decision is approved. Provisional determination recorded if possible. |
| `DEFERRED` | Screenshot not available or review not yet possible. Record reason. |

### Required policy decisions (referenced per record)

| Policy | Question | Status |
|---|---|---|
| Policy 1 | Can HIGH or MEDIUM be assigned when all OCR entity flags are zero, provided visual evidence is documented in `annotation_notes`? | ⬜ Not yet approved |
| Policy 2 | Does DATE detected alone (DATE=1, all other flags=0) map to LOW regardless of application_context? | ⬜ Not yet approved |
| Policy 4 | Should blank `application_context` rows be backfilled in the existing CSV, or in a new versioned copy? | ⬜ Not yet approved |
| Policy 6 | Should the `annotation_notes` column be added to a new annotation file version before labels are corrected? | ⬜ Not yet approved |
| Policy 8 | Does any detected URL (URL=1) automatically trigger MEDIUM, or should system / navigation URLs be excluded? | ⬜ Not yet approved |

---

## Progress tracker

Fill in as each record is completed.

| # | screenshot_id (short) | Current label | Review status | Reviewed by | Date |
|---|---|---|---|---|---|
| 1 | `02f39c8f-…` | HIGH | | | |
| 2 | `02e86b37-…` | MEDIUM | | | |
| 3 | `00c019f8-…` | HIGH | | | |
| 4 | `01bf28e2-…` | LOW | | | |
| 5 | `00fa95dd-…` | MEDIUM | | | |
| 6 | `01583a1d-…` | MEDIUM | | | |
| 7 | `01a758f4-…` | MEDIUM | | | |
| 8 | `024fd28f-…` | MEDIUM | | | |
| 9 | `033abfbe-…` | MEDIUM | | | |
| 10 | `034e7035-…` | MEDIUM | | | |
| 11 | `00a3b7cb-…` | MEDIUM | | | |
| 12 | `0024e4cb-…` | LOW | | | |
| 13 | `00c34dc6-…` | LOW | | | |
| 14 | `00e7560f-…` | LOW | | | |
| 15 | `014635cf-…` | LOW | | | |
| 16 | `02488e7f-…` | LOW | | | |

---

## Record 1

**Group:** G1 — HIGH label with all-zero annotation entity flags  
**Requires:** Screenshot inspection · Policy 1

### Screenshot

`02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| EMAIL | 0 | 0 |
| PHONE | 0 | 0 |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | **0** | **1** (date_count = 2) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **HIGH** | LOW |
| OCR text length | — | 617 chars |
| Pipeline processing | — | done, no error, 12.12 s |

### Discrepancy

The annotation records all entity flags as zero and assigns HIGH. The current pipeline finds 617 characters of extractable text, detects two date matches, and auto-labels LOW. The annotation DATE flag (0) conflicts with the pipeline DATE flag (1), indicating the annotation was produced by a different pipeline version or the flags were set manually. HIGH is the annotation label that requires the most evidence; under guidelines §3.1, HIGH requires a direct personal identifier (email, phone, or IP). No such flag is set.

### Review question

Open the screenshot. Is a direct personal identifier (email address, phone number, or IP address) visible anywhere in the image, even if the current OCR pipeline did not extract it?

- If **yes**: HIGH may be correct. Record the visible content in Reviewer notes. This is an OCR detection gap (Policy 1 applies — if approved, document in `annotation_notes`).
- If **no**: HIGH is not supported by the written rules. Determine whether MEDIUM or LOW is appropriate based on what is visible.

### Model impact

This row is **50% of the HIGH class** in the dataset (2 annotated HIGH rows total). An incorrect HIGH label makes it impossible to train or evaluate the model on the HIGH class.

### Reviewer fields

**Reviewer decision** (CONFIRMED / CORRECTED → new label / ESCALATED / POLICY_BLOCKED / DEFERRED):

> 

**If corrected — new label:** `________`

**Visible content justifying label** (required if HIGH is confirmed with all-zero flags, per Policy 1):

> 

**Reviewer notes:**

> 

**Policy dependencies:** Policy 1 (HIGH with zero OCR flags), Policy 6 (annotation_notes column)

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 2

**Group:** G2 — MEDIUM label despite EMAIL = 1  
**Requires:** Screenshot inspection

### Screenshot

`02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| EMAIL | **1** | **1** (email_count = 1) |
| PHONE | 0 | 0 |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **MEDIUM** | HIGH |
| OCR text length | — | 531 chars |
| Pipeline processing | — | done, no error, 11.24 s |

### Discrepancy

Both the annotation and the current pipeline agree that EMAIL = 1. The entity flag is not in dispute. Guidelines §3.1 states: "Assign HIGH when the screenshot contains at least one direct personal identifier … An email address (EMAIL = 1)." The annotation label MEDIUM contradicts this rule. The pipeline auto-label HIGH is consistent with it.

### Review question

Open the screenshot. Examine the email address visible on-screen.

- Is it a **real personal or organisational email address** that could identify a specific individual (e.g. `firstname.lastname@company.com`, a student address, a personal Gmail)?  
  → If yes: the label should be HIGH per §3.1. Record the type of address (without transcribing the actual address) in Reviewer notes.
- Is it a **generic, system, placeholder, or clearly non-personal address** (e.g. `noreply@service.com`, `admin@`, a template variable, a test fixture)?  
  → If yes: the annotator may have deliberately chosen MEDIUM. Record the reasoning. The research team must then decide whether §3.1 needs a qualifier for non-personal email addresses.

Do not transcribe the actual email address into this checklist.

### Reviewer fields

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Type of email address observed** (personal / generic / system / unclear — do not transcribe the address):

> 

**Reviewer notes:**

> 

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 3

**Group:** G3 — HIGH label where annotation flags support only MEDIUM  
**Requires:** Screenshot inspection

### Screenshot

`00c019f8-0be1-426d-ace4-b8e3c6fa8d35.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\00c019f8-0be1-426d-ace4-b8e3c6fa8d35.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | **YouTube** | Unknown |
| EMAIL | 0 | 0 |
| PHONE | 0 | 0 |
| URL | **1** | **0** |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **HIGH** | LOW |
| OCR text length | — | 1,008 chars |
| Pipeline processing | — | done, no error, 13.10 s |

### Discrepancy

Two independent problems exist for this record:

1. **Flag conflict:** The annotation records URL = 1; the current pipeline records URL = 0. One version has detected a URL that the other did not.
2. **Label inconsistency with annotation's own flags:** Even accepting the annotation flags at face value (URL = 1, DATE = 1, YouTube context), guidelines §3.2 maps URL = 1 to MEDIUM and browser context + entity to MEDIUM. Guidelines §3.1 requires EMAIL, PHONE, or IP_ADDRESS for HIGH. Neither is set. HIGH is not supported by the annotation's own entity flags.

### Review question

Open the screenshot. There are two separate questions:

**Q3a — URL presence:** Is a URL visible in the screenshot (e.g. in the YouTube address bar, an embedded link, a chat message)?  
→ This determines which entity flag version is correct.

**Q3b — HIGH justification:** Is any direct personal identifier (email, phone, IP address) visible, whether or not OCR extracted it?  
→ If yes, HIGH may be correct and the relevant flag is missing from the annotation. Document the visible content.  
→ If no, the highest correct label given visible content is MEDIUM (if URL is confirmed) or LOW (if no qualifying entity is present).

### Reviewer fields

**Q3a answer — URL visible in screenshot?** (yes / no / unclear):

> 

**Q3b answer — Direct personal identifier visible?** (yes / no / unclear):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 4

**Group:** G4 — LOW label with URL=1 in annotation; pipeline detects PHONE + IP instead  
**Requires:** Screenshot inspection · Policy 8

### Screenshot

`01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | **Google Chrome + YouTube** | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 1) |
| URL | **1** | **0** |
| IP_ADDRESS | **0** | **1** (ip_count = 1) |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **LOW** | HIGH |
| OCR text length | — | 1,995 chars |
| Pipeline processing | — | done, no error, 17.87 s |

### Discrepancy

The annotation and the current pipeline record entirely different entities for this row. The two entity flag sets share no common non-DATE non-zero flag: the annotation detects URL; the pipeline detects PHONE and IP_ADDRESS. Under the annotation's own flags (URL = 1, browser context), guidelines §3.2 would yield MEDIUM — not LOW. Under the pipeline's flags (PHONE = 1, IP = 1), §3.1 would yield HIGH. LOW is not well-supported by either version of the flags.

### Review questions

**Q4a — Which entities are visible?** Open the screenshot and look for:
- A URL in the browser address bar or in page content
- A phone number (in any format)
- An IP address (four-part numeric format)

Record which are present and which are absent. Do not transcribe the actual values.

**Q4b — LOW label justification:** If a URL is present, is it a system or navigation link (e.g. a YouTube home page URL, a browser default) with no meaningful privacy exposure? This determines whether Policy 8 (URL carve-out) is relevant.

### Reviewer fields

**Q4a — Entities observed** (check all that apply):  
☐ URL visible  ☐ Phone number visible  ☐ IP address visible  ☐ None of the above  ☐ Unclear

**Q4b — If URL visible, does it represent a privacy risk?** (yes / no / unclear):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Policy dependencies:** Policy 8 (URL carve-out); Policy 4 (application_context backfill)

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Records 5–10

**Group:** G5 — MEDIUM labels with all-zero annotation entity flags  
**Requires:** Screenshot inspection · Policy 1

All six records share this pattern:
- Annotation entity flags: all zero, sensitive_entity_present = 0
- Annotation label: MEDIUM
- Application_context: blank in annotation CSV
- Pipeline: DATE = 1, auto_label = LOW
- All manifest entries: done, no error

These six rows are the **confirmed cause of MEDIUM F1 = 0.000** in the current model evaluation. Their feature vectors (all-zero flags, unknown context) are identical to the majority of LOW rows. No classifier can distinguish them using the current feature set.

Under guidelines §3.3, all-zero flags map to LOW. Assigning MEDIUM with all-zero flags is permitted under §6 only when the annotator documents visual evidence that OCR could not capture (e.g. a visible browser URL bar not extracted by OCR, visible employee names rendered as image text). That documentation field (`annotation_notes`) does not yet exist in the CSV.

### Review question (identical for all six)

Open the screenshot. Is there visual content that a reasonable person would consider elevated-risk privacy exposure, even though OCR did not detect any qualifying entity?

Examples that could justify MEDIUM:
- A browser open to a page that reveals browsing activity or a service subscription, even if the URL text was not extracted
- A visible form containing personal information that OCR failed to segment
- Application windows suggesting sensitive data handling

If no such content is visible and the screenshot contains only date-format text and generic interface elements, LOW is the correct label per Policy 2 (proposed).

---

## Record 5

### Screenshot

`00fa95dd-5a9e-458b-a071-fc2f8913e888.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\00fa95dd-5a9e-458b-a071-fc2f8913e888.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| All entity flags | 0 · 0 · 0 · 0 · **0** · 0 | 0 · 0 · 0 · 0 · **1** · 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| OCR text length | — | 686 chars (date_count = 2) |
| Pipeline processing | — | done, no error, 11.45 s |

### Reviewer fields

**Visual content justifying MEDIUM** (describe what is visible without inferring from metadata; required if MEDIUM is confirmed):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Policy dependencies:** Policy 1, Policy 2, Policy 6

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 6

### Screenshot

`01583a1d-7ccf-49d9-99c8-7ebf3b94cf01.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\01583a1d-7ccf-49d9-99c8-7ebf3b94cf01.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| All entity flags | 0 · 0 · 0 · 0 · **0** · 0 | 0 · 0 · 0 · 0 · **1** · 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| OCR text length | — | 1,256 chars (date_count = 1) |
| Pipeline processing | — | done, no error, 15.22 s |

### Reviewer fields

**Visual content justifying MEDIUM:**

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Policy dependencies:** Policy 1, Policy 2, Policy 6

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 7

### Screenshot

`01a758f4-84ee-400c-a583-b7b70573c076.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\01a758f4-84ee-400c-a583-b7b70573c076.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| All entity flags | 0 · 0 · 0 · 0 · **0** · 0 | 0 · 0 · 0 · 0 · **1** · 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| OCR text length | — | 857 chars (date_count = 2) |
| Pipeline processing | — | done, no error, 14.90 s |

### Reviewer fields

**Visual content justifying MEDIUM:**

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Policy dependencies:** Policy 1, Policy 2, Policy 6

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 8

### Screenshot

`024fd28f-aeb8-42dc-8484-9a8e2cd3ecf5.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\024fd28f-aeb8-42dc-8484-9a8e2cd3ecf5.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| All entity flags | 0 · 0 · 0 · 0 · **0** · 0 | 0 · 0 · 0 · 0 · **1** · 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| OCR text length | — | 478 chars (date_count = 1) |
| Pipeline processing | — | done, no error, 11.82 s |

### Reviewer fields

**Visual content justifying MEDIUM:**

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Policy dependencies:** Policy 1, Policy 2, Policy 6

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 9

### Screenshot

`033abfbe-b7f6-4c58-a769-8ffe082b38e4.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\033abfbe-b7f6-4c58-a769-8ffe082b38e4.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| All entity flags | 0 · 0 · 0 · 0 · **0** · 0 | 0 · 0 · 0 · 0 · **1** · 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| OCR text length | — | 792 chars (date_count = 1) |
| Pipeline processing | — | done, no error, 13.08 s |

### Reviewer fields

**Visual content justifying MEDIUM:**

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Policy dependencies:** Policy 1, Policy 2, Policy 6

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 10

### Screenshot

`034e7035-d661-417e-8cab-6e3131d7a671.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\034e7035-d661-417e-8cab-6e3131d7a671.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| All entity flags | 0 · 0 · 0 · 0 · **0** · 0 | 0 · 0 · 0 · 0 · **1** · 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **MEDIUM** | LOW |
| OCR text length | — | 248 chars (date_count = 1) |
| Pipeline processing | — | done, no error, 10.69 s |

**Note:** This record has the smallest OCR text length of the six (248 characters), suggesting relatively sparse visible text.

### Reviewer fields

**Visual content justifying MEDIUM:**

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Policy dependencies:** Policy 1, Policy 2, Policy 6

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 11

**Group:** G6 — MEDIUM label with DATE = 1 only, non-browser application context  
**Requires:** Screenshot inspection · Policy 2 + potential policy extension

### Screenshot

`00a3b7cb-0490-4a19-8f11-e37dad461566.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\00a3b7cb-0490-4a19-8f11-e37dad461566.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
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
| OCR text length | — | 439 chars |
| Pipeline processing | — | done, no error, 9.83 s |

### Discrepancy

Both versions agree on all entity flags (DATE = 1, all others 0). The disagreement is only on the label. The proposed Policy 2 default maps DATE-alone to LOW. The annotation assigns MEDIUM. The guidelines §3.2 MEDIUM rule for browser/content-sharing context lists Google Chrome, YouTube, Firefox, Edge, and Brave — "Desktop" is not on that list. There is no documented rule path from DATE-alone + Desktop context to MEDIUM in the current draft guidelines.

### Review questions

**Q11a — Application context:** What is the dominant application or context visible in the screenshot? Is the full desktop visible with multiple application windows, or is a single application foregrounded?

**Q11b — MEDIUM justification:** Is there visual content beyond a date that an annotator would reasonably consider elevated risk — for example, multiple sensitive application windows visible simultaneously, personal data visible in a taskbar or notification, or a visible file manager showing personal folder names?

**Q11c — Policy implication:** If MEDIUM is confirmed, this may indicate that the elevated-context rule in §3.2 should be extended to include "Desktop" or "full desktop" context. Record your view on whether the rule should be extended.

### Reviewer fields

**Q11a — Application context observed:**

> 

**Q11b — Visual content justifying MEDIUM:**

> 

**Q11c — Should "Desktop" be added to the elevated-context list in §3.2?** (yes / no / defer to research team):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Policy dependencies:** Policy 2 (DATE-alone rule), possible extension to §3.2 elevated-context list

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Records 12–14

**Group:** G7 — LOW labels where pipeline detects PHONE = 1 (annotation PHONE = 0)  
**Requires:** Screenshot inspection

All three records share this pattern:
- Annotation: PHONE = 0, DATE = 1, label = LOW
- Pipeline: PHONE = 1, DATE = 1, auto_label = HIGH
- Application_context in annotation: a VS Code context (Records 12, 13) or VS Code + ChatGPT (Record 14)

The annotation labels are internally consistent with the annotation's own flags (DATE-only → LOW per Policy 2). The concern is whether the annotation flags are correct — specifically whether PHONE = 0 is right. The pipeline's phone regex may be detecting phone-number-like strings in code, chat output, or version numbers, which would be false positives. Alternatively, the annotation may have been produced by an earlier pipeline version that missed genuine phone numbers.

### Review question (identical for all three)

Open the screenshot. Is a phone number visible anywhere in the image — in code, a displayed file, a chat conversation, a web page, or any other element?

- If **yes**: record the format/context (not the number itself) and determine whether it is a genuine personal phone number or a code/technical artefact (e.g. a port number, version string, numeric identifier).
- If **no**: the pipeline PHONE detection is likely a false positive; LOW is correct.

---

## Record 12

### Screenshot

`0024e4cb-aacb-44f7-8fe2-5c633f1ec67f.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\0024e4cb-aacb-44f7-8fe2-5c633f1ec67f.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | **VS Code** | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 1) |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **LOW** | HIGH |
| OCR text length | — | 1,082 chars |
| Pipeline processing | — | done, no error, 21.40 s |

### Reviewer fields

**Phone number visible in screenshot?** (yes / no / unclear):

> 

**If yes — context** (genuine personal number / code or technical artefact / unclear):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 13

### Screenshot

`00c34dc6-612f-43cb-9b9f-297d92b6868a.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\00c34dc6-612f-43cb-9b9f-297d92b6868a.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | **VS Code** | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = **3**) |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 2) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **LOW** | HIGH |
| OCR text length | — | 1,416 chars |
| Pipeline processing | — | done, no error, 13.79 s |

**Note:** The pipeline records phone_count = 3, meaning three separate phone-pattern matches in the text. This is the highest phone_count of the three G7 records.

### Reviewer fields

**Phone number(s) visible in screenshot?** (yes / no / unclear):

> 

**Count observed** (if yes — approximate number of distinct phone-format strings):

> 

**If yes — context** (genuine personal number(s) / code or technical artefact / mixed / unclear):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 14

### Screenshot

`00e7560f-f51a-4d8a-ad26-f6461fe5d6de.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\00e7560f-f51a-4d8a-ad26-f6461fe5d6de.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | **VS Code + ChatGPT** | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 1) |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 1 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 1 | 1 |
| label / auto_label | **LOW** | HIGH |
| OCR text length | — | 981 chars |
| Pipeline processing | — | done, no error, 12.79 s |

**Note:** VS Code + ChatGPT context. Code and conversational output may contain phone-number-like strings (port numbers, numeric identifiers, example data in LLM responses) that trigger false positives.

### Reviewer fields

**Phone number visible in screenshot?** (yes / no / unclear):

> 

**If yes — source context** (VS Code content / ChatGPT conversation / other / unclear):

> 

**If yes — context** (genuine personal number / code or technical artefact / unclear):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Records 15–16

**Group:** G8 — LOW labels where pipeline detects PHONE + IP (annotation all-zero)  
**Requires:** Screenshot inspection

Both records have all annotation entity flags at zero and are labelled LOW. The pipeline detects PHONE and IP_ADDRESS in addition to DATE and auto-labels HIGH. If the pipeline detections are correct, these records carry a significant risk that the annotation misses entirely.

### Review question (identical for both)

Open the screenshot. Look for:
- A phone number in any format
- An IP address in four-part numeric format (e.g. 192.168.x.x, 10.x.x.x)

Record whether each is present or absent. Do not transcribe the actual values.

---

## Record 15

### Screenshot

`014635cf-027c-4bec-a6c2-304828e4e9f9.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\014635cf-027c-4bec-a6c2-304828e4e9f9.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = 1) |
| URL | 0 | 0 |
| IP_ADDRESS | 0 | 0 |
| DATE | 0 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **LOW** | HIGH |
| OCR text length | — | 664 chars |
| Pipeline processing | — | done, no error, 12.15 s |

### Reviewer fields

**Phone number visible?** (yes / no / unclear):

> 

**IP address visible?** (yes / no / unclear):

> 

**If yes to either — context** (genuine personal data / technical artefact / unclear):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## Record 16

### Screenshot

`02488e7f-132f-4e80-b164-d34cf38424ff.jpg`  
Path: `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\02488e7f-132f-4e80-b164-d34cf38424ff.jpg`  
File present on disk: **YES**

### Data

| Field | Annotation CSV | Pipeline candidates.csv |
|---|---|---|
| application_context | *(blank)* | Unknown |
| EMAIL | 0 | 0 |
| PHONE | **0** | **1** (phone_count = **3**) |
| URL | 0 | 0 |
| IP_ADDRESS | **0** | **1** (ip_count = **2**) |
| DATE | 0 | 1 (date_count = 1) |
| WINDOWS_PATH | 0 | 0 |
| sensitive_entity_present | 0 | 1 |
| label / auto_label | **LOW** | HIGH |
| OCR text length | — | 1,327 chars |
| Pipeline processing | — | done, no error, 15.06 s |

**Note:** The highest detection count of any G8 record: three phone matches and two IP matches in 1,327 characters. This is also the highest-count PHONE + IP divergence of any of the 16 anomalous records.

### Reviewer fields

**Phone number(s) visible?** (yes / no / unclear):

> 

**Count observed** (if yes):

> 

**IP address(es) visible?** (yes / no / unclear):

> 

**Count observed** (if yes):

> 

**If yes to either — context** (genuine personal data / technical artefact / mixed / unclear):

> 

**Reviewer decision:**

> 

**If corrected — new label:** `________`

**Reviewer notes:**

> 

**Review status:** `________`  
**Reviewed by:** `________`  
**Date:** `________`

---

## After completing all reviews

### Step 1 — Tally outcomes

Fill in after all 16 reviews are complete.

| Outcome | Count |
|---|---|
| CONFIRMED (label unchanged) | |
| CORRECTED (label changed) | |
| ESCALATED (second review needed) | |
| POLICY_BLOCKED (awaiting policy decision) | |
| DEFERRED | |
| **Total reviewed** | /16 |

### Step 2 — Resolve policy dependencies

Before applying any corrections to the CSV, the following policy questions must be resolved by a supervisor. Mark each as approved or rejected before proceeding.

| Policy | Decision needed | Approved by | Date | Outcome |
|---|---|---|---|---|
| Policy 1 | HIGH/MEDIUM with all-zero OCR flags | | | ☐ Approved ☐ Rejected |
| Policy 2 | DATE-alone → LOW regardless of context | | | ☐ Approved ☐ Rejected |
| Policy 4 | Backfill application_context: modify existing CSV or create versioned copy | | | ☐ Approved ☐ Rejected |
| Policy 6 | Add annotation_notes column before applying corrections | | | ☐ Approved ☐ Rejected |
| Policy 8 | URL criterion: any URL = MEDIUM, or carve-out for navigation URLs | | | ☐ Approved ☐ Rejected |

### Step 3 — Apply corrections

Apply label corrections only after Step 2 is complete. Update only the file and version agreed under Policy 4. Do not modify `CAPRLM_annotation_50.csv` directly unless Policy 4 approves in-place editing.

### Step 4 — Regenerate entity flags (if needed)

If Reviews 12–16 confirm that the pipeline phone/IP detections are genuine (not false positives), the annotation entity flags for those rows should be regenerated by re-running the current pipeline version rather than being filled in manually.

---

## What cannot be determined without visual review

The following cannot be concluded from CSV and manifest metadata alone:

- Whether any visual element in Records 1, 3, 5–10 justifies a label that the OCR pipeline could not detect
- Whether phone-format strings in Records 12–14 are genuine phone numbers or code/technical artefacts
- Whether PHONE + IP detections in Records 15–16 correspond to real data or regex false positives
- Whether the email address in Record 2 is personal or generic
- Whether the LOW label in Record 4 reflects a deliberate annotator judgment about a non-privacy URL

## What can be determined without visual review (metadata only)

- Records 2, 12, 13, 14: the annotation label is **internally consistent** with the annotation's own entity flags. The concern is whether the flags themselves are correct, not whether the label follows from the flags.
- Records 5–10: all manifest entries are clean (no OCR errors). The DATE flag discrepancy between annotation (0) and pipeline (1) is confirmed from both files.
- Records 1, 3, 4, 15, 16: flag values differ between annotation and pipeline on at least one entity. Both versions processed without errors. The source of the discrepancy is pipeline version change, not a processing failure.
- Record 11: both annotation and pipeline agree on all entity flags. The label discrepancy exists independently of any OCR issue; it depends entirely on how the labelling rules apply to "Desktop" context.

---

*Checklist prepared by read-only file inspection on 2026-10-15.*  
*No annotation CSV, candidates.csv, manifest, model artifact, training log, or prior report was modified.*  
*OCR processes were not interrupted.*  
*Screenshot files were checked for existence only during checklist preparation.*
