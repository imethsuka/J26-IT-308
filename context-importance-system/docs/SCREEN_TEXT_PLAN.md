# Implementation plan: on-screen text capture, episodes, and deferred memory building

**Status (updated 2026-10-20):** phases 1 to 5 are built and tested; phases 6 and 7 are still plan only.
- Phase 5 done: `adaptation/screen_memory.py` (tiers, entries without an LLM), `adaptation/privacy.py` (the Member 2
  boundary + `tests/test_privacy_contract.py`), `storage/memory_store.py` (`memory.db`), hooked into `scheduled_job.py`
  as an opt-in stage (`--screen-memory`). Text is filtered per capture before merging (not after, as section 8 first said).
- Done: UIA reader (section 4), protocol v2 and the collector hook (off by default), the separate encrypted raw
  buffer `raw_buffer.db` (section 5), `features/text_merge.py` (section 7) and `adaptation/episodes.py` (section 6).
- Phase 5b done (app integration): dashboard tab *Screen text* (opt-in with a versioned consent dialog, default off),
  `--screen-text on|off` on the collector, `--no-screen-text` on the worker, `--screen-memory` on the job only when opted in,
  counts/sizes of the buffer, a list of memory entries, and *Delete all captured text*. Checked on a rebuilt bundle with the frozen worker.
- Not done: Member 2's real privacy filter (only the interface, a dev pass-through and a block-all exist), LLM
  summaries, text embeddings and kept image crops, text features for Model 1, a dashboard view of the entries. Not yet
  exercised live on a real desktop.
- Decision taken: three separate SQLite files, none of them `context.db`: `raw_buffer.db` (raw, encrypted, 3 days),
  `memory.db` (filtered documents, episodes, entries) and the existing `context.db` (no text). The table names in
  section 10 became `documents`, `episodes` and `memory_entries` in `memory.db`.
- Measured: merging a 50,000-character page takes about 8 ms (section 7 estimated under 5 ms).
**Owner:** Member 1 (acquisition). Hand-off to Member 2 (privacy), Member 3 (retrieval) and
Member 4 (consolidation), see §9.

## 1. Goal

Turn what the user sees into **memory entries** an AI assistant can use, like:

> *On 19/10/2026, 10:02–10:31, in Microsoft Edge on medium.com, you read about 85% of
> "Understanding LightGBM…". You returned to the section on categorical features.*

without slowing the PC down and without storing the same text over and over.

Three problems to solve:
1. **Duplicated text:** each capture shows only part of a page, overlapping the previous one; pages
   load, scroll and unload text unpredictably. Keep **one unified copy per page**.
2. **Compute:** summarising, embedding and OCR are expensive. Don't do them while the user works.
3. **Screenshots of things that are text:** a screenshot of code or an article is about 92 KB and can't
   be searched; its text is 2–5 KB and can. **Don't screenshot what can be read; keep images only of
   what can't be put into words** (photos, products, charts, diagrams), cropped to the image.

**Example, a shopping page:**
```
text (UIA):   "Oxford Cotton Shirt – Slim Fit · LKR 4,990 · Size M · Add to cart"
image (UIA):  Image 600×600 at (420,180), alt="Red Oxford cotton shirt"
URL:          https://www.example-shop.lk/men/shirts/oxford-slim-red?size=M
→ keep:       text (3 KB) + shirt crop (~25 KB) + MobileCLIP vector + URL
→ skip:       the full screenshot (92 KB)
later: "red shirt I saw last week" → matched by text AND by the picture → URL back to the shop
```

## 2. Architecture: capture now, understand later

```
REAL TIME  (cheap, while the user works)            DEFERRED  (idle + on AC power, e.g. at night)
──────────────────────────────────────              ─────────────────────────────────────────
screen change → screenshot (RAM)                     1. group raw captures into EPISODES (§6)
window, site, title, activity counts                 2. merge text per page, de-duplicated (§7)
UIA text read (~ms)               (§4)               3. Member 2: privacy filter
Model 1: keep / discard  (<1 ms)                     4. MobileCLIP / OCR on kept frames only
        │                                            5. small local LLM writes the entry (§8)
        ▼                                            6. write to the memory layer → Members 3/4
raw buffer (local, encrypted, TTL)  ───────────────► 7. delete the raw buffer
```

**What must be real time:** only reading the text. It exists only while it's on screen; after a
scroll or a closed tab, it's gone. UI Automation reads it in milliseconds.

**What waits:** everything expensive or needing the whole picture. At night the job sees the whole
episode (all text, total reading time, whether the user came back), which a single capture never shows.

