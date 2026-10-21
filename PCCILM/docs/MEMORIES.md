# Memories: from saved page text to one readable sentence

What the user sees: the **Memories** page in the dashboard. On the left a timeline of *sessions* grouped by day (title, topic tag,
two-line summary, time, apps); click a session for its TLDR, time per app, key pages and the memories behind it, or click a day
for its recap, time breakdown and what is top of mind.

```
raw_buffer.db   encrypted page text, 7 days (Settings)         (written by the worker, screen text opt-in)
      │  stage 6, adaptation/screen_memory.py: closed episodes only, idle + AC power
      ▼
CAPRLM filter   POST /filter on this PC                 masks emails, ids, salaries, passwords before anything is kept
      ▼
memory.db       documents (filtered text, DPAPI-encrypted), episodes, memory_entries (facts + needs_summary)
      │  stage 7, adaptation/summarizer.py: only entries that kept text
      ▼
local model     GGUF file, llama.cpp, CPU               reads the filtered text, writes 1 to 2 sentences
      ▼
memory_entries.summary + summary_model  →  Memories page
```

## Rules that matter

* **Filter before everything.** Page text is stored only after CAPRLM has seen it (`adaptation/caprlm_client.py`). If the
  service is down, no text is kept in that run (metadata-only entries), never unfiltered text. The client refuses any address
  that is not this PC. When the filter is on it replaces the "keep unredacted" developer option.
* **The model sees filtered text only,** decrypted in memory for one prompt. It writes nothing but `summary` and `summary_model`.
* **The facts stay.** App, site, title and times are stored separately from the summary, so any sentence can be checked, and the
  page shows a sentence built from those facts alone when there is no summary (so the page is never empty of meaning).
* **A bad answer is dropped.** Empty, too short, or a refusal is discarded; that entry keeps its plain sentence. A crashing
  model leaves entries waiting for the next run.
* **Nothing leaves the PC.** The model is a file on disk. Summaries are written only while the PC is idle and plugged in.

## The model

Default: **Qwen2.5-1.5B-Instruct, Q4_K_M** (`qwen2.5-1.5b-instruct-q4_k_m.gguf`, 1.1 GB, Apache-2.0).

| Why | |
|---|---|
| Size | 1.1 GB on disk; measured peak memory 1.9 GB while running; no GPU needed |
| Licence | Apache-2.0, so it can be shipped inside the installer |
| Fit | follows short instructions ("two plain sentences, only from the text") well enough for this job |
| Runtime | llama.cpp's prebuilt `llama-server.exe` (Vulkan build, `packaging/fetch_llama.ps1`), started only while memories are written; the in-process wheel (`requirements-llm.txt`) is the fallback in a dev setup without it |

Measured on this PC (i5-11400H, 6 cores, CPU only): the model loads in about 2 s and writes a memory in about 3 s, so the
default 25 memories per run take about 1.5 minutes. Checked by eye on six made-up pages and one end-to-end run on made-up
text (the filter removed the email, phone and password before storing; the sentence described the page and contained no
secret). That is a smoke test, not an evaluation.

Alternatives if quality is not enough: **Phi-3.5-mini-instruct** (3.8B, MIT licence, about 2.4 GB, roughly 2.5x slower),
or a Qwen2.5-3B/Llama-3.2-3B GGUF (check the licence before shipping those). Any GGUF chat model works: drop it in
`%LOCALAPPDATA%\context-importance\models\llm\` or set `LlmModelPath` in `dashboard.json`.

Small models can state things the page does not say. The prompt forbids it and the facts are kept beside the sentence, but
nobody has measured how often it happens: rate a sample by hand before claiming the summaries are good.

## What is not a memory, and what the model is not asked

Changed 2026-10-24 after reading every sentence, note and recap written so far (222 sentences, 31 notes, 4 recaps):

| Seen | Change |
|---|---|
| 45 "memories" of the Alt+Tab switcher ("Task Switching"), plus Snap Assist, overlays, sign-in brokers | Shell surfaces by app *and* title (`features/surfaces.py`): never stored, never remembered |
| 67 % of episodes under 5 s; A, a glance at B, back to A became "visit 1" and "visit 2" | Glances get no entry; A→glance→A is one episode; time on a page runs until the next window |
| "You visited page 32" (from "visit number 32" in the prompt) | The prompt has no visit number and no browser/profile suffix in the title |
| Invented content on near-empty pages | No model call under 40 real words: the entry keeps its fact-built sentence |
| "You: 1. Which ContextDashboard is the base?" (page text pasted back) | A sentence must start "You <verb>" and must not copy 10 words in a row from the page |
| "C# project" became "C project" | Only Markdown marks are removed; `#` inside a word stays |
| Session notes listed apps and minutes; every day recap copied one session note | Pages ranked by time, with an excerpt when they have no sentence; the prompt asks for the subject; a recap that copies a note is replaced by one built from the session titles |

Old entries of the first two kinds are hidden, not deleted (`adaptation/memory_tidy.py`, `MemoryStore.unhide_all`). Notes
carry `note_version`; notes from an older version are rewritten a few per run (`rollups.NOTES_VERSION`).

## Where it runs: GPU when it can, CPU otherwise

`adaptation/llm_runtime.py` picks the device every time the model starts:

- **On AC with a dedicated GPU** (NVIDIA, AMD Radeon RX, Intel Arc A/B) whose free memory holds the model: the GPU, through
  Vulkan, so no CUDA install is needed. Integrated graphics (Intel UHD/Iris, AMD "Radeon Graphics") are skipped.
