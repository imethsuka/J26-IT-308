# How screen text capture works

For teammates (Members 2, 3 and 4) and anyone new to the project. It explains what is built, where the data goes, and
what you can plug into. The design and the reasons behind it are in [SCREEN_TEXT_PLAN.md](SCREEN_TEXT_PLAN.md).

Status: written 2026-10-20. Everything below is built and tested except where it says **not built**.

## 1. What it does, in one paragraph

While you work, the system can read the words that are visible in your active window (a web page, a document, a chat).
It keeps that text for a few days, encrypted, on your own PC. At night, when the PC is idle and plugged in, a job groups
the captures into "episodes" (one stretch in one page), merges the text of a page into a single copy, passes it through a
privacy filter, and writes a short **memory entry** such as: *"20/10/2026 10:02-10:31, Edge, medium.com, "Understanding
LightGBM", visit 2 of this page."* Other components (search, long-term memory) read those entries.

It is **off by default**. A person has to turn it on and agree to a consent text.

## 2. The big picture

```
 WHILE THE USER WORKS (cheap)                             AT NIGHT (idle + on AC power)
 ----------------------------                             -----------------------------
 C# collector                                             adaptation/scheduled_job.py  (stage 6)
   screen changes -> take a frame                           |
   read visible text + big images (UI Automation)           |  1. find closed episodes in raw_buffer.db
   send ONE message to the worker                           |  2. tier each: high / medium / low (Model 1 score)
        |                                                   |  3. PrivacyFilter (Member 2) on each capture's text
        v                                                   |  4. merge into one document per page
 Python worker                                              |  5. write document + episode + entry to memory.db
   Model 1 scores the context (keep / discard)              |  6. delete the raw rows
   stores the record in context.db (no text)                v
   stores the text in raw_buffer.db (encrypted)          memory.db  ---->  Members 3 and 4 (search, consolidation)
```

Three separate SQLite files, none of them mixed:

| File | Holds | Who reads it |
|---|---|---|
| `context.db` | One scored row per context: app, domain, title, features, decision. **No text.** | worker, dashboard, training |
| `raw_buffer.db` | The raw screen text, encrypted, kept 7 days by default (Settings, 1 to 90) | only the nightly job |
| `memory.db` | Filtered merged pages, episodes, memory entries | Members 3 and 4 |

