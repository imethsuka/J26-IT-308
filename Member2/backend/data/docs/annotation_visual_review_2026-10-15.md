# CAPRLM Annotation Visual Review — 2026-10-15

**Project:** J26-IT-308 · Member 2 · CAPRLM  
**Date:** 2026-10-15  
**Status:** DRAFT — provisional visual assessments. No labels were changed. All findings require human reviewer confirmation and supervisor sign-off before any annotation correction.  
**Method:** Direct image inspection of all 16 flagged screenshots using the Read tool (image rendering). Each screenshot was opened and inspected independently.  
**Scope:** 16 anomalous rows identified in `consolidated_annotation_review_DRAFT.md`.

---

## Summary of visual findings

| # | screenshot_id (prefix) | Ann label | Visual suggests | OCR missed? | Pipeline PHONE/IP verdict | Action required |
|---|---|---|---|---|---|---|
| 1 | `02f39c8f` | HIGH | LOW | No | n/a | Correct → LOW (supervisor) |
| 2 | `02e86b37` | MEDIUM | HIGH | No | n/a | Policy decision: template email |
| 3 | `00c019f8` | HIGH | MEDIUM | No (URL visible) | n/a | Correct → MEDIUM (supervisor) |
| 4 | `01bf28e2` | LOW | MEDIUM–HIGH | Yes (student ID, CIDR) | Plausible for CIDR | Correct → MEDIUM or HIGH |
| 5 | `00fa95dd` | MEDIUM | **HIGH** | **YES — email missed** | n/a | **Correct flags and label → HIGH** |
| 6 | `01583a1d` | MEDIUM | LOW–MEDIUM | Marginal | n/a | Policy decision: context-only sensitivity |
| 7 | `01a758f4` | MEDIUM | LOW | No | n/a | Correct → LOW (supervisor) |
| 8 | `024fd28f` | MEDIUM | **HIGH** | **YES — name/ID missed** | n/a | **Correct flags and label → HIGH** |
| 9 | `033abfbe` | MEDIUM | LOW–MEDIUM | Marginal | n/a | Policy decision: context-only sensitivity |
| 10 | `034e7035` | MEDIUM | MEDIUM | Partial (Gmail tab) | n/a | Confirm: Gmail address in tab bar |
| 11 | `00a3b7cb` | MEDIUM | LOW–MEDIUM | No | n/a | Policy decision: Desktop context |
| 12 | `0024e4cb` | LOW | LOW | No | FALSE POSITIVE (likely) | Confirm: no phone visible |
| 13 | `00c34dc6` | LOW | MEDIUM–HIGH | **YES — ID/path** | FALSE POSITIVE on ID digits | Correct flags → HIGH (path + ID) |
| 14 | `00e7560f` | LOW | LOW | No | FALSE POSITIVE (likely) | Confirm: no phone visible |
| 15 | `014635cf` | LOW | **SENSITIVE** | Yes (health content) | FALSE POSITIVE (likely) | Policy gap: sensitive content w/o entities |
| 16 | `02488e7f` | LOW | MEDIUM–HIGH | **YES — ID/path** | FALSE POSITIVE on CIDR/ID | Correct flags → HIGH (path + ID) |

---

## Detailed visual assessments

---

### Record 1 — `02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg`
**Group G1 | Annotated HIGH, all-zero flags | Pipeline: DATE=1, auto LOW**

**What is visible:**
- YouTube video player showing "Aaley (ෂාලේ) | Episode 236 | 01st October 2026 | Sirasa TV" — a Sri Lankan TV drama
- URL bar: `youtube.com/watch?v=80JPF4vnhtA`
- YouTube search bar text: "accress subliminal" (personal search query)
- Browser tab bar: "DSA MID Paper Discu...", **"Mail - WIJESINGHA W..."**, "Dashboard | CourseW...", (active YouTube tab), "mail.google.com", "DSA Past Paper Midte..."
- Video duration visible; date "01st October 2026" in video title

**PII assessment:**
- No email address, phone number, IP address, or credentials visible in the main frame
- The browser tab "Mail - WIJESINGHA W..." indicates a mail tab is open in a different tab — its contents are not visible in this screenshot
- The search query "accress subliminal" is a personal behavioural signal but not a directly identifying credential
- DATE confirmed: "01st October 2026" is visible (pipeline DATE=1 is correct; annotation DATE=0 appears to be an error)

**OCR miss check:**
- No identifiable PII visible that OCR could have missed. The mail tab title is partial and cannot confirm any identifying content without seeing the mail tab itself.

**Suggested risk label:** LOW  
**Confidence:** High (no PII visible in frame)  
**Uncertainty:** The "Mail" tab context. If the WIJESINGHA name in the tab title was captured by OCR, that could be a name detection — but names alone are not in the entity flag set.  
**Human confirmation required:** Yes — a human reviewer should confirm no identifying content is visible off-screen (e.g. notifications, hover tooltips). The annotation HIGH cannot be supported from what is visible.

---

### Record 2 — `02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg`
**Group G2 | Annotated MEDIUM, EMAIL=1 | Pipeline: EMAIL=1, auto HIGH**

**What is visible:**
- SLIIT CourseWeb login page at `courseweb.sliit.lk/login/index.php?loginredirect=1`
- Session timeout message: "Your session has timed out. Please log in again."
- **Username field contains: `I23252936`** — the user's student ID number actively typed in the field
- Password field: masked dots
- Page instructions show example: **Username: `IT21000090@my.sliit.lk`**, Password: [O365 email password]
- The email `IT21000090@my.sliit.lk` is displayed as a generic example for new student users

