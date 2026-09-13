# Desktop Context-Importance System

Adaptive desktop context acquisition and importance learning. The system observes
desktop activity, captures screen context, and learns which moments matter to the user.

> Status: the end-to-end pipeline (sensing → IPC → features → scoring → storage → delayed-label
> adaptation → deployment) is implemented and tested. See [IMPLEMENTATION.md](IMPLEMENTATION.md)
> for the full component-by-component writeup, and its "Known limitations" section for what's left.
> §10 there maps the architecture diagram (`Deep-down Member 1 Architecture.drawio.png`) onto the code.
> [CLAUDE.md](CLAUDE.md) is the short contributor/agent guide: invariants, gotchas, current status.

## Architecture

The system runs as two cooperating local processes:

```
┌──────────────────────────────┐                      ┌──────────────────────────────────┐
│  csharp-collector (.NET 8)   │                      │  python-worker (Python 3.11)     │
│                              │   named pipe  OR     │                                  │
│  • Win32 hooks (keyboard,    │   localhost TCP      │  • sensing/    ingest events     │
│    mouse, window focus)      │ ───────────────────▶ │  • features/   MobileCLIP embeds │
│  • Screen capture            │   events + frames    │  • models/     LightGBM scorer   │
│  • IPC client                │ ◀─────────────────── │  • adaptation/ online learning   │
│                              │   importance scores  │  • storage/    persistence       │
│                              │   / control msgs     │  • eval/       metrics           │
└──────────────────────────────┘                      └──────────────────────────────────┘
```

