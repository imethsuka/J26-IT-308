# How to Run

Step-by-step guide to build, run, and test the Desktop Context-Importance System. Commands are for
**Windows PowerShell**. Run them from the `context-importance-system/` folder unless noted.

The system is two processes: the **C# collector** (senses the desktop) and the **Python worker**
(receives snapshots and runs the ML). You can run each on its own; run both together for the full
pipeline.

---

## 0. Prerequisites

| Need | Check | Notes |
|---|---|---|
| **.NET 8+ SDK** | `dotnet --version` | Windows only (uses Win32 APIs). |
| **Python 3.11+** | `py --list` | 3.13 works too. |
| **MobileCLIP-S0 weights** | a `mobileclip_s0.pt` file (~216 MB) | Needed for the visual features when the worker runs for real (without it, records are stored with no embedding). Not needed for the tests. Install Apple's `ml-mobileclip` package separately. |

> On this machine: .NET 10 SDK is installed, Python is **3.13** (not 3.11 — that's fine), and the
> MobileCLIP checkpoint already exists at
> `..\Member1\checkpoints\mobileclip_s0.pt`.

---

## 1. C# collector

### Build & test
```powershell
cd csharp-collector
dotnet build
dotnet test                 # runs the 94 test cases
```

### Run it (senses the desktop, prints JSON events)
```powershell
dotnet run --project src/CsharpCollector
```
- Prints one JSON object per line to stdout (window changes, activity counts, idle, frame captures).
- Press **Ctrl+C** to stop.
- Run in your normal (interactive) user session, **without** admin rights.
- Edit `src/CsharpCollector/collector.config.json` to change the exclusion list, idle threshold,
  capture settings, and IPC transport. The app won't start if this file is missing.
- Optional custom config path: `dotnet run --project src/CsharpCollector -- --config path\to\config.json`

### Capture-latency benchmark
```powershell
dotnet run -c Release --project benchmarks/CsharpCollector.Benchmarks
```
Prints a table and saves a report under `csharp-collector/benchmark-results/`.

```powershell
cd ..
```

---

## 2. Python worker

### One-time setup
```powershell
cd python-worker
py -3.11 -m venv .venv                 # or:  py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt        # numpy, pandas, scikit-learn, lightgbm, river, torch, pillow
pip install -r requirements-dev.txt    # + pytest (for the test suite)
```

> A `.venv` may already exist here with the dependencies installed. If so, just activate it:
> `.\.venv\Scripts\Activate.ps1`

**MobileCLIP** is not on PyPI. Install Apple's `ml-mobileclip` package separately, then point the
code at the checkpoint:
```powershell
$env:MOBILECLIP_S0_CHECKPOINT = "C:\Users\saime\source\repos\J26-IT-308\Member1\checkpoints\mobileclip_s0.pt"
```
Set this in the **same terminal** that starts the worker (`main.py`), every time; it's what gives
records their embeddings. `eval/measure_encoder.py` also uses it. The tests don't need it.

### Run the tests
```powershell
python -m pytest -q                    # 268 tests
```

### Start the worker (the IPC server the collector connects to)
```powershell
python main.py --transport namedpipe   # default; matches collector.config.json
# or:  python main.py --transport tcp --tcp-port 51789
# no MobileCLIP checkpoint?  add --no-embedding
```
It scores and stores every non-excluded snapshot in `context.db` (`--db` to change) and logs one
line per snapshot. Press **Ctrl+C** to stop; it stops even while waiting for the collector.

Embeddings need `MOBILECLIP_S0_CHECKPOINT` set (see above). Without it the worker logs one warning
at startup and stores records without embeddings; `--no-embedding` skips the attempt. Without
embeddings the visual features stay neutral and prototype learning has nothing to learn from.

---

## 3. Run both together (full sensing → IPC path)

Open **two terminals**.

**Terminal A — worker (start first):**
```powershell
cd python-worker
.\.venv\Scripts\Activate.ps1
python main.py --transport namedpipe
```

**Terminal B — collector:**
```powershell
cd csharp-collector
dotnet run --project src/CsharpCollector
```

Use your desktop normally. Each time the screen changes, the collector captures a frame and sends a
snapshot; Terminal A logs a line like:
```
INFO worker.pipeline seq=12 id=40 'chrome' decision=save p=0.731 (seen=12 stored=12 saved=9)
```
Make sure the collector's `collector.config.json` `ipc.transport` / `pipeName` match the worker's
`--transport` / `--pipe-name`.

---

## 4. The storage database

```powershell
cd python-worker
python -m storage.migrate --db context.db            # create / upgrade the schema
python -m storage.migrate --db context.db --status   # show version without changing anything
python -m storage.report_storage --db context.db     # DB size + row counts (storage-overhead metric)
```

---

## 4b. The dashboard (tray + window)

```powershell
cd dashboard
dotnet build ContextDashboard
dotnet test ContextDashboard.Tests                              # 20 tests
$exe = ".\ContextDashboard\bin\Debug\net8.0-windows\ContextDashboard.exe"
& $exe              # the window (also starts the tray)
& $exe --tray       # tray only
```

Closing the window ends that process and frees its memory. The tray (about 5 MB) stays and controls
the collector and worker: **Start capture** starts the worker, waits for it to load MobileCLIP, then the
collector, both hidden. Logs go to `%LOCALAPPDATA%\context-importance\logs`. **Back up `context.db`
first**: starting the worker applies schema migration v3.

---

## 5. The scheduled adaptation cycle

Runs the whole learning loop in order: label construction → prototype learning → retrain → drift
check → validation gate. It only runs when the machine is **idle and on AC power** unless you pass
`--force`.

```powershell
# one-shot (what Task Scheduler would call), ignoring the idle/AC gate for a manual run:
python -m adaptation.scheduled_job --db context.db --force --summary-log runs.jsonl

# daemon that waits until the machine is idle, then runs, repeatedly:
python -m adaptation.scheduled_job --db context.db --loop --poll-seconds 300
```
- Each run appends a JSON summary (duration, contexts labelled, drift, deploy/keep decision) to
  `runs.jsonl`, and logs the decision with its reason.
- If there isn't enough labelled data yet, it **skips** retraining and says why — expected on a
  fresh database. A record is labelled only once it is 48h old **and** the store holds a full 48h
  of data after it (its revisit window must be fully observed), so a store spanning less than 48h
  can never be labelled. Keep collecting, or lower `--label-horizon-hours` (and
  `--label-delay-hours`) for a short dataset.
- It also skips retraining when nothing was labelled since the last candidate (same data, same
  model), so `--loop` doesn't create a new version every poll. `--retrain-without-new-labels`
  overrides that.
- The first model is only deployed if it beats the rule-based baseline the worker serves until
  then; otherwise the worker keeps using the baseline.
- The worker loads the active model at startup, so restart it after a deploy.

**To schedule it in Windows Task Scheduler:** create a task that runs
`…\python-worker\.venv\Scripts\python.exe -m adaptation.scheduled_job --db C:\path\to\context.db`
on your chosen cadence (e.g. daily). The job self-gates on idle + AC.

---

## 6. The model-comparison table (for the results chapter)

Compares the rule-based baseline, static-threshold baseline, Model 1, and a save-everything
reference on your **labelled** data. Everything is computed from the data — nothing hardcoded.

```powershell
# train Model 1 on a temporal split, evaluate all models on the held-out rows:
python -m eval.model_comparison --db context.db --test-fraction 0.3 --csv comparison.csv

# or evaluate a previously saved Model 1 version on all labelled rows:
python -m eval.model_comparison --db context.db --model-version lightgbm-0.1.0
```
Prints a markdown table (paste-ready) and writes `comparison.csv`. Needs labelled records with both
classes present.

> The numbers come entirely from your data. Run it against your real labelled `context.db`, not a
> throwaway test one.

### MobileCLIP encoder benchmark (latency + memory)
```powershell
python -m eval.measure_encoder `
  --checkpoint "C:\Users\saime\source\repos\J26-IT-308\Member1\checkpoints\mobileclip_s0.pt" `
  --image path\to\screenshot.png --calls 100 --csv encoder_measurements.csv
```

---

## 6b. Train Model 1 on your DRC data (notebook)

`Colab/01_train_local.ipynb` walks through the whole training process step by step, with
explanations, on the data in `D:\DesktopResearchCollector` (read-only; DRC can keep running).

1. `pip install -r requirements-dev.txt` in the worker venv (adds the notebook kernel and plotting).
2. Open the notebook in VS Code → *Select Kernel* → `python-worker\.venv`.
3. Check the paths in step 0, then *Run All*. The first run embeds every screenshot with MobileCLIP
   (several minutes on CPU, then cached). Later runs take seconds.

Outputs go to `%LOCALAPPDATA%\context-importance\drc-training\`. Each run saves a new model version.
The live worker only uses it if you set `DEPLOY_TO_LIVE_DB = True` and it beats what's live. **Clear
all outputs before committing the notebook**: they contain your screenshots.

The same import from the command line, into a `context.db`-schema database for the scheduled job:
```powershell
python import_research_collector.py --source D:\DesktopResearchCollector\collector.db --db context_drc.db
```

---

## 7. Quick reference

| Task | Command (from `python-worker/`, venv active) |
|---|---|
| Run all Python tests | `python -m pytest -q` |
| Start the worker | `python main.py --transport namedpipe` |
| Create/upgrade DB | `python -m storage.migrate --db context.db` |
| DB size + counts | `python -m storage.report_storage --db context.db` |
| Run one adaptation cycle | `python -m adaptation.scheduled_job --db context.db --force` |
| Model comparison table | `python -m eval.model_comparison --db context.db` |

| Task | Command (from `csharp-collector/`) |
|---|---|
| Build | `dotnet build` |
| Run all C# tests | `dotnet test` |
| Run the collector | `dotnet run --project src/CsharpCollector` |
| Capture benchmark | `dotnet run -c Release --project benchmarks/CsharpCollector.Benchmarks` |

---

## 8. Troubleshooting

- **`py -3.11` fails / "no suitable Python"** — you have 3.13. Use `py -3.13 -m venv .venv`; everything
  works on 3.13.
- **Collector exits immediately with "config error"** — `collector.config.json` is missing or invalid.
  It's copied next to the built exe; run via `dotnet run` from `src/CsharpCollector`, or pass
  `-- --config <path>`.
- **Worker logs "listening" but no snapshots arrive** — the transport/pipe name must match on both
  sides. Default is a named pipe called `context-importance`. Start the worker *before* the collector.
- **`ModuleNotFoundError: mobileclip` / `torch`** — only the encoder and its benchmark need those.
  The core pipeline, storage, adaptation, and the model comparison run without them. Install
  `ml-mobileclip` + torch separately if you need embeddings.
- **Adaptation cycle says "skipped"** — normal on a new DB: it needs records older than the 48h
  `label_delay`, a full 48h of data after them (the reason says "waiting for a full 48h of later
  data" otherwise), and both importance classes before it will retrain.
- **Worker logs "MobileCLIP encoder unavailable"** — `MOBILECLIP_S0_CHECKPOINT` isn't set or
  torch/mobileclip are missing. Records are still stored, without embeddings.
- **`report_storage` shows far fewer `w/ embedding` than total records** — the worker ran without
  the checkpoint env var for those sessions (easy to miss: it's one warning at startup). Those
  records have neutral visual features and give prototype learning nothing to learn from. Set the
  variable in the worker's terminal before collecting real data.
- **Model comparison errors "single-class" / "no labelled records"** — your DB has no labels yet, or
  only one class. Run the adaptation cycle (or label some records) first.
- **Antivirus flags the collector** — expected for an unsigned binary that installs a global keyboard
  hook and captures the screen. For real deployment, code-sign it and submit for allow-listing (see
  `csharp-collector/src/CsharpCollector/Native/NativeMethods.cs`).