**Where it runs:** a new stage of `adaptation/scheduled_job.py`, which already waits for idle + AC
power (`adaptation/host_state.py`).

| Part | Where | When |
|---|---|---|
| Read visible text (UIA) | C# collector | real time |
| Keep/discard (Model 1) | Python worker | real time |
| Raw buffer (captures + text) | Python worker, local disk | real time (write) |
| Episodes, text merge, privacy, summaries, embeddings | Python worker, deferred job | idle + AC |
| Memory layer (entries) | Members 3/4 | after the deferred job |

## 2b. What form to keep (decided per capture, in real time, cheap)

| Situation (from §4's read) | Keep |
|---|---|
| Model 1 says discard | nothing (metadata only, for labels) |
| Text read, no large images on screen | **text only** |
| Text read + large images (UIA `Image` elements ≥ 64 px per side) | **text + image crops** (cropped from the frame already in memory) |
| Little or no text, but the screen is mostly visual (video, canvas, game, dashboard, slide) | **full screenshot** |
| Nothing exposed by UIA at all | **full screenshot** (MobileCLIP decides later whether it's worth keeping) |

- **Duplicate images** (the same product photo while scrolling): compare MobileCLIP vectors in the
  deferred stage, and keep one.
- **Small images** (icons, logos, avatars) are ignored by the size limit. MobileCLIP can drop "a logo or
  icon" crops later.
- **The full URL** is kept with the entry (today only the domain reaches the worker). Removing
  tokens, session IDs and emails from query strings is Member 2's privacy step.
- **Photos of people** go through Member 2's privacy step before storage.
- MobileCLIP's main job becomes **describing the kept image crops** (photos are what it was
  trained on), for Member 3's search, not scoring UI screenshots.

## 3. Model 1 decides how much work each episode gets

Model 1's importance score now also controls **deferred compute**, not just storage:

| Episode importance | Deferred processing |
|---|---|
| High (≥ keep threshold) | full: merged text, privacy, embeddings, kept screenshots, LLM summary |
| Medium | merged text + privacy + short metadata entry, no LLM, no screenshots |
| Low | one metadata line (app, site, title, time, duration), and the raw text is dropped |

This is a measurable claim for the report: **storage and compute saved** at a given recall of
important episodes (same threshold-free method as `offline/training.py::ranking_metrics`).

## 4. Real time, C#: reading visible text, images and URL (`Content/`)

Implemented in step 1 as `Content/ScreenContentExtractor.cs` (the walk, testable on a fake tree) +
`Content/UiaContentNode.cs` (the UI Automation adapter). It returns the visible text plus every
large on-screen **image element** (bounds in screen pixels + alt text). The full URL comes from the
existing `UiaBrowserUrlReader`.

**Order of checks (fail closed, same as screenshots):**
1. `ContextGate`: the context must be evaluated and **not excluded** (app and domain rules).
2. Never read password fields (`IsPasswordProperty = true`), nor anything inside them.
3. Never read the edit field being typed into (keyboard focus): that's the user's typing, the same
   rule as the address bar.
4. Read, with hard limits.

**How (as built in step 1):**
- **Browser pages / documents:** one **bulk request** for every on-screen Text, Edit and Image
  element below the `Document` (`FindAll(Descendants)` with a cache request, so properties come back
  in the same call). If it finds no text (rich-text editors), fall back to the document's
  `TextPattern.GetVisibleRanges()` when time is left.
- **Measured, why it's built this way (Edge, i5-11400H, 2026-10-19):** `TextPattern` visible ranges
  took **2,168 ms** in one call on a web page, and walking the page element by element took about
  700 ms. The bulk request reads the same page in **median 221 ms, p95 278 ms** (8/8 reads, none
  truncated): about 14× faster.
- **Other apps:** walk the tree (control view, properties cached per level) and collect the names
  of on-screen Text, Hyperlink, list/tree/data items, and the values of Edit fields. Window chrome
  (menus, toolbars incl. the address bar, title bar, tabs, scroll/status bars) is neither read nor walked.
- **Limits** (`screenContent` in `collector.config.json`): 400 ms, 3,000 nodes, 50,000 characters,
  images ≥ 64 px per side. On a limit, return what was read with `truncated = true`. A failed read is
  retried once (pages invalidate elements while re-rendering). It runs on a worker thread, never the hook thread.
- **Measure on your own apps:**
  `dotnet run -c Release --project benchmarks/CsharpCollector.Benchmarks -- --content [--samples 30]`
  (switch apps while it runs; it records counts and timings only, never text), and `-- --trace` to see
  which accessibility call is slow on one window.
- **Electron apps** (VS Code, Teams) expose little: record `source = "uia-partial"`. The OCR
  fallback is decided in §9.
- **When:** on each captured frame (the same trigger as the screenshot), so text and frame match.

**Output:** whitespace collapsed, block line breaks kept, invisible characters removed (reuse the
`features/titles.py` rules).

**IPC protocol v2:** add to the `context_snapshot` JSON header (the frame payload is unchanged):
```json
"text": { "source": "uia", "chars": 4210, "truncated": false, "read_ms": 38.5, "content": "..." }
```
- `"text": null` when excluded, a password field, nothing exposed, or the feature is off.
- `version` 1 → 2. The worker accepts both; v1 means no text.
- **Invariant:** update together C# `Ipc/SnapshotHeader.cs` + `Ipc/WireFormat.cs`, Python
  `sensing/protocol.py`, and the README "IPC transport" section.
- Config: the `screenContent` section (step 1). **Off by default** until consent and the Member 2
  hand-off are agreed (§10).

## 5. Real time, Python: the raw buffer

Each non-excluded snapshot appends a small record to a **raw buffer** (SQLite table `raw_captures`):
`ts`, context key, Model 1 score/decision, activity counts, the frame id if kept, and the **raw text**.

- **Encrypted at rest:** Windows DPAPI (current-user scope) via `ctypes`, no extra dependency. Only
  the deferred job decrypts.
- **Time limit:** rows older than `raw_ttl_days` (default 3) are deleted whether processed or not.
  The deferred job deletes rows as soon as it has processed them.
- **Size limit:** if the buffer passes `raw_max_mb`, drop the raw text of the lowest-importance rows
  first (keep their metadata).
- Kept **frames** stay as today (RAM → kept-frame store per the "capture to SSD" decision); the
  buffer only references them.

## 6. Deferred: episodes

An **episode** is a continuous stretch with the same page/document: consecutive captures with the
same context key `(app, domain, normalized title)` (the v4 key used for labels), split when the
user is away for more than 30 minutes. Returning later starts a **new episode linked to the same
document**, so "came back on Tuesday" is visible.

Per episode: start/end, active time (idle excluded), number of captures, activity totals, scroll
pattern, max Model 1 score, kept frames, `doc_id`.

## 7. Deferred: merging text into one copy per page (`features/text_merge.py`)

The episode's captures are merged into the **document** (one per context key, across episodes and
days). Merge each capture `N` into the document's **fragments** (word lists; usually one):