### csharp-collector
A .NET 8 console app (`src/CsharpCollector`, tests in `tests/CsharpCollector.Tests`) that
owns everything needing native Windows access:
- **Hooks/** — `SetWinEventHook` (foreground and title changes) and `WH_KEYBOARD_LL` / `WH_MOUSE_LL`
  (activity *counts* only; key codes and cursor positions are never read).
- **Sensing/** — exclusion-first window processing, rolling-window activity counts, idle detection (`GetLastInputInfo`).
- **Browser/** — Edge/Chrome address bar via UI Automation.
- **Filtering/** — app-name and URL-domain exclusion list, applied before any title/URL is read.
- **Native/** — P/Invoke declarations. `NativeMethods.cs` lists the **elevation and antivirus
  false-positive risks** of each API.
- **Capture/** — screen-change detector. Every probe (500 ms by default) grabs the primary
  monitor, downscales it to a 64x36 greyscale thumbnail and compares the mean absolute difference
  with the last stored frame. When it crosses `diffThreshold`, that frame is copied into an
  in-memory `FrameBuffer` (1 slot by default). Frames are never written to disk. Capture follows
  the same exclusion gate as input counting, so nothing is captured while an excluded app or
  domain is in the foreground.
- **Ipc/** — transport to the Python worker: `"CIS1"` framing + JSON header + raw frame bytes over
  a named pipe or localhost TCP (see below). Verified end-to-end over a real Windows named pipe.

Exclusions are configured in `collector.config.json` and fail closed: an unknown process, or a
browser URL that can't be read while domain rules exist, is treated as excluded.

Events are written to stdout as JSON Lines, and diagnostics go to stderr:

```json
{"type":"foreground","ts":"...","process":"chrome","title":"Docs","url":"docs.example.com/page"}
{"type":"title_change","ts":"...","process":"Code","title":"main.cs"}
{"type":"excluded_context","ts":"..."}
{"type":"activity","ts":"...","window_seconds":10,"key_presses":42,"mouse_clicks":3,"mouse_scrolls":7,"mouse_moves":310}
{"type":"idle_start","ts":"...","idle_since":"..."}
{"type":"idle_end","ts":"...","idle_seconds":184.2}
{"type":"frame_captured","ts":"...","width":1920,"height":1080,"diff":6.98}
```

### python-worker
A Python 3.11 process that owns all ML:
- **sensing/** — IPC listener (`listener.py`) plus the wire protocol (`protocol.py`, `codec.py`).
  It accepts collector connections and hands each decoded snapshot to a handler; `main.py` wires
  that handler to `pipeline.SnapshotPipeline` (feature assembly → scoring → storage), so a running
  worker persists every non-excluded snapshot to `context.db`. The bare `log_snapshot` handler
  (logs only, never pixels) remains available for the listener's own tests.
- **features/** — `mobileclip_encoder.py` loads the frozen MobileCLIP-S0 **image encoder only**
  (text tower dropped) and exposes `embed_frame(image) -> 512-d L2-normalized float32 vector`.
  `feature_assembly.py` provides `assemble_features(sensing_event, embedding, recent_context_history)`,
  which returns a flat feature dict — behavioural (counts, app-switch rate), temporal (dwell, time
  of day, time since last save), contextual (app/title tokens/domain, revisit-so-far), and visual.
  The 512-d embedding is **summarized** into two scalars — similarity to the nearest injected
  importance prototype and novelty vs. a small in-memory ring of recently saved embeddings — and is
  never emitted raw.
- **models/** — importance scorers behind one interface (`base.ImportanceScorer`:
  `predict_proba(features) -> float`). `rule_based.RuleBasedImportanceScorer` is the interpretable
  baseline: three rules (dwell-time threshold, revisit flag, app-category weight) combined as
  weighted evidence in log-odds and squashed to a real probability, with `explain()` for the
  per-rule breakdown. `lightgbm_model.ImportanceModel` (Model 1) implements the same interface,
  plus `train(labelled_dataframe)`, feature-importance reporting (`feature_importances` /
  `format_importances`), and append-only `save(version_id)` / `load(version_id)` under
  `models/versions/` (existing versions are never overwritten, so rollback history is kept).
  `vectorizer.py` pins the fixed numeric feature layout shared by train and inference.
- **adaptation/** — `label_construction.py` builds delayed weak-supervision labels: for records
  older than `label_delay` (default 48h) and still unlabelled, it computes revisit (left the context
  and came back within the 48h horizon), continuation (task resumed in a later session), and an
  injected retrieval signal (Member 3's component — a
  callback/mock here, never implemented), then merges them via the citable
  `combine_importance_label` rule. `label_pending_records` runs it against the SQLite store.
  `prototype_learning.py` learns the visual **importance prototypes** — spherical KMeans over the
  embeddings of important contexts, returning unit-norm centroids that feed
  `assemble_features(..., prototypes=...)`; `save_prototypes` / `load_prototypes` persist them per
  model version under `models/versions/<version_id>/` (append-only). Two **separate** drift
  components live here, not conflated: `drift_detection.ErrorDriftDetector` wraps River's ADWIN over
  Model 1's labelled error stream (`detect(error) -> bool`, concept drift, needs labels), and
  `input_monitor.InputDistributionMonitor` tracks rolling app-mix and mean prototype-similarity to
  flag input-distribution shifts label-free (covariate drift, runs continuously). Both only *detect*
  drift — they do not schedule or perform retraining. `deployment.py` gates promotion:
  `validate_and_maybe_deploy(candidate, current, held_out)` scores both models
  (precision/recall/F1/accuracy), deploys the candidate only if *strictly better* on the primary
  metric (at cold start, better than the rule-based baseline the worker serves), logs the full comparison to the `model_versions` history table, and `rollback_to(version)`
  re-activates any prior version. `scheduled_job.py` is the maintenance job that runs the whole
  pipeline in order — label construction → prototype learning → retrain → drift check → validation
  gate — **gated on the host being idle and on AC power** (`host_state.py`, real Windows
  `GetLastInputInfo` / `GetSystemPowerStatus` via ctypes, injectable for tests). It runs one-shot
  (Task Scheduler) or as a `--loop` daemon, and appends a structured JSON run summary (duration,
  contexts labelled, drift, deploy/keep decision) for reporting:
  ```powershell
  python -m adaptation.scheduled_job --db context.db [--loop] [--force] [--summary-log runs.jsonl]
  ```
- **storage/** — SQLite persistence. `context_records` holds features (JSON), the 512-d embedding
  (float32 BLOB), the model's `decision` (save/discard), the later ground-truth `label` (NULL until
  the label-construction phase), and timestamps; `model_versions` tracks model history. A partial
  index `ix_context_unlabelled` serves the "unlabelled records older than N hours" scan.
  `python -m storage.migrate --db context.db` applies migrations; `python -m storage.report_storage
  --db context.db` reports DB size and row counts (the storage-overhead metric — ~2 KB/record,
  dominated by the embedding BLOB).
- **eval/** — offline evaluation. `measure_encoder.py` benchmarks the MobileCLIP encoder;
  `model_comparison.py` scores the rule-based baseline, the static-threshold baseline, Model 1, and a
  save-everything reference side by side on a labelled dataset — precision/recall/F1, projected
  storage reduction vs. saving everything (the worker currently stores every context, since
  labelling and retraining need them), relevant-context retention, and mean/p95 decision latency — and
  writes a CSV plus a pasteable markdown table. Every value is computed from the provided data (the
  labelled context store) and live timing; nothing is hardcoded.
  ```powershell
  python -m eval.model_comparison --db context.db [--test-fraction 0.3] [--model-version V] [--csv out.csv]
  ```

The worker is the IPC **server**; the collector is the client. Start the worker first:
```powershell
cd python-worker
python main.py [--db context.db] [--transport namedpipe|tcp] [--pipe-name NAME] [--tcp-port PORT] [--no-embedding]
```

### IPC transport and message schema
The collector connects to the worker over one transport, chosen in `collector.config.json` and
matched by the worker's `--transport`:
- **Named pipe** (`\\.\pipe\context-importance`, default) — opened current-user-only on the client
  and rejecting remote clients on the server. No network exposure.
- **Localhost TCP** (`127.0.0.1` only) — fallback; loopback-bound but the peer is not authenticated.

**Framing** (all integers little-endian):
```
"CIS1" (4 bytes) | header_length: u32 | payload_length: u32 | header (UTF-8 JSON) | payload (raw frame bytes)
```

The header is a `context_snapshot` JSON object (schema version 2; the worker also accepts version 1, which has no `text`); the payload is the raw BGRA
frame pixels it describes. The frame is sent as raw bytes **beside** the JSON rather than base64
inside it — an ~8 MB 1080p frame would grow ~33% and cost an encode/decode pass on every capture.
One snapshot is sent per captured frame:
```json
{
  "schema": "context_snapshot", "version": 1, "seq": 12,
  "ts": "2026-09-24T07:02:35.796737Z", "trigger": "frame",
  "context": {"excluded": false, "app": "chrome", "title": "Docs", "domain": "docs.example.com", "dwell_seconds": 12.5},
  "activity": {"window_seconds": 10, "key_presses": 42, "mouse_clicks": 3, "mouse_scrolls": 7, "mouse_moves": 310},
  "idle": {"is_idle": false, "idle_seconds": 0.0},
  "frame": {"width": 1920, "height": 1080, "stride": 7680, "format": "bgra32", "captured_at": "...", "diff": 6.98}
}
```
When `screenContent.enabled` is true (off by default), a non-excluded snapshot also carries the foreground window's
visible text and large images, read with UI Automation at the time of the frame:
```json
"text": {"source": "uia", "chars": 4210, "truncated": false, "read_ms": 38.5, "content": "...",
         "images": [{"x": 420, "y": 180, "width": 600, "height": 600, "alt": "Red shirt"}]}
```
`text` is absent or null for an excluded context, a password field, nothing readable, or the feature being off. The worker
keeps it in a separate encrypted file (`raw_buffer.db`), never in `context.db`. The header limit is 1 MiB for this.

An excluded context carries `excluded: true` and null app/title/domain. The three definitions —
C# `SnapshotHeader`/`WireFormat`, Python `protocol.py`, and this section — must be kept in sync.

## Getting started

### C# collector
```powershell
cd csharp-collector
dotnet build
dotnet test
dotnet run --project src/CsharpCollector [-- --config path\to\collector.config.json]
```
Run it in the interactive user session (not as a Windows Service) and without admin rights.

Capture latency benchmark (prints a Markdown report and saves it to `benchmark-results/`):
```powershell
dotnet run -c Release --project benchmarks/CsharpCollector.Benchmarks [-- --iterations 200 --warmup 20 --out dir]
```

### Python worker
```powershell
cd python-worker
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

MobileCLIP is not on PyPI. Install Apple's `ml-mobileclip` package separately and download the
S0 checkpoint (`mobileclip_s0.pt`, ~216 MB). Point the code at it with `--checkpoint` or the
`MOBILECLIP_S0_CHECKPOINT` environment variable.

Resource benchmark for the image encoder (CPU latency + peak RSS, appends a CSV row per run so
results from several machines accumulate in one file):
```powershell
python -m eval.measure_encoder --checkpoint path\to\mobileclip_s0.pt --image screenshot.png `
    [--calls 100] [--device cpu] [--csv encoder_measurements.csv] [--label this-machine]
```
Memory is read straight from the OS (Windows `GetProcessMemoryInfo`, POSIX `getrusage`), so no
profiler dependency is needed. Reference run (desktop i5-11400H, CPU, 1920x1080 input, 100 calls):
median **107 ms**, p95 **128 ms** per embedding; peak RSS **~1.25 GB** with the model loaded.

## Scope
See [CONTRIBUTING.md](CONTRIBUTING.md).