All three sit in the same folder: `%LOCALAPPDATA%\context-importance\` for the installed app, or `python-worker\` when
running from the repo. `raw_buffer.db` and `memory.db` are only created when the first text arrives.

## 3. Step by step

### 3.1 Reading the text (C#, real time)
- `csharp-collector/src/CsharpCollector/Content/` reads the foreground window with Windows UI Automation. It returns the
  visible text and every large image element (position and alt text). Limits: 400 ms, 3,000 nodes, 50,000 characters.
- `ForegroundScreenTextSource.cs` decides **whether** to read. It reads only when the context is evaluated and allowed. It
  never reads an excluded app or site, a password field, or the box you are typing in. If you switch window during the
  read, the result is thrown away.
- The text travels in the same message as the frame, in a `text` object (protocol version 2). Version 1 messages (no text)
  are still accepted. The format is defined in three places that must stay in sync: C# `Ipc/SnapshotHeader.cs` and
  `Ipc/WireFormat.cs`, Python `sensing/protocol.py`, and the README section "IPC transport".

### 3.2 Storing the raw text (Python worker, real time)
- `pipeline.py` stores the normal record in `context.db`, then hands the text to `storage/raw_buffer.py`.
- The text is compressed (zlib) and encrypted with **Windows DPAPI** (current user). If it cannot be encrypted, it is not
  stored. The table is `raw_captures`; the text is in the single column `text_blob`.
- Limits: rows older than the keep time (7 days by default, set in Settings) are deleted. If the stored text passes 200 MB, the text of the lowest-scored rows is
  dropped first (the row and its metadata stay).
- Losing a capture's text never loses its record in `context.db`, and the text is never written to a log.

### 3.3 Episodes (nightly)
- `adaptation/episodes.py`. An **episode** is a run of captures in the same context, where context = app + domain +
  normalized title. It ends when you change context or are away more than 30 minutes.
- Coming back later is a **new episode of the same document** (same `doc_id`), so "visit 2, visit 3" is visible. An
  episode that is still in progress (its last capture is under 30 minutes old) is left for the next run.

### 3.4 One copy per page (nightly)
- `features/text_merge.py`. Each capture only shows the part of the page that is on screen, and consecutive captures
  overlap. The merge finds the overlap (8-word runs) and joins them: scrolling down appends, scrolling up prepends, a
  bigger capture replaces a smaller one. Text that scrolls away is never deleted, and a large change makes a new fragment
  instead of overwriting. Merging a 50,000-character page takes about 8 ms.

### 3.5 Tiers (nightly)
Model 1's highest score during the episode decides how much work it gets. The thresholds are starting guesses, not tuned.

| Tier | Score | What is kept |
|---|---|---|
| high | 0.5 or more | privacy-filtered merged text + full entry, marked `needs_summary` for a future LLM stage |
| medium | 0.2 to 0.5 | privacy-filtered merged text + shorter entry |
| low | under 0.2 | one metadata line; the raw text is dropped, never merged or stored |

### 3.6 Privacy filter (Member 2) - the boundary
- `adaptation/privacy.py` defines the interface. Nothing in this repo decides what is sensitive; that is Member 2's job.
- Rules the job applies around the filter:
  - **No filter, no text.** Entries then contain only metadata and no text is kept.
  - **Fail closed.** If the filter raises an error, returns something that is not text, or says `blocked`, that capture's
    text is dropped.
  - Text is filtered **per capture, before merging**, so the stored page is always filtered.
  - Nothing unfiltered reaches `memory.db`.
- A pass-through filter exists for development on your own data only (`--dev-passthrough-filter`). It protects nothing.

### 3.7 The memory entry (nightly)
`memory.db` tables (code: `storage/memory_store.py`):
- `documents`: one row per page (`doc_id`, app, domain, title, `visits`, `word_count`, `fragments_blob`, `filter_version`).
  `fragments_blob` is the merged filtered text, compressed and encrypted the same way as the raw buffer.
- `episodes`: one row per visit (start, end, `visit_number`, captures, `active_seconds`, key/click/scroll counts,
  `max_score`, `words_added`, `tier`). Unique per `(doc_id, start_ts)`, so processing the same episode twice adds nothing.
- `memory_entries`: the entry for each episode: `entry_text` (readable), `entry_json` (fields), `tier`, `importance`,
  `summary` (empty until the LLM stage exists), `needs_summary`, `text_persisted`, `filter_version`.
  Entries contain **no page text**, only a description.

Example `entry_text` for a high-tier episode:
```
Episode: 20/10/2026 09:00-09:29 (29 min active)
App: chrome - Site: medium.com
Page: "Understanding LightGBM"
Activity: 14 captures, 120 scrolls, 12 key presses - visit 2 of this page
Text: 340 new words this visit; the page has 1250 words in total
Importance: 0.82 (high)
```

### 3.8 History slider (dashboard **History** page)
- The slider runs over every record in `context.db` for one day. For the moment under the thumb it shows the screenshot
  (if screenshots are on and Model 1 saved that moment, or an earlier moment of the same window) and the stored page with
  what was on screen highlighted.
- The highlight comes from `memory.db` table `moments`: one row per capture whose filtered text went into a page, with
  *anchors* (hashes of 8-word runs, `features/text_merge.capture_anchors`), never words. Every time a page is rewritten
  (a new visit, a re-filter) the anchors are looked up again, so the position follows the text as it grows.
- Text only appears after the nightly job: until then a moment's text is in the 3-day raw buffer, which the dashboard
  never opens.
- Screenshots (`storage/screenshot_store.py`, `screenshots.db`) are a separate opt-in with their own consent text.

## 4. Opt-in and consent

- Default: **off** everywhere. The collector setting `screenContent.enabled` is `false`.
- Dashboard tab **Screen text**: a checkbox shows the consent text first. Only a Yes turns it on. The consent is saved with
  a version number and a time; changing the consent text (and bumping `AppSettings.CurrentConsentVersion`) asks everyone again.
- The choice is passed explicitly every time capture starts: collector `--screen-text on|off`, worker `--no-screen-text`
  when off, learning job `--screen-memory` when on. Turning it off also withdraws the consent.
- The tab shows counts and sizes of the buffer and a list of memory entries, never the raw text. **Delete all captured
  text** empties both files.
- Current participant consent covers screenshots and window titles, **not extracted text**. Update the consent form before
  enabling this for any other participant.

## 5. Why the text is unreadable in the database

`raw_captures.text_blob` and `documents.fragments_blob` are compressed and then encrypted with DPAPI. The key is tied to
the Windows login, so only code running as the same user on the same PC can decrypt them. A copied file is useless
elsewhere. That also means database viewers and the dashboard cannot show the text. To read it, run Python as the same
user and use `RawBuffer.iter_unprocessed()` or `MemoryStore.load_document()`. Do not paste or commit the output.

## 6. What each member can plug into

| Member | Plug in here | Where |
|---|---|---|
| 2 (privacy) | Implement `PrivacyFilter.filter(text, ctx) -> FilterResult`; add your class to `FILTERS` and run the contract test | `adaptation/privacy.py`, `tests/test_privacy_contract.py` |
| 3 (retrieval) | Read `memory_entries` and `documents`; send retrieval events back so labels can use them | `storage/memory_store.py`; `retrieval_fn` in `adaptation/label_construction.py` |
| 4 (long-term memory) | Read episodes and entries; decide who writes the summary (`summary` column is empty) | `memory.db` |

Open decisions are listed in section 14 of the plan: who writes summaries, whether OCR is needed for apps that expose no
text, and who may enable this.

## 7. Run it yourself

```powershell
# python-worker\ (from the repo)
.\.venv\Scripts\python.exe -m pytest -q                      # tests, no real screen is read
# the collector needs screenContent on: set "enabled": true in collector.config.json, or pass --screen-text on
# the worker takes --raw-db, --raw-ttl-days, --raw-max-mb, --no-screen-text
.\.venv\Scripts\python.exe -m adaptation.scheduled_job --db context.db --force --screen-memory --dev-passthrough-filter
```
Use the dev flag on your own data only. For the installed app, use the dashboard's Screen text tab.
`packaging\smoke_test_worker.py --screen-text` checks the packaged worker end to end.

## 8. Limits and not built

- **Not built:** Member 2's real filter (only the interface, a dev pass-through and a block-all exist), LLM summaries, text
  embeddings, kept image crops, OCR for apps that expose no text, text features for Model 1.
- Electron apps (VS Code, Teams) expose little through UI Automation, so their text is partial.
- Tier thresholds and the 3-day / 200 MB limits are defaults, not tuned.
- Tested with generated data and the packaged worker; not yet run through a long real-desktop session.
- Nothing leaves the PC. Sharing between machines would need a new, consented design.

## 9. Where to look in the code

| Topic | File |
|---|---|
| Read text on screen | `csharp-collector/src/CsharpCollector/Content/` |
| Message format | `csharp-collector/.../Ipc/SnapshotHeader.cs`, `python-worker/sensing/protocol.py` |
| Raw buffer (encrypted) | `python-worker/storage/raw_buffer.py` |
| Merge | `python-worker/features/text_merge.py` |
| Episodes | `python-worker/adaptation/episodes.py` |
| Privacy boundary | `python-worker/adaptation/privacy.py` |
| Nightly stage and tiers | `python-worker/adaptation/screen_memory.py` |
| Memory store | `python-worker/storage/memory_store.py` |
| Dashboard tab and consent | `dashboard/ContextDashboard/Views/`, `Services/AppSettings.cs`, `Data/ScreenTextDb.cs` |