1. Tokenize into words (keeping the original spans for reconstruction).
2. Index each fragment by **8-word shingles** (a hash of each run of 8 consecutive words).
3. Find the overlap with a fragment `F` through shared shingles, then check it word by word:
   - `N` inside `F` → nothing new.
   - end of `F` overlaps start of `N` → **append** the rest (scrolling down; works when `N` starts mid-sentence).
   - end of `N` overlaps start of `F` → **prepend** (scrolling up).
   - `F` inside `N` → replace (more of the page loaded).
   - no overlap → **new fragment** (jumped far down).
4. Join fragments that now overlap (a gap filled).
5. **Never delete** text that scrolled away.

- **Small changes** (live counters, timestamps, edits): allow ≤10% mismatched words inside an overlap,
  and the newer wording wins for that span. Above that, make a new fragment, so nothing is silently overwritten.
- **Minimum overlap:** 8 words (so short repeated phrases like "Click here" don't join unrelated text).
- **Cost:** linear in text length, well under 1 ms for a few KB.
- **Per capture:** `new_words` / `new_ratio` (how much new text it added), used for reading progress (§8)
  and as candidate Model 1 features (§11).

## 8. Deferred: the memory entry

For each episode, by importance tier (§3), build an entry:

```
Episode: 19/10/2026 10:02–10:31 (29 min active)
App: Microsoft Edge · Site: medium.com
Page: "Understanding LightGBM: Gradient Boosting Explained"
Activity: read ~85% of the page (text merged from 14 captures), scrolled steadily, little typing
Summary: "Read an article explaining how LightGBM grows trees leaf-wise and why it is faster than
          XGBoost on large tabular data; returned to the section on categorical features."
Kept screenshots: 3 · Importance: 0.82 · Revisited within 48 h: yes
```

- **Reading progress** comes from the merge: the share of the page's text that appeared on screen, and the active time.
- **Summary:** a small **local** LLM (1–3B parameters, for example Qwen2.5-1.5B or Phi-3-mini, through
  Ollama, already installed). It runs on the **privacy-filtered** text only, never sending
  anything off the PC. Roughly seconds per episode on CPU. Set a daily limit and process the
  highest importance first.
- **Embeddings** (MobileCLIP for kept frames, a text embedding for the merged text) for Member 3's search.
- **Labels catch up later:** "revisited within 48 h" is filled in by label construction, as today.

## 9. Ownership and hand-offs

| Step | Owner | Hand-off to agree |
|---|---|---|
| Text read, raw buffer, Model 1, episodes, text merge | **Member 1** | the **episode format** (§6–8) |
| Privacy filter on merged text (and OCR fallback?) | **Member 2** | `PrivacyFilter` interface: `text → text` (injected, like the retrieval callback in `adaptation/label_construction.py`) |
| Search over entries, retrieval feedback | **Member 3** | entry schema; retrieval events flow back into Model 1's labels |
| Long-term memory: consolidation, summaries over time, forgetting | **Member 4** | who writes the entry text/summary: us (per episode) or Member 4 (DMCLM) |

## 10. Storage and privacy rules

- Excluded apps/domains and password fields are **never read** (§4).
- Raw text lives only in the **encrypted, time-limited raw buffer** (§5) until the deferred job
  processes it. **Nothing un-filtered reaches the memory layer.** Until Member 2's filter exists,
  the job runs with a pass-through filter **only in dev mode on the author's own data**, and
  `memory.persist_text = false` otherwise.
- **Consent:** current participant consent covers screenshots and window titles, **not extracted text
  or summaries**. Update the consent form before enabling `screenText` for any other participant.
- Expect more antivirus attention (screen text + keyboard hook in one unsigned process). Code-sign.

New tables (migration in `storage/schema.py`): `raw_captures` (§5), `text_documents` /
`text_fragments` (§7), `episodes` (§6), `memory_entries` (§8, or Member 4's store).

## 11. Optional experiment: text features for Model 1

The deferred job can't feed the **real-time** decision, but the cheap real-time numbers can:
`txt_visible_chars`, `txt_new_ratio` (from an in-memory, last-few-captures overlap check, which is
cheap), `txt_has_numbers`, `txt_has_code`. Evaluate them like the screen categories: rolling evaluation,
threshold-free metrics, with vs without.

## 12. Tests

- **C#:** `UiaTextReader` against a fake element tree (visible-only, passwords skipped, excluded never
  read, limits → `truncated`); wire format v2 round-trip; v1 still accepted.
- **Python merge** (`tests/test_text_merge.py`): the three Lorem Ipsum captures from this discussion →
  one copy, nothing duplicated; a capture starting mid-sentence; scroll up; jump down then gap filled;
  a small edit within the threshold; a large change → new fragment; overlaps < 8 words don't join;
  50,000 chars merged in < 5 ms.
- **Raw buffer:** encrypt/decrypt round-trip; TTL deletion; size cap drops low-importance text first.
- **Episodes:** splitting on context change and on 30-minute gaps; returning links to the same document.
- **Deferred job:** tiers by importance; idle/AC gating (reuse the scheduled-job tests' fakes);
  daily limit respected; buffer rows deleted after processing.

## 13. Phases

| Phase | Work | Rough size |
|---|---|---|
| 1 | C# `UiaTextReader` + config flag + tests; **measure read time** on Edge, Chrome, Word, Notepad, VS Code | 2–3 days |
| 2 | Protocol v2 (C# + Python + README together) + IPC tests | 1 day |
| 3 | Raw buffer (DPAPI encryption, TTL, size cap) | 1–2 days |
| 4 | Episodes + `text_merge.py` + tests | 3–4 days |
| 5 | Deferred job stage: tiers, `PrivacyFilter` boundary, entry building (no LLM yet) | 2 days |
| 6 | Local LLM summaries (Ollama) + daily limit; measure time per episode | 1–2 days |
| 7 | Experiment: real-time text features for Model 1; compute saved by tiering (§3) | 1–2 days |

## 14. Open decisions

1. **Episode format and who writes summaries:** us, or Member 4 (DMCLM)?
2. **OCR fallback** for apps exposing no text: ours, or Member 2's (it's in their charter)?
3. **Consent:** who may have `screenText` enabled, and when?
4. **Raw buffer limits:** `raw_ttl_days` (3?) and `raw_max_mb`?
5. **LLM choice and limit:** which local model, and how many summaries per day?
