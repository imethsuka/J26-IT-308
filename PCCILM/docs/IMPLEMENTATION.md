# Desktop Context-Importance System — Implementation Documentation

**Scope:** Member 1 — *adaptive desktop context acquisition and importance learning*.
This document describes everything built, component by component, how the pieces connect, how to
run and reproduce them, and the design decisions behind them.

Out of scope (other members): privacy / tokenization, and retrieval. Where this system needs those,
it defines an interface boundary and stops there (e.g. the retrieval signal in label construction is
an injected callback, never implemented here).

---

## 1. What the system does

The system watches desktop activity, decides which on-screen *contexts* are important enough to keep,
and continuously **learns** what "important" means for the user from their later behaviour — with no
manual labelling.

It runs as **two cooperating local processes**:

```
┌───────────────────────────────┐                      ┌────────────────────────────────────┐
│  csharp-collector (.NET 8)    │                      │  python-worker (Python 3.11+)      │
│                               │   local IPC:         │                                    │
│  • Win32 hooks (kbd/mouse/    │   named pipe (pref.) │  • sensing/    receive + decode    │
│    foreground window/title)   │   or localhost TCP   │  • features/   MobileCLIP + feats  │
│  • Idle detection             │ ───────────────────▶ │  • models/     baselines + LightGBM│
│  • Screen-change capture      │  length-prefixed     │  • storage/    SQLite persistence  │
│  • Exclusion filtering        │  JSON header +       │  • adaptation/ labels, prototypes, │
│  • IPC client                 │  raw frame bytes     │                drift, deploy, job   │
│                               │                      │  • eval/       benchmarks + compare │
└───────────────────────────────┘                      └────────────────────────────────────┘
```

- The **C# collector** owns everything needing native Windows access. It senses the foreground
  context (app / title / URL), counts input activity (never content), detects idle, and captures a
  screen frame only when the screen changes materially. Everything an exclusion list matches is
  dropped *before* any title/URL/pixels are read.
- The **Python worker** owns all ML. It receives each context snapshot, turns it into features
  (including a MobileCLIP image embedding summarized into a couple of scalars), stores it, and runs
  the learning loop: construct labels from later behaviour, learn importance prototypes, retrain a
  classifier, watch for drift, and deploy a new model only when it is measurably better.

**The end-to-end learning loop is fully implemented and tested**, from raw Win32 events all the way
to a validated model deployment with rollback, plus an evaluation harness that compares the models.

---

## 2. End-to-end data flow

```
 Win32 events ─► exclusion filter ─► WindowContextProcessor ─► CollectorState
      │                                                             │
 input hooks ─► ActivityCounter (counts only)                      │  every captured frame
 GetLastInputInfo ─► IdleMonitor                                   ▼
 screen ─► ScreenChangeDetector ─► FrameBuffer ─────► SnapshotPublisher ─► WorkerConnection
                                                                             │  (named pipe / TCP)
 ───────────────────────────────────────────────────────────────────────────┼──────────────────────
                                                                             ▼
 listener.py ─► codec/protocol ─► Snapshot ─► feature_assembly.assemble_features ─► feature dict
                                                     │ (+ MobileCLIP embedding, prototypes, novelty)
                                                     ▼
                                         storage: context_records (features JSON, embedding BLOB,
                                                  decision, label=NULL, timestamps)
                                                     │
        ┌────────────────────────────────────────────┘  (48h later, on a scheduled, idle+AC run)
        ▼
 label_construction ─► prototype_learning ─► ImportanceModel.train ─► drift check ─► validate_and_maybe_deploy
   (revisit/continue/                          (LightGBM)              (ADWIN)         (deploy iff strictly
    retrieval → label)                                                                  better; else keep)
                                                                                          │
                                                                          model_versions (history, rollback)
```

---

## 3. C# collector (`csharp-collector/`)

.NET 8 console app, target `net8.0-windows`. Layout: app in `src/CsharpCollector/`, tests in
`tests/CsharpCollector.Tests/`, benchmark in `benchmarks/CsharpCollector.Benchmarks/`, tied together
by `CsharpCollector.sln`.

### 3.1 Desktop sensing