**PII assessment:**
- EMAIL=1 (pipeline and annotation agree): the detected email is `IT21000090@my.sliit.lk`
- **Critical determination:** `IT21000090@my.sliit.lk` is a generic template example shown on the public login page to all users — it is NOT personally associated with this user
- The **student ID `I23252936`** in the username field IS a real personal identifier, but it is not in the email/phone/IP/path regex set
- No personal email is visible — the one email detected is a public example

**OCR miss check:**
- No personal email missed by OCR. The template email was correctly detected. No personally identifying email was visible to miss.

**Suggested risk label:** This is a policy decision.
- If policy treats any email (including generic template examples) as triggering HIGH → label should be HIGH
- If policy carves out non-personally-identifying examples → MEDIUM is defensible, but the student ID in the username field creates a secondary concern not captured by any current flag

**Confidence:** Medium  
**Uncertainty:** Policy on template vs personal emails. The student ID visible in the username field is not captured by any flag.  
**Human confirmation required:** Yes — supervisor must decide Policy: "Does a public/template email shown as a UI example trigger HIGH, or should only personal emails trigger HIGH?"

---

### Record 3 — `00c019f8-0be1-426d-ace4-b8e3c6fa8d35.jpg`
**Group G3 | Annotated HIGH, URL=1, DATE=1 | Pipeline: URL=0, DATE=1, auto LOW**

**What is visible:**
- YouTube video player: "DSA MID Paper Discussion (1Y 2S)" by Deshan Rashmika
- URL bar: **`youtube.com/watch?v=DRwr5JTeJ6o&t=1819s`** — a YouTube video URL
- Video content: handwritten DSA tree diagram lecture notes (educational)
- Drawing annotation toolbar overlay visible
- Video timestamp: 53:02 / 1:43:27
- Browser tab: "Mail - WIJESINGHA W L S A it2..." (mail tab open, not visible)
- System clock: 10:45 AM 10/1/2026

**PII assessment:**
- **URL IS visible** in the browser address bar — annotation URL=1 is correct; pipeline URL=0 is a miss (pipeline OCR did not extract the address bar text)
- The URL is `youtube.com/watch?v=DRwr5JTeJ6o` — a YouTube navigation URL, not a personal identifier URL (no user email, credential, or private path in the URL)
- Per guidelines §3.2, URL=1 with browser context → MEDIUM. HIGH requires EMAIL=1, PHONE=1, or IP_ADDRESS=1 per §3.1 — none of these are present

**OCR miss check:**
- Pipeline missed the URL in the address bar (OCR did not extract address bar text). Annotation URL=1 is more accurate.

**Suggested risk label:** MEDIUM (URL detected + browser context per §3.2)  
**Confidence:** High (URL clearly visible; no email/phone/IP present)  
**Uncertainty:** Policy 8 — does a YouTube navigation URL trigger MEDIUM, or should only personal/credential URLs trigger it?  
**Human confirmation required:** Yes — requires Policy 8 decision. The visual confirms URL=1 is correct. HIGH annotation is not supported.

---

### Record 4 — `01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg`
**Group G4 | Annotated LOW, URL=1 | Pipeline: PHONE=1, IP=1, auto HIGH**

**What is visible:**
- YouTube video playing in foreground: "IT4090 Cloud Computing Assignment Walkthrough" by Sachithra Wijesinghe
- **YouTube search dropdown is open** with personal search history visible:
  - "everything success subliminal", "ale", "prema dadayama 6 review", "prema dadayama 6", "bolt anayak teledrama", "attraction binural sinhala", "exam subliminal", "ds lab test sliit", "universe in you", "chicken sandwich", "divine beauty subliminal", "attraction subliminal", "premaye munivariya"
- **Behind the search overlay**: AWS VPC console visible with form showing **`IT23152700`** in the "Name tag auto-generation" field
- AWS form: IPv4 CIDR `10.0.0.0/16` — a private network CIDR block
- Browser tabs: "IT4090 - Clou...", "Mail - WUJES...", "Sandbox Env...", "Workbench - ...", "Stack IT23252...", "Inbox (601) - ...", "proposal M2..."
- URL: `youtube.com/watch?v=cnzPreaqK1Q&t=3210s`

**PII assessment:**
- **Student ID `IT23152700`** visible in AWS form — real personal identifier (course assignment)
- **Personal YouTube search history** exposed — significant behavioural privacy exposure (personal interests, music, study habits)
- **CIDR `10.0.0.0/16`**: network specification, not a real IP address. Pipeline IP=1 is likely triggered by this — FALSE POSITIVE on CIDR block
- Annotation URL=1: the YouTube URL IS visible, confirming the annotation flag
- Pipeline PHONE=1: no phone number visible. The pipeline phone detection may be triggered by digit sequences in the page (student IDs, video IDs)
- The annotation label LOW is inconsistent with annotation flags (URL=1, browser context → §3.2 MEDIUM)