- **On battery, or no usable GPU** (gaming laptops often switch the dedicated GPU off when unplugged): the CPU.
- **The GPU fails** to start or mid-run: restarted on the CPU and the request retried.
- It runs at low priority on a third of the cores. The background job starts it only when the PC is idle, on AC, and
  lightly loaded (CPU under 35 %, enough free RAM), and **pauses** it when the user is back or the PC is unplugged. The
  server is stopped after every run, so nothing stays in RAM or VRAM in between.

Measured 2026-10-24 on the author's laptop (i5-11400H, RTX 2050 4 GB), Qwen2.5-1.5B Q4_K_M, made-up pages of about
1,500 tokens, 5 memories per start:

| Device | Start | Per memory |
|---|---|---|
| RTX 2050 (Vulkan) | ~5 s | ~1.4 s (the first after a start up to ~9 s) |
| CPU, 4 threads, low priority | ~4 s | ~40 s |

The CPU figure is much slower than the earlier "about 3 s" above: that was all cores, in-process, on shorter pages.

## Running it

```powershell
# python-worker/  (repo mode)
pip install -r requirements-llm.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
python -m adaptation.scheduled_job --db %LOCALAPPDATA%\context-importance\context.db --force --no-require-ac `
    --screen-memory --privacy-url http://127.0.0.1:8787 --summarize --llm-model <path to .gguf>
```

The dashboard's *Run learning now* passes these options itself when Screen text is on, the CAPRLM service is running and a
model file exists. It asks the job for `--help` first and leaves out any option an older `adapt.exe` does not know, so
rebuild the worker bundle (`packaging/build_bundle.ps1`) for the installed app to get them.

## Sessions and days (stage 8)

* A **session** is a run of important (high or medium) entries with no gap over 30 minutes and no more than 60 minutes from its
  first entry to the start of its last. Without the 60-minute cap a busy day became one session of hundreds of entries.
  Low-importance entries are not sessions but still count toward the day's time.
* The dashboard groups entries itself (`UI/ContextDashboard/Data/Sessions.cs`), so the page works with no model at all. The
  learning job (`adaptation/rollups.py`) writes a title, tag and TLDR per session and a recap per day into `memory.db`
  (`session_notes`, `day_notes`), found again by a key: the first 16 hex characters of SHA-1 over the session's entry ids,
  sorted and joined by commas. The grouping rule and the key exist in both languages; `tests/test_rollups.py` and
  `SessionBuilderTests.cs` assert the same keys for the same example, so a mismatch fails a test.
* A session still open (last entry under 30 minutes ago) gets no note yet, and a session that grows gets a new key and a new note.
  A day recap is shown only if it was written from exactly the sessions the dashboard now sees, so today's recap appears after the
  day's last session has closed.
* **What is computed and what is written.** Time per app, key pages, top of mind and the "based on N memories, M with page text"
  line come from stored numbers. The model writes only the words (title, tag, TLDR, recap), always labelled with its name; the
  page says "built from the stored facts" where there is no model note.
* The model sees only the entries' sentences and page titles, not page text.

## Cleaning text stored before the filter existed

Text kept by the unredacted developer option (`filter_version` starting `passthrough`) is never read by the summary model. To clean
it, run once (the dashboard starts the CAPRLM service; any port works):

```powershell
python refilter_memory.py --memory-db %LOCALAPPDATA%\context-importance\memory.db --privacy-url http://127.0.0.1:8787          # report
python refilter_memory.py --memory-db ... --privacy-url ... --apply                                                             # backs up first
```

Each page is re-run through the CAPRLM filter fragment by fragment and replaced; a page the filter blocks loses its text; a page the
filter fails on is left exactly as it was. 2026-10-21 on the author's data: 83 pages, all converted, 3.2% of the words masked or removed.

`memory_llm.py` runs stages 7 and 8 alone on `memory.db` (no learning cycle, nothing trained or deployed):
`python memory_llm.py --memory-db ... --llm-model <gguf> --max-entries 40`. On long pages a sentence takes about 15 s
(the model reads up to 6,000 characters), so 40 entries took 11 minutes.

## Scope decision: summaries stay on this PC

Decided 2026-10-21: the model's sentences, session notes and day recaps are for **local use only**. So they are not run through
the privacy filter a second time (it would catch almost nothing the first pass missed and turns dates into placeholders), and the
`summary`, `session_notes` and `day_notes` columns in `memory.db` are stored as plain text (page text itself is DPAPI-encrypted).
If this ever changes (another member reads `memory.db`, or a sentence goes to an outside LLM), the boundary needs its own step:
mask what leaves with the CAPRLM filter ("mask before send"), and consider encrypting those three columns at rest.

## What the model is shown: page furniture is trimmed first

`adaptation/page_text.py` cleans a stored page just before it goes to the summary model; the stored page itself is not changed.
It drops lines with no letters or digits (icon glyphs, bullets), lines a whole site repeats on most of its pages (navigation, footers),
a repeated short label after its first appearance, and any run of five or more short lines (three words or fewer, no sentence
punctuation: menu bars, tab strips, toolbars). A heading between sentences stays. If cleaning would leave under 15% of a page, the
model reads the original instead (file listings and one-line chats are mostly short items). Switch it off with
`SummarizeConfig(clean_pages=False)`.

Measured on the author's 99 stored pages (2026-10-21): 20% of the characters and 58% of the lines were removed; the typical page lost
11%, the 90th percentile 43%. Most stored text is desktop apps (editor panels, chat apps), not websites; only about 2% of the
characters were navigation repeated across a site's pages. The collector already skips real window chrome (menus, toolbars, tab bars)
at the source; the menus of a website sit inside the page, where they look like ordinary text, which is why this second pass exists.