| Area | Files | What it does |
|---|---|---|
| **Win32 interop** | `Native/NativeMethods.cs` | All P/Invoke declarations. The file header documents, per API, which need elevation and which trigger antivirus false positives (see §3.5). |
| **Foreground / title hooks** | `Hooks/ForegroundWindowHooks.cs`, `Hooks/HookThread.cs` | `SetWinEventHook` for `EVENT_SYSTEM_FOREGROUND` and `EVENT_OBJECT_NAMECHANGE` (title changes), on a dedicated message-loop thread. Callbacks only raise a "something changed" signal — they read nothing, so the exclusion check always runs first. |
| **Input activity** | `Hooks/InputActivityHooks.cs`, `Sensing/ActivityCounter.cs` | Global low-level `WH_KEYBOARD_LL` / `WH_MOUSE_LL` hooks that **count** keypresses / clicks / scrolls / moves in a rolling window. Key codes and cursor coordinates are deliberately never dereferenced. |
| **Idle** | `Sensing/IdleMonitor.cs` | `GetLastInputInfo` polling; emits `idle_start` / `idle_end` around a configurable threshold (wrap-safe tick maths). |
| **Window context** | `Sensing/WindowContextProcessor.cs`, `Win32WindowInfoSource.cs`, `WindowSignal.cs` | Turns a raw signal into an event in a fixed privacy order: process name → app-exclusion → (browsers only) URL → domain-exclusion → title → emit. Dedups unchanged contexts. |
| **Browser URL** | `Browser/UiaBrowserUrlReader.cs` | Reads the Edge/Chrome address bar via UI Automation, off the hook thread. Skips page `Document` nodes (so a page's own text field can't be mistaken for the address bar) and returns nothing while the bar has focus (you're typing). |
| **Exclusion filter** | `Filtering/ExclusionFilter.cs` | App-name and URL-domain exclusion. **Fails closed**: an unidentifiable process, or a browser URL that can't be read while domain rules exist, is treated as excluded. Domain match covers subdomains; app names normalize `.exe`/paths/case. |
| **Context gate** | `Sensing/ContextGate.cs` | Sequence-number gate so input is counted / screen captured only while the *evaluated-and-allowed* context is in front. Between a window switch and its evaluation, everything is blocked — an excluded app is never sensed even briefly. |

Events are emitted as **JSON Lines to stdout** (diagnostics to stderr) by
`Output/JsonStdoutSink.cs`. Config is `collector.config.json` (`Config/CollectorConfig.cs`); the app
refuses to start if it is missing, rather than run with no exclusions.

### 3.2 Screen-change capture

Capture is expensive, so it only happens when the screen actually changes:

- `Capture/GdiScreenCapturer.cs` — GDI `BitBlt` of the primary monitor into a cached DIB, copied to a
  reused managed buffer. DPI-aware (`Capture/DpiAwareness.cs`).
- `Capture/Thumbnail.cs` — downscales a frame to a **64×36 greyscale** thumbnail (box-filter, BT.601
  luma) and computes **mean absolute difference** between thumbnails.
- `Capture/ScreenChangeDetector.cs` — compares the current thumbnail to the last *accepted* one; a
  slow drift still triggers once it accumulates past the threshold.
- `Capture/ScreenChangeMonitor.cs` — the probe loop: exclusion check → grab → thumbnail → diff →
  (on change) re-check exclusion → store the frame.
- `Capture/FrameBuffer.cs` — in-memory buffer, **single-slot by default** (holds only the latest
  frame), configurable capacity; **never written to disk**.

**Measured design change:** a GDI scale-to-thumbnail was found to cost the same as a full-screen
grab (both dominated by DWM GPU→CPU readback). So each probe grabs the full frame once and shrinks
it in managed code, and a trigger keeps *that same frame* instead of capturing twice. This halved
the trigger-path cost. Benchmark on the dev machine (i5-11400H, 1920×1080, Release, 200 samples):

| Stage | p50 | p95 |
|---|---|---|
| Every probe (grab + thumbnail + diff) | 31.3 ms | 41.8 ms |
| Probe that triggers a capture (+ store) | 34.1 ms | 47.0 ms |

Reproduce: `dotnet run -c Release --project benchmarks/CsharpCollector.Benchmarks`. Report saved to
`csharp-collector/benchmark-results/`.

### 3.3 IPC to the Python worker

- `Ipc/SnapshotHeader.cs` — the `context_snapshot` message schema (v1).
- `Ipc/WireFormat.cs` — framing: `"CIS1"` magic + `header_length` + `payload_length` + JSON header +
  raw BGRA frame bytes, all little-endian.
- `Ipc/WorkerConnection.cs` — client; sends from a bounded background queue (drops the oldest frame
  if the worker stalls), keeps one connection open across messages, reconnects on drop.
- `Ipc/SnapshotPublisher.cs` — builds one snapshot per captured frame from a `CollectorState`
  (`Sensing/CollectorState.cs`) that observes the event stream, so the snapshot is assembled from
  exactly what was emitted.

**Format choice (JSON header + raw bytes, not protobuf, not base64):** the metadata is small and
benefits from being human-readable with no schema compiler in either language; the frame is ~8 MB,
and base64-in-JSON would inflate it ~33% plus an encode/decode pass per frame. Raw bytes after the
JSON header get protobuf-like payload efficiency with JSON convenience.

### 3.4 What the collector emits

```json
{"type":"foreground","ts":"…","process":"chrome","title":"Docs","url":"docs.example.com/page"}
{"type":"title_change","ts":"…","process":"Code","title":"main.cs"}
{"type":"excluded_context","ts":"…"}
{"type":"activity","ts":"…","window_seconds":10,"key_presses":42,"mouse_clicks":3,"mouse_scrolls":7,"mouse_moves":310}
{"type":"idle_start","ts":"…","idle_since":"…"}   /   {"type":"idle_end","ts":"…","idle_seconds":184.2}
{"type":"frame_captured","ts":"…","width":1920,"height":1080,"diff":6.98}
```

The IPC `context_snapshot` (one per captured frame) bundles the frame metadata + raw pixels with the
current context (excluded flag, app/title/domain, dwell seconds), the rolling activity counts, and
the idle state.

### 3.5 Permission & antivirus notes (documented in `Native/NativeMethods.cs`)

- **Nothing needs administrator rights**, but the collector must run in the interactive user session
  (not a Session-0 service).
- **Highest AV risk:** the global `WH_KEYBOARD_LL` hook — the textbook keylogger technique — flagged
  by heuristic engines on unsigned binaries whatever the callback does. Mitigation: code-sign and
  submit for allow-listing. The callback never reads key codes.
- **Medium risk:** GDI screen capture and reading browser URLs via UI Automation. Periodic
  screenshots + a keyboard hook in the same unsigned process score higher together than apart.
- **UIPI:** without elevation, input to elevated windows isn't seen; an elevated browser's URL can't
  be read.

---

## 4. Python worker (`python-worker/`)

Python 3.11 target (developed/tested on 3.13). `main.py` starts the IPC listener. Dependencies in
`requirements.txt` (numpy, pandas, scikit-learn, lightgbm, river, torch, pillow); `requirements-dev.txt`
adds pytest. MobileCLIP is installed separately (not on PyPI).

### 4.1 `sensing/` — IPC listener + protocol

- `protocol.py` — the wire protocol mirror of the C# side: framing constants, the `Snapshot` /
  `FrameInfo` dataclasses, header parsing, size guards.
- `codec.py` — `read_message` (receive) and `encode_message` (mirror send, used by tests).
- `listener.py` — the server. Two transports: `TcpSnapshotListener` (stdlib) and
  `NamedPipeSnapshotListener` (Windows named pipe via ctypes — no third-party dependency). Each
  decoded snapshot is handed to a pluggable handler; the default (`log_snapshot`) just logs it
  (never pixels), used directly only by the listener's own tests.
- `pipeline.py` (worker root) — `SnapshotPipeline`, the handler `main.py` actually installs. Skips
  excluded snapshots; otherwise embeds the frame (unless `--no-embedding`), calls
  `assemble_features` against the currently loaded prototypes and novelty ring, scores with
  `load_active_scorer`'s result (the active deployed LightGBM version if one exists, else the
  rule-based baseline), and inserts a `ContextRecord` — closing the loop from raw snapshot to a
  labelled-later row in `context.db`. Calls are serialized on a lock (the TCP listener runs one
  thread per connection; the DB connection is opened with `check_same_thread=False`). If the
  encoder can't be loaded, embedding is switched off for the run after one warning. Every
  non-excluded snapshot is stored whatever the decision, because labelling and retraining need the
  discarded contexts too; the decision is recorded, not enforced.