**OCR miss check:**
- Student ID in AWS form was not captured in annotation (WINDOWS_PATH=0, and there's no student_id flag). OCR of the form field may have captured this.
- Personal search history was likely captured in OCR (1,995 chars) but not mapped to any entity type.

**Suggested risk label:** At minimum MEDIUM (URL=1, browser context). Arguably HIGH given exposed personal identity (student ID in form + personal search history).  
**Confidence:** High (LOW is clearly wrong)  
**Uncertainty:** Whether the search history alone constitutes HIGH-level risk under the current guidelines.  
**Human confirmation required:** Yes — verify whether `IT23152700` is the annotator's student ID or a different user. Determine appropriate entity flag for student IDs in form fields.

---

### Record 5 — `00fa95dd-5a9e-458b-a071-fc2f8913e888.jpg`
**Group G5 | Annotated MEDIUM, all-zero flags | Pipeline: DATE=1, auto LOW**

**What is visible:**
- Microsoft Word "Save As" dialog for file "IE1030 ASSIGNMENT.docx"
- OneDrive accounts panel showing:
  - **OneDrive - Sri Lanka Ins...** with account: **`it23252936@my.sliit.lk`** — REAL institutional email address (not a template)
  - **OneDrive - Personal** with account: **`sashiniwijesingha83@gmail.c...`** — REAL personal Gmail address (truncated)
- File browser path: `C: > Users > ASUS > AppData > Local > Packages > 5319275A.WhatsAppDesktop_cv1g1gvanyjgm > LocalState > sessions > 2B65006ED...` — full Windows path exposing WhatsApp Desktop session directory
- File: "IE1030 ASSIGNMENT.docx" dated 10/3/2026 9:31 PM
- System clock: 10:17 PM 10/3/2026

**PII assessment:**
- **EMAIL: TWO REAL email addresses visible** — `it23252936@my.sliit.lk` (institutional) and `sashiniwijesingha83@gmail.c...` (personal Gmail)
- **WINDOWS_PATH: Real Windows path visible** including WhatsApp Desktop session data path
- The annotation EMAIL=0 is **WRONG** — two emails are plainly visible in the OneDrive account panel
- This screenshot contains more PII than almost any other in the flagged set
- Pipeline OCR text_length = 686 chars, DATE detected — OCR may not have extracted the OneDrive sidebar content. This is an OCR miss.

**OCR miss check:**
- YES — OCR failed to extract the email addresses from the OneDrive account names in the sidebar. The pipeline DATE=1 detected only the date in the file listing. The emails and path were not captured.
- This is a significant OCR gap: UI sidebar text (OneDrive account labels) was not extracted.

**Suggested risk label:** HIGH (EMAIL=1 confirmed visual; two real emails present)  
**Confidence:** Very high — two real email addresses are unmistakably visible  
**Uncertainty:** None on the label. The only question is whether to record EMAIL=1 for the institutional email, Gmail, or both.  
**Human confirmation required:** Yes — a human reviewer must confirm and correct the annotation. This is a clear annotation error.

**ACTION:** Annotation EMAIL=0 is incorrect. Correct to EMAIL=1, WINDOWS_PATH=1, label=HIGH. Requires supervisor sign-off.

---

### Record 6 — `01583a1d-7ccf-49d9-99c8-7ebf3b94cf01.jpg`
**Group G5 | Annotated MEDIUM, all-zero flags | Pipeline: DATE=1, auto LOW**

**What is visible:**
- SLIIT CourseWeb course page (IT4010 Research Project) in background
- **ChatGPT overlay** open with conversation "Supervisors" pinned
- ChatGPT conversation shows research questions about CAPRLM:
  - Question (Sinhala): Why does 'John' from a research document → LOW risk? Why from banking screenshot → HIGH risk?
  - Answer: ChatGPT explanation visible
- ChatGPT sidebar shows personal conversation list: "Imeth", "IME COFFE SHOP", "Service Platform Idea", "AI & Tech Daily Briefing", "Auto Font Detect...", "Tania"
- ChatGPT pinned: "Research Proposal Help", "Branch - GCS Bucket Access Setup", "GCS Bucket Access Setup", "Create Claude Project Sources", "Review Member 1 Part"
- Input field: "resech document eke john kiyana eka api" (Sinhala)
- Browser tab: "Course: IT4010 - Research Proj...", two YouTube tabs
- System clock: 2:33 PM 10/5/2026

**PII assessment:**
- No email, phone number, IP address, or credentials visible
- Personal ChatGPT conversation names visible: "Imeth", "Tania" — personal contact names, not formal identifiers
- Personal project/business names: "IME COFFE SHOP", "Service Platform Idea" — business ideas
- Research project conversations visible — research IP but not personal PII
- The annotation MEDIUM (all-zero flags) cannot be verified or refuted from the entity flag rules alone

**OCR miss check:**
- No identifiable regex-detectable PII was present to miss. The MEDIUM label reflects visual judgment of sensitive content (personal AI conversation history, research IP).

**Suggested risk label:** Policy-dependent. Per current guidelines (no detected entities) → LOW. By visual judgment of context → MEDIUM defensible (personal conversation metadata visible).  
**Confidence:** Medium  
**Uncertainty:** Whether personal contact names and business project names in chat history constitute elevated risk without direct entity detection.  
**Human confirmation required:** Yes — Policy 1 and Policy 6 decisions required.

---

### Record 7 — `01a758f4-84ee-400c-a583-b7b70573c076.jpg`
**Group G5 | Annotated MEDIUM, all-zero flags | Pipeline: DATE=1, auto LOW**

**What is visible:**
- YouTube video: "Aaley (ෂාලේ) | Episode 236 | 01st October 2026 | Sirasa TV" — same video as Record 1 (`02f39c8f`), same tab arrangement
- Video paused at 14:46 / 21:03 (Record 1 was at a different timestamp)
- URL: `youtube.com/watch?v=80JPF4vnhtA`
- Same tab bar: "DSA MID Paper Discu...", "Mail - WIJESINGHA W...", "Dashboard | CourseW...", (active YouTube), "mail.google.com", "DSA Past Paper Midte..."
- System clock: 11:01 PM 10/1/2026

**PII assessment:**
- Identical to Record 1 assessment. No PII visible in the main frame.
- Same mail tab open but not visible.
- DATE confirmed: "01st October 2026" in video title.
- This screenshot is from the same YouTube session as Record 1, captured ~3 hours later (Record 1 was captured earlier in the session).

**OCR miss check:**
- No PII to miss.

**Suggested risk label:** LOW  
**Confidence:** High  
**Uncertainty:** Same as Record 1 — mail tab.  
**Human confirmation required:** Yes — consistent with Record 1 determination.

---

### Record 8 — `024fd28f-aeb8-42dc-8484-9a8e2cd3ecf5.jpg`
**Group G5 | Annotated MEDIUM, all-zero flags | Pipeline: DATE=1, auto LOW**

**What is visible:**
- SLIIT CourseWeb search results page
- URL: `courseweb.sliit.lk/course/search.php?q=IT1070`
- **Page header clearly shows: `IT23252936 WIJESINGHA W L S A`** — the annotator's full student name and student ID, displayed in the authenticated navigation bar
- Search query: "IT1070" (course code search)
- Result: "No courses were found with the words 'IT1070'"
- SLIIT support section shows: phone `+94 11 754 4801` (institutional SLIIT support phone, not personal), `support.sliit.lk`
- Calendar: October 2026
- System clock: 7:34 AM 10/5/2026

**PII assessment:**
- **Full student name AND student ID `IT23252936 WIJESINGHA W L S A` plainly visible** in authenticated header
- This is the most direct personal identification visible in the entire flagged set
- The annotation all-zero flags (EMAIL=0, PHONE=0, URL=0, IP=0, DATE=0, PATH=0) are entirely incorrect — the student ID and name are clearly visible
- Pipeline DATE=1 (calendar date), but missed the student name/ID
- SLIIT phone `+94 11 754 4801` is institutional (public), not personal — PHONE=0 is debatable for institutional numbers

**OCR miss check:**
- YES — both the student name and student ID in the navigation header were missed by OCR. This is an OCR gap: authenticated user display names in navigation bars were not extracted.
- This is the second significant OCR miss in Group G5 (after Record 5).

**Suggested risk label:** HIGH — authenticated full name + student ID visible  
**Confidence:** Very high — identity is unambiguous  
**Uncertainty:** None on the label. The entity flag set doesn't include a "student_id" type; the closest mapping is to add EMAIL/PHONE depending on what regex would detect, but the name is clearly visible even if not regex-detectable.  
**Human confirmation required:** Yes — supervisor must approve correction. The annotation needs a new entity category or `annotation_notes` to document this.

**ACTION:** Annotation flags incorrect (all-zero). Visual evidence: full name + student ID in authenticated header. Correct label: HIGH. Requires discussion of whether student IDs fall under existing flag categories or require `annotation_notes` per §6.

---

### Record 9 — `033abfbe-b7f6-4c58-a769-8ffe082b38e4.jpg`
**Group G5 | Annotated MEDIUM, all-zero flags | Pipeline: DATE=1, auto LOW**

**What is visible:**
- ChatGPT desktop application
- Conversation: "DSA IT1170 lab test" — linked list study session (Singly vs Doubly Linked Lists)
- ChatGPT explains linked list concepts in Sinhala with code examples
- Pinned conversations sidebar: **"Medicine"**, **"Pediatrics"**, **"Psychiatry"** (health-topic AI consultations), "Supervisors", **"Imeth"**, **"IME COFFE SHOP"**, "Service Platform Idea", "AI & Tech Daily Briefing", "Practice Cloud Questions", "Commercialization plan for Membe..."
- Projects: "DSA IT1170 lab test", "Exam Study Guide" (×2)
- At bottom: "Tania" contact
- System clock: 1:16 PM 10/1/2026

**PII assessment:**
- No email, phone, IP, or credentials visible
- Personal ChatGPT conversation metadata exposed: health-related topics (Medicine, Pediatrics, Psychiatry), personal contact names (Imeth, Tania), personal business projects (IME COFFE SHOP)
- The health-related pinned chats are a sensitive personal disclosure (implies the user has consulted AI about medical topics in those categories)
- The educational content (DSA linked lists) itself is not sensitive

**OCR miss check:**
- No regex-detectable PII was present. The MEDIUM annotation likely reflects visual sensitivity judgment of the health-related conversation titles.

**Suggested risk label:** MEDIUM by visual judgment (personal health conversation metadata, personal contacts). LOW per current guidelines (no detected entity flags).  
**Confidence:** Medium  
**Uncertainty:** Policy 1 decision required.  
**Human confirmation required:** Yes.

---

### Record 10 — `034e7035-d661-417e-8cab-6e3131d7a671.jpg`
**Group G5 | Annotated MEDIUM, all-zero flags | Pipeline: DATE=1, auto LOW**

**What is visible:**
- YouTube loading/buffering — black screen with spinner
- Video at 0:00 / 0:58
- **Browser tab bar visible:**
  - "IT4090_2026_7_4: IT4090 - Ass..." (course assignment tab)
  - **"Inbox (598) - sashiniwijesingha..."** — Gmail inbox tab with partial email address visible
  - "proposal M2.drawio - draw.io" (project diagram tab)
  - Active YouTube tab (buffering)
- URL: `youtube.com/watch?v=N0s-8EjrgTI`
- System clock: 9:57 AM 10/2/2026

**PII assessment:**
- **Gmail address partially visible in tab title: "Inbox (598) - sashiniwijesingha..."** — this is the beginning of `sashiniwijesingha83@gmail.com`
- The OCR text_length = 248 chars (very sparse). The tab title text was likely in OCR scope but may not have been fully extracted.
- DATE pipeline detection is likely from the clock or assignment tab title

**OCR miss check:**
- PARTIAL MISS — the Gmail address in the tab title was available for OCR but may not have been extracted (OCR text = 248 chars, very short, suggesting minimal content capture).
- If OCR extracted the full tab title "Inbox (598) - sashiniwijesingha83@gmail.com", EMAIL would have been detected.

**Suggested risk label:** MEDIUM (partial email visible in tab title; tab content not visible)  
**Confidence:** Medium — the email is partially visible but the full address requires inference  
**Uncertainty:** Whether "sashiniwijesingha..." in a tab title constitutes EMAIL detection under the pipeline regex  
**Human confirmation required:** Yes — check whether the full Gmail address was in the OCR output, and whether partial email in a tab title qualifies for EMAIL=1.

---

### Record 11 — `00a3b7cb-0490-4a19-8f11-e37dad461566.jpg`
**Group G6 | Annotated MEDIUM, DATE=1 | Pipeline: DATE=1, auto LOW**

**What is visible:**
- Windows 11 desktop — clear desktop view, no active windows
- Desktop folder/file icons visible:
  - **`IT23252936`** (folder, top right) — student ID as folder name
  - **`IT23252936 AWS CLOUD...`** (folder, left side) — student ID in folder name
  - Other folders: "RESERCH NEW", "It1170-dsa-...", "DSA Stack...", "study materials", "RESEARCH", "IME project", "IME new", "IT3080"
  - Files: "DSA mid.pdf", "Practical Test Questionci...", "Scolar Soft...", "Midterm Past Papers - GL...", "Intern monthly", "Group Assignmenti...", "CV", "dsa"
  - Shortcuts: "google-rec...", "github-rec...", "Downloads - Shortcut", "Desktop Researc..."
  - Apps: "Anaconda Desktop", "IntelliJ IDEA 2026.2.3"
- System clock: 1:37 PM 9/30/2026

**PII assessment:**
- Student ID `IT23252936` visible in two folder names on the desktop
- No email, phone, or IP visible directly
- "CV" folder name implies personal curriculum vitae — not itself identifiable but context-sensitive
- The desktop view exposes the user's file organisation and personal project names
- WINDOWS_PATH: folder names with student ID are visible on the desktop, though not as a full path
- DATE: system clock date 9/30/2026 detectable

**OCR miss check:**
- The folder names with student ID are visible; OCR text_length = 439 chars — some desktop icon labels likely extracted. Whether `IT23252936` was extracted and whether it was processed as WINDOWS_PATH depends on the regex.

**Suggested risk label:** MEDIUM is defensible by visual judgment (student ID in folder names, personal file structure visible). LOW per current guidelines (DATE-only → LOW; no WINDOWS_PATH flag in annotation or pipeline).  
**Confidence:** Medium  
**Uncertainty:** Policy 2 (DATE + Desktop context → MEDIUM?) and whether folder names containing student IDs qualify as WINDOWS_PATH.  
**Human confirmation required:** Yes — Policy 2 + Policy 4 decisions needed.

---

### Record 12 — `0024e4cb-aacb-44f7-8fe2-5c633f1ec67f.jpg`
**Group G7 | Annotated LOW, DATE=1 | Pipeline: PHONE=1, DATE=1, auto HIGH**

**What is visible:**
- IntelliJ IDEA IDE (NOT VS Code — annotation application_context = "VS Code" is wrong)
- `Main.java` open with Java Queue implementation code (lines 1–20 visible)
- Code: `import java.util.Scanner; public clss Main { public static void man(string[args])... Scanner sc = new Scanner(System.in); int size = sc.nextInt(); Queue q1 = new Queue(size); ...`
- ChatGPT panel (right): "DSA IT1170 lab test" conversation
- ChatGPT response in Sinhala explaining exam question patterns
- User's partial message: "mt mokuth ba help laranna vinadi 10" (Sinhala: "help me with this exam in 10 minutes")
- Project path in IDE: `untitled3 — C:\Users\ASUS\Ideaf...`
- System clock: 1:58 PM 9/27/2026

**PII assessment:**
- **No phone number visible** in code, ChatGPT text, or any part of the screenshot
- The Java code contains no phone-like digit sequences
- Pipeline PHONE=1 is very likely a **FALSE POSITIVE** — possibly triggered by a digit sequence in the Sinhala text or code structure
- Annotation application_context = "VS Code" is incorrect (IntelliJ IDEA is shown)
- Partial Windows path `C:\Users\ASUS\Ideaf...` visible

**OCR miss check:**
- No phone number was present to miss. The pipeline detection appears to be a false positive.

**Suggested risk label:** LOW (annotation is correct)  
**Confidence:** High (no phone number visible)  
**Uncertainty:** What digit sequence triggered the pipeline's phone regex  
**Human confirmation required:** Yes — confirm no phone number is elsewhere in the screenshot. Also: correct `application_context` from "VS Code" to "IntelliJ IDEA" in annotation.

---

### Record 13 — `00c34dc6-612f-43cb-9b9f-297d92b6868a.jpg`
**Group G7 | Annotated LOW, DATE=1 | Pipeline: PHONE=1 (×3), DATE=1, auto HIGH**

**What is visible:**
- VS Code with AWS CloudFormation YAML template `IT23252936 IaC template.yaml`
- **Breadcrumb path:** `C: > Users > ASUS > OneDrive > Desktop > IT23252936 AWS CLOUDE COMPUTING INDIVIDUAL ASSIGNMENT > IT23252936 AWS CLOUDE COMPUTING INDIVIDUAL ASSIGNMENT > IT23252936 IaC template.yaml`
- YAML content visible (lines 1–36):
  - Line 1: `AWSTemplateFormatVersion: '2010-09-09'`
  - Line 4: `it23252936 - tomorrow day cloud computing assignment.`
  - Line 5–6: infrastructure description (VPC, EC2, RDS, S3)
  - Line 8: `# Student ID used for resource names and tags`
  - Line 10: `studentid:`
  - Line 12: `Default: it23252936` — **student ID as CloudFormation default parameter**
  - Line 13: `Description: student id used in resource names and tags.`
  - Lines 15–19: `dbusername: Type: String, Default: admin`
  - Lines 21–27: `dbpassword: ...NoEcho: true, MinLength: 8` — DB password config

**PII assessment:**
- **Student ID `IT23252936` appears multiple times**: in filename, in full Windows path (breadcrumb), in line 4 (description), and in line 12 (CloudFormation default parameter)
- **Full Windows path** is visible: `C:\Users\ASUS\OneDrive\Desktop\IT23252936 AWS CLOUDE COMPUTING INDIVIDUAL ASSIGNMENT\...`
- Pipeline PHONE=1 (×3): almost certainly **FALSE POSITIVE** — digit sequence `23252936` from `it23252936` matches an 8-digit phone regex. This is a student ID, not a phone number.
- `'2010-09-09'` (AWS format version date) explains one DATE match; another date may be from system clock
- The `admin` username and `dbpassword` configuration describe a database setup but no actual credential values are shown

**OCR miss check:**
- YES — annotation missed the Windows path (WINDOWS_PATH=0 when full path is clearly visible in breadcrumb), and missed the student ID presence. The annotation flags are incomplete.

**Suggested risk label:** HIGH — student ID in multiple places, full Windows path visible  
**Confidence:** High  
**Uncertainty:** Pipeline phone detections are false positives on student ID digits  
**Human confirmation required:** Yes — supervisor must approve. Correct to WINDOWS_PATH=1, label=HIGH, annotate `annotation_notes` to explain student ID in path and template.

---

### Record 14 — `00e7560f-f51a-4d8a-ad26-f6461fe5d6de.jpg`
**Group G7 | Annotated LOW, DATE=1 | Pipeline: PHONE=1, DATE=1, auto HIGH**

**What is visible:**
- IntelliJ IDEA IDE (NOT VS Code — annotation context wrong, same as Record 12)
- `Main.java` with Queue code (slightly earlier version — only 13 lines, line 11 shows autocomplete suggestion)
- ChatGPT panel: same "DSA IT1170 lab test" conversation as Record 12 (captured at 1:52 PM, 6 minutes earlier)
- Same Sinhala exchange about exam help
- System clock: 1:52 PM 9/27/2026

**PII assessment:**
- **No phone number visible** — same assessment as Record 12
- This is a paired screenshot with Record 12 from the same coding session, 6 minutes apart
- Same false positive situation: pipeline PHONE=1 triggered by the same digit pattern
- Autocomplete popup shows `size: int` — no phone-like content

**OCR miss check:**
- No phone number present to miss.

**Suggested risk label:** LOW (annotation correct)  
**Confidence:** High  
**Uncertainty:** Same as Record 12  
**Human confirmation required:** Yes — also correct application_context from "VS Code + ChatGPT" to "IntelliJ IDEA + ChatGPT".

---

### Record 15 — `014635cf-027c-4bec-a6c2-304828e4e9f9.jpg`
**Group G8 | Annotated LOW, all-zero flags | Pipeline: PHONE=1, DATE=1, auto HIGH**

**What is visible:**
- ChatGPT desktop application — "Medicine" pinned conversation
- **Conversation content (Sinhala):**
  - ChatGPT: "ඔයාට දැන් **Yasmin** packet එක ළඟ තිනෙනවා නම් photo එකක් එවන්න. මම එකේ exact tablet එකත් expiry බලලා, **කොහොමද prescribed** කරලා තිබුණේ කියලා **තේරුම් කරලා දෙන්නම**."
  - Translation: "If you have a Yasmin packet near you now, send a photo. I'll look at the exact tablet and expiry, and explain how it was prescribed."
  - User response: "na mn laga kalin ewa na" (Sinhala: "No, I don't have one near me now")
- Pinned conversations: **"Medicine"**, **"Pediatrics"**, **"Psychiatry"** (health AI consultations), "Supervisors", "Imeth", "IME COFFE SHOP", "Service Platform Idea", "AI & Tech Daily Briefing", "Commercialization plan for Membe..."
- Projects: "Cloud Computing 2.0", "DCN project", ".NET"
- Contact: "Tania" at bottom
- System clock: 7:16 PM 10/6/2026

**PII assessment:**
- **No email, phone number, IP address, or credentials visible** — annotation all-zero flags are technically correct for the regex-based entity set
- **However: this screenshot contains highly sensitive personal health information**
  - Yasmin is a brand of oral contraceptive pill — the conversation reveals personal reproductive health decision-making
  - The user is consulting AI about their prescription medication
  - The pinned "Medicine", "Pediatrics", "Psychiatry" chats indicate ongoing AI-mediated health consultations
- Pipeline PHONE=1: almost certainly **FALSE POSITIVE** — possibly triggered by a digit sequence or the "tenna" pattern in Sinhala
- DATE=1: system clock date visible

**OCR miss check:**
- No regex-detectable PII was present. The sensitivity is in the content, not in structured entities.

**CRITICAL POLICY GAP:** The current annotation guidelines and entity flag set (EMAIL, PHONE, URL, IP_ADDRESS, DATE, WINDOWS_PATH) have **no mechanism to capture health/medical information sensitivity**. This screenshot would be labelled LOW by the current rules, but it contains among the most sensitive personal information in the dataset. This represents a fundamental limitation of the entity-flag approach.

**Suggested risk label:** LOW per current guidelines (no detected entities). HIGH by any real-world sensitivity assessment.  
**Confidence:** High on the guideline-based LOW; the discrepancy is a policy gap, not a review error  
**Uncertainty:** None on factual assessment. The policy gap is the uncertainty.  
**Human confirmation required:** Yes — supervisor must review this policy gap. Recommend adding a "SENSITIVE_CONTENT" flag or equivalent to handle health, financial, and other non-entity sensitive topics.

---

### Record 16 — `02488e7f-132f-4e80-b164-d34cf38424ff.jpg`
**Group G8 | Annotated LOW, all-zero flags | Pipeline: PHONE=1 (×3), IP=1 (×2), DATE=1, auto HIGH**

**What is visible:**
- VS Code — same CloudFormation YAML file as Record 13 (`IT23252936 IaC template.yaml`), scrolled to lines ~96–187
- **Same breadcrumb path visible**: `C: > Users > ASUS > OneDrive > Desktop > IT23252936 AWS CLOUDE COMPUTING INDIVIDUAL ASSIGNMENT > IT23252936 AWS CLOUDE COMPUTING INDIVIDUAL ASSIGNMENT > IT23252936 IaC template.yaml`
- YAML content (security group section):
  - Lines 168–169: `FromPort: 80`, `ToPort: 80`
  - Lines 170–171: `CidrIp: 0.0.0.0/0` — "http from the internet"
  - Lines 173–176: SSH port 22, `CidrIp: 0.0.0.0/0`
  - Line 183: `Value: !Sub '${studentid}_webserversg'` — student ID referenced in resource name template
  - Lines 184–185: `Key: studentid`, `Value: !Ref studentid`
- System clock: 11:47 AM 10/3/2026

**PII assessment:**
- **Full Windows path visible** (same as Record 13) with student ID in multiple directory names
- **Student ID `IT23252936`** referenced in `${studentid}` substitution variable
- Pipeline IP=1 (×2): triggered by `CidrIp: 0.0.0.0/0` — this is a CIDR specification meaning "all IP addresses", NOT a real target IP address. **FALSE POSITIVE.**
- Pipeline PHONE=1 (×3): almost certainly FALSE POSITIVE on student ID digits `23252936` appearing in the file — same pattern as Record 13
- Port numbers 22, 80 are not phone numbers

**OCR miss check:**
- YES — annotation WINDOWS_PATH=0 when full path is clearly visible in breadcrumb. Same miss as Record 13.

**Suggested risk label:** HIGH — full Windows path with student ID visible  
**Confidence:** High  
**Uncertainty:** Pipeline IP detections are false positives (CIDR blocks, not real IPs)  
**Human confirmation required:** Yes. Correct WINDOWS_PATH=1, label=HIGH. Pipeline false positives for PHONE and IP in infrastructure code should be documented as a known limitation.

---

## Cross-cutting findings from visual inspection

### F1 — Two major OCR gaps confirmed

**Gap A — UI sidebar and account panels**: Records 5 and 8 both show real PII in UI side panels (OneDrive account names, authenticated navigation headers) that OCR did not extract. These are among the highest-risk screenshots in the dataset. The pipeline's OCR appears not to process UI chrome/sidebar text consistently.

**Gap B — Browser address bar / navigation URLs**: Record 3 confirms URL=1 is correct in the annotation but pipeline URL=0. The URL in the browser address bar was not extracted by OCR. This is a consistent limitation: address bar text is not reliably captured.

### F2 — Pipeline phone regex fires on student ID digit sequences

Records 12, 13, 14, 16 (and Record 4 via annotation's phone flag conflict) all involve student ID numbers (`IT23252936` → digits `23252936`, 8 digits). The phone regex appears to match any 8+ digit sequence, producing false positives on:
- Student IDs in CloudFormation templates (Records 13, 16)
- Student IDs in other screen content (Records 12, 14)

**Recommendation:** Refine the phone regex to require a leading `+` or country code prefix, or add a negative look-behind for the `IT` prefix pattern. The current regex over-generates on student IDs.

### F3 — Pipeline IP regex fires on CIDR blocks

Records 4 and 16 both involve CIDR specifications (`10.0.0.0/16`, `0.0.0.0/0`). The IP_ADDRESS regex is matching these as IP addresses. These are network specifications, not real IP addresses identifying a specific host or user.

**Recommendation:** Exclude CIDR blocks (pattern: `x.x.x.x/nn`) from IP_ADDRESS detection, or add a post-processing filter.

### F4 — Critical policy gap: health/sensitive content not captured by entity flags

Record 15 contains sensitive personal medical information (reproductive health consultation) but would be labelled LOW under current rules because no regex-detectable entity is present. The annotation entity flag approach cannot capture content-level sensitivity. This is a known limitation of structured annotation, but it is more acute here than in most contexts.

### F5 — Two application_context errors in annotation

Records 12 and 14 are annotated as "VS Code" but show IntelliJ IDEA. Record 14 is annotated "VS Code + ChatGPT" but shows IntelliJ IDEA + ChatGPT. These context errors do not affect the current labels (which are LOW based on DATE-only flags) but would affect any rule that elevates risk for specific application contexts.

### F6 — The HIGH class has no valid training examples

Combining the earlier metadata analysis with visual findings:
- Record 1 (`02f39c8f`): HIGH label is incorrect → should be LOW (confirmed visual)
- Record 3 (`00c019f8`): HIGH label is incorrect → should be MEDIUM (URL=1, not EMAIL/PHONE/IP)
- Record 5 (`00fa95dd`): should be corrected to HIGH (email confirmed visual)
- Record 8 (`024fd28f`): should be corrected to HIGH (name + student ID confirmed visual)

After corrections: the HIGH class would include Records 5 and 8 (and potentially Records 4 and 13 pending supervisor decision). The current HIGH class (Records 1 and 3 from the full 50-row set) contains no valid HIGH examples. The model cannot learn HIGH class behavior from the current annotations.

---

## Recommended actions (pending supervisor sign-off)

| Priority | Record | Action |
|---|---|---|
| P1 | 5 (`00fa95dd`) | Correct EMAIL=0→1, WINDOWS_PATH=0→1, label MEDIUM→HIGH. Two real emails and Windows path confirmed visual. |
| P1 | 8 (`024fd28f`) | Correct label MEDIUM→HIGH. Full name + student ID visible. Add `annotation_notes` — no email flag applies to student ID. |
| P1 | 1 (`02f39c8f`) | Correct label HIGH→LOW. No PII visible. |
| P1 | 3 (`00c019f8`) | Correct label HIGH→MEDIUM, URL=0→1. URL visible; not a personal URL. |
| P2 | 4 (`01bf28e2`) | Correct label LOW→MEDIUM (at minimum). Student ID in form, personal search history visible. Pending Policy 8 decision. |
| P2 | 13 (`00c34dc6`) | Correct WINDOWS_PATH=0→1, label LOW→HIGH. Full path + student ID in file confirmed visual. |
| P2 | 16 (`02488e7f`) | Correct WINDOWS_PATH=0→1, label LOW→HIGH. Same file as Record 13, same path visible. |
| P2 | 7 (`01a758f4`) | Correct label MEDIUM→LOW. No PII. Consistent with Record 1. |
| P3 | 12 (`0024e4cb`) | Add `annotation_notes`: pipeline PHONE=1 is false positive. LOW is correct. Correct `application_context` to IntelliJ IDEA. |
| P3 | 14 (`00e7560f`) | Same as Record 12. Correct `application_context` to "IntelliJ IDEA + ChatGPT". |
| P3 | 15 (`014635cf`) | Add `annotation_notes`: sensitive medical content present but not captured by entity flags. LOW per guidelines, HIGH by real-world sensitivity. Flag as policy gap. |
| P3 | 2 (`02e86b37`) | Awaits Policy decision on template emails. Document in `annotation_notes`. |
| P3 | 6, 9, 10, 11 | Awaits Policy 1, 2, 4, 6 decisions. Document visual findings in `annotation_notes`. |

---

## Decisions requiring supervisor approval

| Policy | Decision needed | Affects |
|---|---|---|
| Policy 1 | Can HIGH/MEDIUM be assigned when all OCR entity flags are zero but visual content is sensitive? | Records 1, 5, 8, 15 |
| Policy 2 | Does DATE alone → LOW, or can desktop/context elevate to MEDIUM? | Records 7, 11 |
| Policy 4 | Backfill `application_context` for blank rows — modify CSV in place or create versioned copy? | Records 5–10, 12, 14, 15, 16 |
| Policy 6 | Add `annotation_notes` column to capture visual reasoning not expressible in entity flags | All 16 |
| Policy 7 | Add `SENSITIVE_CONTENT` flag (or equivalent) for health, financial, credential contexts not captured by existing flags | Record 15 (and potentially others) |
| Policy 8 | Does any URL trigger MEDIUM, or only personal/credential URLs? Does navigation URL (YouTube video URL) trigger MEDIUM? | Records 3, 4 |
| Phone regex | Student ID digit sequences (`2XXXXXXX`) triggering PHONE=1 — fix regex or add exclusion rule? | Records 12, 13, 14, 16 |
| IP regex | CIDR blocks (`x.x.x.x/0` etc.) triggering IP=1 — exclude CIDR notation from IP detection? | Records 4, 16 |

---

*Visual inspection performed 2026-10-15 using the Read tool (image rendering). All 16 screenshots were inspected directly.*  
*No annotation CSV, candidates.csv, manifest, model artifact, or training log was modified.*  
*OCR processes were not interrupted.*  
*This report contains provisional findings only. No labels were changed. All corrections require supervisor sign-off.*
