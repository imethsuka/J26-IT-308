# CAPRLM Annotation Review Report
**Generated:** 2026-10-15  
**Status:** DRAFT — AI Provisional Assessments Only  
**Human Review Required:** All 16 records  
**Reviewer:** [BLANK — awaiting human assignment]

---

> **DISCLAIMER**: All visual assessments in this report are AI-provisional observations produced by Claude Sonnet 4.6 for annotation planning purposes. They do NOT constitute human annotation decisions. No labels have been changed. Ground-truth labels may only be updated by an authorised human reviewer following annotation_guidelines_DRAFT.md policies.

---

## 1. Summary

| Metric | Value |
|--------|-------|
| Total annotated records | 50 |
| Records flagged for review | 16 (32%) |
| Priority screenshots visually inspected (AI) | 6 of 16 |
| Remaining screenshots inspected | 0 of 10 |
| Human reviewer decisions recorded | 0 / 16 |
| Open policy decisions required | 8 (policies 1–8, annotation_guidelines_DRAFT.md v0.2) |
| Screenshot path correction needed | YES — audit script searched wrong directory |

### Screenshot Path Correction
The audit script (`annotation_audit.py`) searched for screenshots in the wrong directory, causing all 16 records to show `screenshot_file_exists: false`.

- **Incorrect path used:** `C:\Users\ASUS\AppData\Roaming\DesktopResearchCollector\screenshots\`  
- **Correct path confirmed:** `C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\`

Priority screenshots confirmed present at correct path (6/6 verified).

---

## 2. Anomaly Groups

| Group | Issue | Count | Records |
|-------|-------|-------|---------|
| G1 | HIGH label with all-zero annotation entity flags | 1 | `02f39c8f` |
| G2 | MEDIUM label with EMAIL=1 (should be HIGH per §3.1) | 1 | `02e86b37` |
| G3 | HIGH label where flags support only MEDIUM (URL=1) | 1 | `00c019f8` |
| G4 | LOW label with URL=1 + pipeline detects PHONE+IP | 1 | `01bf28e2` |
| G5 | MEDIUM label with all-zero annotation entity flags | 6 | `00fa95dd` + 5 others |
| G6 | Pipeline agrees, label consistent | 1 | (no action) |
| G7 | PHONE flags in annotation not confirmed by pipeline | 3 | `0024e4cb`, `00c34dc6`, `00e7560f` |
| G8 | Mixed: PHONE+IP annotation vs pipeline disagreement | 2 | `014635cf`, `02488e7f` |

---

## 3. Priority Visual Inspection Records (AI Provisional)

### Record: `02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg` — Group G2
**Annotation:** MEDIUM, EMAIL=1, DATE=1  
**Pipeline:** EMAIL=1, DATE=1, auto_label=HIGH  
**Issue:** Label inconsistency — EMAIL=1 should produce HIGH per §3.1

**AI Provisional Observation:**  
Screenshot shows SLIIT CourseWeb university login portal (`courseweb.sliit.lk`). The page contains an example email address displayed in the "Is this your first time here?" help section. The username field shows a student ID. Password field is masked. No phone number or IP address visually confirmed.

**Key question for human reviewer:** Is the visible email address an example/template (reducing sensitivity) or an actual user credential? If example-only, the MEDIUM label may be defensible under a "contextual sensitivity" policy. If treated as any visible email = HIGH, the label needs upgrading.  
**Status:** NEEDS_HUMAN_DECISION (Policy 1 — email sensitivity threshold)

---

### Record: `0024e4cb-aacb-44f7-8fe2-5c633f1ec67f.jpg` — Group G7
**Annotation flags:** [unknown PHONE flags based on group assignment]  
**Issue:** PHONE disagreement between annotation and pipeline

**AI Provisional Observation:**  
Screenshot shows IntelliJ IDEA (left) with incomplete Java Queue implementation alongside ChatGPT Classic (right) showing "DSA IT1170 lab test" conversation. Content is a student asking for exam help in Sinhala. The ChatGPT response provides Java code examples. No phone number, IP address, or email address visually confirmed in the visible content.

**Key question for human reviewer:** Is a phone number present anywhere in the OCR-extracted text not visible in this viewport (e.g., page footer, hidden notification)? Or is the PHONE flag a false positive from a numeric sequence?  
**Status:** NEEDS_HUMAN_DECISION — likely false positive but requires human confirmation

---

### Record: `00c34dc6-612f-43cb-9b9f-297d92b6868a.jpg` — Group G7
**Annotation flags:** [PHONE×3 disagreement]  
**Issue:** Multiple PHONE flags in annotation not confirmed by pipeline

**AI Provisional Observation:**  
Screenshot shows VS Code with AWS CloudFormation YAML file (`IT23252936 IaC template.yaml`). Content shows CloudFormation Parameters section — student ID, database username, database password (`NoEcho: true`, no actual value shown), and AMI ID parameter. No phone number, email address, or real IP address visually confirmed in the visible content. The file path contains a student ID number.

**Key question for human reviewer:** Are the numeric sequences in this YAML (e.g., student ID `IT23252936`) being misidentified as phone numbers? The OCR text may contain digit sequences the pipeline parsed as PHONE entities.  
**Status:** NEEDS_HUMAN_DECISION — suspected false positive from digit sequences in CloudFormation YAML

---

### Record: `00e7560f-f51a-4d8a-ad26-f6461fe5d6de.jpg` — Group G7
**Annotation flags:** [PHONE disagreement]  
**Issue:** PHONE annotation vs pipeline disagreement

**AI Provisional Observation:**  
Screenshot shows IntelliJ IDEA with same Java Queue project and ChatGPT "DSA IT1170 lab test" — appears to be a slightly earlier or later moment in the same session as `0024e4cb`. Autocomplete popup visible for `Queue(size)`. No phone number, email, or IP address visually confirmed.

**Key question for human reviewer:** Same as `0024e4cb` — phone flags likely from OCR artifacts or numeric literals in code.  
**Status:** NEEDS_HUMAN_DECISION — likely false positive, same context as `0024e4cb`

---

### Record: `014635cf-027c-4bec-a6c2-304828e4e9f9.jpg` — Group G8
**Annotation flags:** [PHONE+IP disagreement]  
**Issue:** PHONE+IP in annotation not confirmed by pipeline (or vice versa)

**AI Provisional Observation:**  
Screenshot shows ChatGPT Classic (full window). Sidebar shows personal names: "Tania" (logged-in user avatar), "Imeth" (pinned chat). Multiple medical chat threads pinned: "Medicine", "Pediatrics", "Psychiatry". Active chat appears to be about "Yasmin packet" (a prescription contraceptive) with Sinhala-language conversation about medication. No phone number or IP address visually confirmed in the main chat content visible.

**Sensitive content note (AI):** This screenshot contains health-related queries and personal names in the chat sidebar. However, no structured PII entities (PHONE, IP) are visually apparent. The sensitivity may be contextual (health query content) rather than entity-based.  
**Status:** NEEDS_HUMAN_DECISION — no PHONE/IP visually confirmed; label may need re-evaluation based on contextual sensitivity policy

---

### Record: `02488e7f-132f-4e80-b164-d34cf38424ff.jpg` — Group G8
**Annotation flags:** [PHONE×3+IP×2 disagreement]  
**Issue:** Multiple PHONE and IP flags — pipeline and annotation disagree

**AI Provisional Observation:**  
Screenshot shows VS Code with the **same** AWS CloudFormation YAML file as `00c34dc6` but at a later section (lines 156–187) showing Security Group resource definitions. Content includes:
- `CidrIp: 0.0.0.0/0` appearing twice (open ingress rules for HTTP port 80 and SSH port 22)
- Port numbers: 80, 22
- Security group tags using `${studentid}` substitution

**The `0.0.0.0/0` CIDR blocks are the likely source of IP_ADDRESS flags.** These are infrastructure network permission rules (allow all traffic), not real identifiable IP addresses. Whether CIDR blocks should count as IP_ADDRESS entities is a policy question.  
**Status:** NEEDS_HUMAN_DECISION — IP flags are likely from CIDR notation `0.0.0.0/0`; requires policy decision on whether CIDR = IP_ADDRESS in annotation guidelines

---

## 4. Unreviewed Records (No AI Observation Available)

The following 10 records were not visually inspected in this session:

| Screenshot ID (prefix) | Group | Issue |
|------------------------|-------|-------|
| `02f39c8f` | G1 | HIGH label, all-zero entity flags |
| `00c019f8` | G3 | HIGH label, URL=1 only (MEDIUM expected) |
| `01bf28e2` | G4 | LOW label, URL=1 + PHONE+IP from pipeline |
| `00fa95dd` + 5 others | G5 | MEDIUM label, all-zero entity flags |
| (any G6 record) | G6 | Pipeline agrees — may not require priority review |

These require human visual inspection using the correct screenshot path:  
`C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots\`

---

## 5. Open Policy Decisions (Blocks Human Review)

All of the following require supervisor approval before labels can be finalised:

| Policy # | Description |
|----------|-------------|
| Policy 1 | Email in example/template context — HIGH or MEDIUM? |
| Policy 2 | URL in browser address bar only — sensitive entity? |
| Policy 3 | CIDR blocks (`0.0.0.0/0`) — counts as IP_ADDRESS? |
| Policy 4 | Numeric sequences in code/IDs — PHONE false positive threshold |
| Policy 5 | ChatGPT conversation content — contextual sensitivity vs entity-based |
| Policy 6 | Health/medical query content — label elevation required? |
| Policy 7 | Infrastructure-as-code files — WINDOWS_PATH/IP treatment |
| Policy 8 | Student ID numbers — entity type classification |

---

## 6. Recommended Human Review Workflow

1. Open the HTML review interface (see `annotation_review_interface.html`)
2. For each record: open screenshot at the correct path above
3. Confirm or correct the AI provisional observations
4. Apply approved policies to determine correct label
5. Record decision in the checklist (`human_annotation_review_checklist_DRAFT.md`)
6. Obtain supervisor sign-off on all policy decisions before updating CSV
7. After all 16 records reviewed: re-run `baseline_eval.py` with corrected dataset

---

## 7. Blocking Risks for Submission

| Risk | Severity | Mitigation |
|------|----------|------------|
| 0/16 human annotation decisions completed | HIGH | Start visual review immediately |
| 8 open policies not approved | HIGH | Schedule supervisor meeting |
| Screenshot path bug in audit script | MEDIUM | Fix script or update results manually |
| Model not retrained on corrected labels | MEDIUM | Blocked on annotation review completion |
| WINDOWS_PATH: 0 positive examples | LOW | Document as known limitation |

---

*Report generated by AI (Claude Sonnet 4.6) for CAPRLM project J26-IT-308.*  
*AI provisional assessments are for planning only and must not be used to update ground-truth labels.*