- `main.py` serves on a background thread and waits on the main thread, so Ctrl+C works even while
  the named-pipe listener is blocked waiting for the collector. The active model is loaded at
  startup; restart the worker after a deploy.

The worker is the IPC **server**; start it first, then the collector connects.

### 4.2 `features/` — embeddings + feature assembly

- `mobileclip_encoder.py` — loads **MobileCLIP-S0, image encoder only** (text tower dropped, weights
  frozen). `embed_frame(image) -> 512-d L2-normalized float32 vector`. Accepts a PIL image or a raw
  BGRA frame array.
- `feature_assembly.py` — `assemble_features(sensing_event, embedding, recent_context_history, *,
  prototypes=None, saved_embeddings=None) -> dict`. Produces a flat feature dict in four groups:
  - **behavioural** (`beh_*`): input counts, interactions/sec, app-switch count + rate, distinct apps.
  - **temporal** (`tmp_*`): dwell seconds, hour-of-day + sin/cos, weekend/idle flags, time since last save.
  - **contextual** (`ctx_*`): app, domain, title tokens, and revisit-so-far (`seen_before`, count, ratio),
    counted in *visits*: consecutive captures of the same context with no gap over 30 min are one
    visit, and the visit in progress is not a revisit. (The collector sends a snapshot per screen
    change, so counting records would mark nearly every capture "seen before".)
  - **visual** (`vis_*`): cosine similarity to the nearest importance **prototype**, and a **novelty**
    score against a small in-memory ring of recently-saved embeddings.
  - The raw 512-d embedding is **never** in the output — it is summarized into the similarity/novelty
    scalars. `SavedEmbeddingRing` is the caller-owned novelty ring (read-only here).

**MobileCLIP-S0 resource benchmark** (`eval/measure_encoder.py`, CPU, 1080p input, 100 calls,
dev machine): median **107 ms**, p95 **128 ms** per embedding; peak RSS **~1.25 GB** with the model
loaded (dominated by the torch import + checkpoint load; settles ~866 MB after first inference).

### 4.3 `storage/` — SQLite persistence

- `schema.py` — DDL + ordered migrations. `context_records` holds features (JSON), the 512-d
  embedding (float32 BLOB), the model's `decision` (save/discard), the later ground-truth `label`
  (NULL until labelled), and timestamps; `model_versions` holds model history. Timestamps are
  INTEGER microseconds UTC.
- **Key index:** `ix_context_unlabelled` — a *partial* index `ON context_records(ts) WHERE label IS
  NULL` — serves the label-construction query `label IS NULL AND ts < cutoff` and stays small
  because it only indexes unlabelled rows. (Verified via `EXPLAIN QUERY PLAN`.)
- `database.py` — connection (WAL), migration runner, embedding pack/unpack, epoch-µs helpers.
- `context_store.py` — CRUD: `insert_context_record`, `query_unlabelled_older_than`, `set_label`,
  model-version insert/activate/lookup, `list_context_records`.
- `migrate.py` — CLI to apply migrations.
- `report_storage.py` — CLI reporting DB size + row counts (the **storage-overhead metric**). On 500
  seeded records: ~2 MB total, ~2 KB/record, dominated by the embedding BLOB.

Run: `python -m storage.migrate --db context.db`, `python -m storage.report_storage --db context.db`.

### 4.4 `models/` — importance scorers (one shared interface)

All scorers implement `base.ImportanceScorer` (`predict_proba(features) -> float`, `predict(...) ->
int`), so they are interchangeable in evaluation and deployment.

- `rule_based.py` — **interpretable baseline**. Three rules (dwell-time ramp, revisit flag,
  app-category weight) combined as weighted evidence in log-odds, squashed to a real probability.
  `explain()` returns the per-rule breakdown. Deliberately a *fair* baseline, not a strawman
  (smooth ramp; browsers neutral; every constant configurable).
- `static_threshold.py` — **naive baseline**: save iff one chosen feature is past a fixed threshold
  (default dwell ≥ 30s). No training, no feature combination.
- `vectorizer.py` — pins the fixed numeric feature layout (`FEATURE_COLUMNS`) shared by training and
  inference: numeric/flag passthrough + one-hot app category. Free-text IDs are intentionally
  dropped. Saved with each model version and validated on load.
- `lightgbm_model.py` — **Model 1**. Same interface plus `train(labelled_dataframe)`,
  feature-importance reporting (`feature_importances`/`format_importances`), and **append-only**
  `save(version_id)` / `load(version_id)` under `models/versions/<id>/` (never overwrites — keeps
  rollback history). Booster persisted as portable `model.txt` + `metadata.json`.

### 4.5 `adaptation/` — the learning loop

- `label_construction.py` — **delayed weak-supervision labels**. For records older than `label_delay`
  (default 48h) and unlabelled, computes three signals: **revisit** (the user left the context and
  came back to it in a later *visit*; repeated captures within one visit don't count),
  **continuation** (task resumed in a *later session*, split by an inactivity gap), and **retrieval**
  (an injected callback — Member 3's component, mocked here, never implemented). They merge via
  `combine_importance_label` — a single, isolated, documented function so the rule can be cited and
  defended. Retrieval is the only signal that can be *missing*; when missing, its weight drops out of
  both numerator and denominator, so missing feedback is neither rewarded nor penalised.
  Both behavioural signals look only within a bounded 48h horizon, and a record whose horizon isn't
  fully covered by data yet is left unlabelled (censored), so labels never change once written.
  Each label is stamped with `label_version` (rule version + horizon, currently `v3/h48`).
  - *Consequence of the rule:* a return in a later session is also a return, so continuation
    implies revisit. While retrieval is missing, revisit alone scores 0.6, so the label equals the
    revisit flag ("came back within 48h") and continuation only raises the score to 1.0. Once
    retrieval feedback exists, revisit alone (0.3) is no longer enough.
- `prototype_learning.py` — **importance prototypes** via spherical KMeans over the embeddings of
  important contexts; returns unit-norm centroids that feed `assemble_features(..., prototypes=…)`.
  `save_prototypes`/`load_prototypes` persist them per model version (append-only).
- `drift_detection.py` — **`ErrorDriftDetector`**: wraps River's ADWIN over Model 1's error stream
  (`detect(error) -> bool`). Concept drift; needs labels.
- `input_monitor.py` — **`InputDistributionMonitor`**: label-free covariate-drift monitor tracking
  rolling app-mix (total-variation distance) and mean prototype-similarity against a frozen
  reference. Runs continuously.
  - *These two are deliberately separate components* (concept vs covariate drift). Both only *detect*
    drift; neither schedules or performs retraining, and nothing here is named "retrain".
- `deployment.py` — **validation gate**. `validate_and_maybe_deploy(candidate, current, held_out)`
  computes precision/recall/F1/accuracy for both models, **deploys the candidate only if strictly
  better** on the primary metric (ties keep current), logs the full comparison to `model_versions`,
  and `rollback_to(version)` re-activates any prior version. At cold start the scheduled job passes
  the rule-based baseline as "current", since that is what the worker serves until a model is
  deployed, so the first model must beat it too.
- `host_state.py` — **idle + AC-power gate**. Real Windows implementations
  (`GetLastInputInfo` / `GetSystemPowerStatus` via ctypes, no dependency), injectable for tests.
  Non-Windows falls back to a clearly-marked `TODO(cross-platform)` and expects injected providers.
- `scheduled_job.py` — **the scheduled job** that runs the whole pipeline in order — label
  construction → prototype learning → retrain → drift check → validation gate — **gated on the host
  being idle and on AC power**. One-shot (Task Scheduler) or `--loop` daemon. Emits a structured JSON
  run summary (duration, contexts labelled, drift, deploy/keep decision). Skips gracefully (not
  enough data / single class / everything still censored, with the data span in the reason) and
  captures errors instead of crashing. It also skips the retrain when nothing was labelled since
  the last candidate, so `--loop` doesn't save an identical new version every poll
  (`--retrain-without-new-labels` overrides).

Run: `python -m adaptation.scheduled_job --db context.db [--loop] [--force] [--summary-log runs.jsonl]`.

### 4.6 `eval/` — benchmarks + comparison

- `measure_encoder.py` (+ `resource_probe.py`) — standalone MobileCLIP-S0 latency + peak-RSS
  measurement, appends a CSV row per run (native OS memory reads; no profiler dependency).
- `model_comparison.py` — **the results-chapter table**. Given the labelled store, computes for the
  rule-based baseline, static-threshold baseline, Model 1, and a save-everything reference:
  precision/recall/F1, *projected* storage reduction vs saving everything (the worker stores every
  context today; this is what dropping discards would save), relevant-context retention, and
  mean/p95 decision latency (live-timed). Writes CSV + prints a markdown table. **Everything is
  computed from the provided data**; nothing is hardcoded. Model 1 is trained on a temporal split and
  evaluated on held-out rows (no leakage), or loaded via `--model-version`.

Run: `python -m eval.model_comparison --db context.db [--test-fraction 0.3] [--model-version V] [--csv out.csv]`.

> The comparison table's *numbers* depend entirely on your data. Runs against synthetic seed data
> during development are illustrative only — do not paste development numbers into the results chapter;
> run it against your real labelled store.

---

## 5. How to run / reproduce

### C# collector
```powershell
cd csharp-collector
dotnet build
dotnet test                                   # 94 test cases
dotnet run --project src/CsharpCollector       # senses + emits JSON Lines; connects to the worker
dotnet run -c Release --project benchmarks/CsharpCollector.Benchmarks   # capture latency
```
Run in the interactive user session, without admin rights.

### Python worker
```powershell
cd python-worker
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt               # + install MobileCLIP separately; set MOBILECLIP_S0_CHECKPOINT
pip install -r requirements-dev.txt           # pytest, for the test suite
python -m pytest -q                            # 268 tests

python main.py --transport namedpipe           # start the IPC listener (server) first
python -m storage.migrate --db context.db      # create/upgrade the DB
python -m adaptation.scheduled_job --db context.db --force   # run one adaptation cycle
python -m eval.model_comparison --db context.db              # produce the comparison table
```

### Two-process smoke test
Start `python main.py` (worker/server), then `dotnet run --project src/CsharpCollector` (collector/
client). The worker logs one `snapshot …` line per captured frame, carrying the frame bytes, app,
dwell, and diff. Verified end-to-end over a real Windows named pipe.

---

## 6. Test coverage

**Python — 268 tests** (all passing), `python-worker/tests/`:

| Suite | Tests | Covers |
|---|---|---|
| `test_ipc_integration.py` + `test_named_pipe.py` | 10 | framing/codec round-trips over real TCP and a real named pipe; malformed messages |
| `test_pipeline.py` | 9 | snapshot → stored record, TCP listener threads, encoder-failure handling, Ctrl+C shutdown |
| `test_encoder_inputs.py` | 7 | BGRA/RGB handling, input validation, the OS memory probe (no torch needed) |
| `test_feature_assembly.py` | 24 | each feature group; visit-based revisit + novelty edges (first-ever, exact repeat, novel) |
| `test_storage.py` | 20 | schema/migrations (incl. atomicity), CRUD round-trips, the partial-index query plan, the report |
| `test_rule_based_scorer.py` | 25 | each rule branch + combinations + config |
| `test_lightgbm_model.py` | 10 | features→train→predict pipeline, append-only save, bit-exact load round-trip |
| `test_label_construction.py` | 46 | each signal, visits, horizon, censoring, label stability, the combination rule |
| `test_prototype_learning.py` | 13 | KMeans recovery of synthetic clusters, per-version persistence |
| `test_drift_detection.py` | 10 | ADWIN no-drift vs regime-change detection |
| `test_input_monitor.py` | 11 | app-mix + similarity shift detection, readiness gating |
| `test_deployment.py` | 17 | deploy/keep branches (mocked metrics), metric maths, rollback |
| `test_scheduled_job.py` | 18 | host gating, the ordered five-stage cycle, cold-start baseline gate, skip + error branches, summary logging |
| `test_model_comparison.py` | 16 | metric correctness vs hand-computed values, storage/retention/latency, output |

**C# — 94 test cases** (63 methods; `[Theory]` cases expand), `csharp-collector/tests/`: exclusion
filter, window-context privacy ordering, context gate, thumbnail/diff maths, screen-change detector,
frame buffer, and IPC wire-format / snapshot building.

---

## 7. Design decisions & rationale (for the report)

- **Exclusion runs first, fails closed.** Nothing an exclusion rule matches has its title, URL, or
  pixels read; anything unidentifiable is treated as excluded. Privacy by construction.
- **Counts, never content.** Input hooks count events; key codes and cursor positions are never read.
- **Capture only on change**, single frame in memory, never on disk — minimises footprint and risk.
- **JSON header + raw frame bytes** over protobuf/base64 — readable metadata, efficient payload.
- **The 512-d embedding is summarized, never fed raw** into the model — two interpretable scalars
  (prototype similarity, novelty) instead.
- **Labels are delayed weak supervision** (48h), combined by one citable function; retrieval is an
  injected boundary to Member 3.
- **Spherical KMeans** for prototypes because the visual feature is cosine similarity on unit vectors.
- **Concept drift and covariate drift are separate components**, never conflated with retrain policy.
- **Deployment is strictly-better-gated with full history + rollback**; ties keep the incumbent, and
  at cold start the incumbent is the rule-based baseline the worker is serving.
- **The scheduled job gates on idle + AC**, skips gracefully, and never crashes the scheduler.
- **Evaluation computes everything from data** — no hardcoded results; Model 1 evaluated only on
  held-out rows.

---

## 8. Known limitations / future work

- **The save decision is recorded, not enforced.** Model 1's `save`/`discard` is stored on each
  record, but nothing acts on it: no screenshot is written to disk on `save`, and `discard` still
  stores the record (needed for labelling). The "Capture to SSD → Screenshot folder" branch of the
  architecture diagram does not exist yet. Storage reduction in `eval.model_comparison` is projected.
- **The decision is made at the moment of capture**, so dwell time is "time so far", not the full
  visit. Deferring the decision until the user leaves the context (frame held in RAM) is a possible
  refinement.
- **MobileCLIP memory**: ~1.25 GB peak RSS under torch. On an 8 GB machine, an ONNX Runtime export
  of the image encoder is the first optimisation to try.
- **Primary monitor only** for capture; an excluded app that is visible but not foreground can still
  appear in a frame (the exclusion check is on the foreground window).
- **Full URLs incl. query strings** are emitted — redaction belongs to the privacy phase (out of
  scope here).
- **Retrain uses features as captured.** Newly-learned prototypes are bundled with the new model
  version for *future* captures; visual features are not recomputed from stored embeddings against
  fresh prototypes (a possible refinement).
- **Label construction reads the full record set** to build the revisit/continuation timeline; the
  first thing to window if the table grows large.
- **Input-distribution monitor** is built for continuous runtime use; it is not wired into the batch
  job (which uses the label-based error-drift check).
- **Cross-platform idle/AC** is Windows-only; non-Windows needs injected providers (marked TODO).
- **MobileCLIP** must be installed separately and is not import-tested in CI (the ~216 MB weights and
  torch stack are excluded from the light test path).

---

## 9. File index

```
context-importance-system/
├── README.md, CONTRIBUTING.md, IMPLEMENTATION.md, HOW_TO_RUN.md, CLAUDE.md, .gitignore
├── Deep-down Member 1 Architecture.drawio.png    conceptual diagram (mapped in §10)
├── csharp-collector/                 .NET 8 collector
│   ├── src/CsharpCollector/          Hooks/ Capture/ Sensing/ Browser/ Filtering/ Ipc/ Native/ Output/ Config/ Events/
│   ├── tests/CsharpCollector.Tests/  94 test cases
│   └── benchmarks/…                  capture-latency benchmark
└── python-worker/                    Python ML worker
    ├── main.py, pipeline.py          entry point + snapshot -> features -> scoring -> storage
    ├── sensing/    protocol, codec, listener (named pipe / TCP)
    ├── features/   mobileclip_encoder, feature_assembly
    ├── models/     base, rule_based, static_threshold, vectorizer, lightgbm_model  (+ versions/)
    ├── storage/    schema, database, context_store, migrate, report_storage
    ├── adaptation/ label_construction, prototype_learning, drift_detection, input_monitor,
    │               deployment, host_state, scheduled_job
    ├── eval/       measure_encoder, resource_probe, model_comparison
    └── tests/      268 tests
```

---

## 10. Architecture diagram vs implementation

`Deep-down Member 1 Architecture.drawio.png` is the conceptual model. It maps onto the code as
follows, with four places where the implementation deliberately differs.

| Diagram element | Implementation |
|---|---|
| Window title, mouse, keyboard, "time user stayed" | Collector sensing (§3.1), turned into `ctx_*`, `beh_*`, `tmp_*` features (§4.2) |
| MobileCLIP (the "eyes") | Frozen MobileCLIP-S0 image encoder → 512-d vector → `vis_*` similarity/novelty scalars |
| Model 1: "decide whether to capture or not" | `models/lightgbm_model.py`; rule-based baseline at cold start |
| Activity data saved to the DB | `context_records`, every non-excluded snapshot |
| Model 2: "learn user behaviour slowly over a long time" | The scheduled adaptation job (§4.5) |
| Model 2 → Model 1: "adjust model decision making" | Retrain + strictly-better deploy gate |
| Model 2 → MobileCLIP: "adjust vector embedding" | Prototype learning (see 3 below) |
| "Capture to SSD" → Screenshot folder | Not implemented (§8) |

**1. "Capture or not" is really "keep or discard".** Model 1 consumes the MobileCLIP embedding, which
needs the screenshot, so the frame must already exist in memory when Model 1 runs. The real flow is
a three-stage funnel, and the first stage is not drawn in the diagram:

```
cheap screen-change check (64×36 thumbnail diff, ~31 ms)  →  MobileCLIP + Model 1  →  keep / discard
```

**2. Model 2 is a learning pipeline, not a neural network.** It turns later behaviour (revisit,
continuation, retrieval) into labels, learns prototypes, retrains Model 1, checks drift, and deploys
only if strictly better. This is cheap enough to run locally (seconds, gated on idle + AC),
interpretable, and appropriate for the small amount of per-user data. A neural "behaviour model"
would add cost without a clear benefit at this data scale.

**3. MobileCLIP is never retrained.** Fine-tuning a vision encoder on-device from weak labels would
be slow and unstable. Instead, Model 2 learns *importance prototypes*: cluster centres, in
MobileCLIP's embedding space, of contexts that turned out to be important. Model 1 then measures how
close a new screen is to them. The "adjust vector embedding" arrow therefore points at Model 1's
prototype input, not at the encoder.

**4. MobileCLIP gives a visual gist, not a description.** The image encoder outputs a fingerprint of
the screen's overall appearance (code editor vs video vs document). The frame is resized to about
256×256, so text is unreadable, and the text tower is dropped, so it cannot name what it sees. What
is on screen comes mainly from the window title, app, and URL. Describing screens in words would need
OCR or a small vision-language model, which costs seconds per image on CPU and is only practical
for frames already kept.

### Local feasibility (measured on the dev machine, i5-11400H, CPU)

| Component | Cost |
|---|---|
| Screen-change probe | p50 31 ms every 500 ms |
| MobileCLIP-S0 embedding | median 107 ms per changed frame, ~1.25 GB peak RSS |
| Model 1 decision | single LightGBM row prediction, negligible next to the encoder (measured by `eval.model_comparison`) |
| Model 2 cycle | seconds, run when idle + on AC |
