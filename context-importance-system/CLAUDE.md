# CLAUDE.md

Guidance for Claude Code (and any contributor) working in `context-importance-system/`.
[THESIS_NOTES.md](THESIS_NOTES.md) holds the algorithms (formal + pseudocode), every measured result
with its source, and the charter mapping. Update it when results change.
Read [IMPLEMENTATION.md](IMPLEMENTATION.md) for the component-by-component writeup and
[HOW_TO_RUN.md](HOW_TO_RUN.md) for every command; this file is the short version plus the things
that are easy to get wrong.

## What this is

Member 1's part of the J26-IT-308 group project: *adaptive desktop context acquisition and
importance learning*. Two local processes on Windows:

- `csharp-collector/` (.NET 8, Win32): senses the desktop and sends one snapshot per screen change.
- `python-worker/` (Python, ML): MobileCLIP embedding → features → importance score → SQLite, plus
  the scheduled learning loop.

The git root is the parent folder `J26-IT-308/`. `../Member1/` is an earlier standalone MobileCLIP
prototype; it is not part of this system, but its `checkpoints/mobileclip_s0.pt` is the weights file
the worker uses.

## Scope (from CONTRIBUTING.md, enforce it)

Out of scope, owned by other members: **privacy/tokenization/redaction** and **retrieval/search/RAG**.
If a change needs one of them, define an interface boundary (like the injected retrieval callback in
`adaptation/label_construction.py`) and stop there.

## The author's vocabulary → code

The author thinks in terms of `Deep-down Member 1 Architecture.drawio.png`. Map it like this:

| Diagram | Code |
|---|---|
| Senses: window title, mouse, keyboard, "time user stayed" | C# `Sensing/`, `Hooks/`; Python `features/feature_assembly.py` (`beh_*`, `tmp_*`, `ctx_*`) |
| MobileCLIP ("the eyes") | `features/mobileclip_encoder.py`, a frozen image encoder giving a 512-d vector, summarized into `vis_*` scalars |
| Model 1 ("capture or not") | `models/lightgbm_model.py`; the rule-based baseline (`models/rule_based.py`) serves until a model is deployed |
| Model 2 ("learns long-term behaviour, teaches Model 1") | Not a neural network. It is the `adaptation/scheduled_job.py` pipeline: delayed labels → prototypes → retrain → drift → deploy gate |
| "Adjust vector embedding" arrow | `adaptation/prototype_learning.py`. MobileCLIP is never fine-tuned; Model 2 learns prototypes that Model 1 compares against |
| DB cylinder / SQL | `storage/`, `context_records` table in `context.db` |
| "Capture to SSD" → Screenshot folder | **Not implemented.** The decision is recorded, not enforced, and frames are never written to disk |

IMPLEMENTATION.md §10 explains where the diagram and the code differ, and why.

## Commands (Windows; run from the folder shown)

```powershell
# csharp-collector/
dotnet build; dotnet test                      # 130 tests
dotnet run --project src/CsharpCollector

# python-worker/  (venv is Python 3.13 at .venv/; the tests do not need MobileCLIP)
.\.venv\Scripts\python.exe -m pytest -q        # 461 tests, ~30 s
$env:MOBILECLIP_S0_CHECKPOINT = "C:\Users\saime\source\repos\J26-IT-308\Member1\checkpoints\mobileclip_s0.pt"
.\.venv\Scripts\python.exe main.py --transport namedpipe        # start the worker BEFORE the collector
.\.venv\Scripts\python.exe -m storage.report_storage --db context.db
.\.venv\Scripts\python.exe -m adaptation.scheduled_job --db context.db --force
.\.venv\Scripts\python.exe -m eval.model_comparison --db context.db
```

From Git Bash, use `./.venv/Scripts/python.exe` (no activation needed).

## Invariants: don't break these

- **Wire format lives in three places that must stay in sync:** C# `Ipc/SnapshotHeader.cs` +
  `Ipc/WireFormat.cs`, Python `sensing/protocol.py`, and the README "IPC transport" section.
- **Exclusion runs first and fails closed.** No title/URL/pixels are read for an excluded or
  unidentifiable context. Input hooks count events and never read key codes or cursor positions.
- **Frames stay in memory.** Nothing writes screenshots to disk today. Adding the screenshot folder
  is a deliberate design change, not a bug fix (see open questions).
- **The raw 512-d embedding never goes into the feature dict**, only the `vis_*` summaries. It is
  stored separately as a BLOB.
- **Every non-excluded snapshot is stored**, whatever the decision. Labelling and training need the
  discarded contexts too. The one exception: Windows shell surfaces (`pipeline.NON_CONTEXT_APPS`: `dwm`, `lockapp`,
  `searchhost`, `shellhost`, ...) are not contexts and are skipped before embedding. `explorer` and
  `ApplicationFrameHost` are real and still stored. Offline replays do not apply this list yet (small train/serve skew).
- **Model and prototype versions are append-only** under `python-worker/models/versions/`. Never
  overwrite one; rollback depends on the history.
- **Feature layout is pinned** in `models/vectorizer.py` (`FEATURE_COLUMNS`). Changing it invalidates
  saved models, so bump/version accordingly.
- **Labels are immutable once written** and stamped `label_version` (currently `v3/h48`). If you
  change the labelling rule, bump `LABEL_RULE_VERSION` in `adaptation/label_construction.py`.
- **Deploy only if strictly better** than the incumbent (the rule-based baseline at cold start).
- **No hardcoded results** in `eval/`. Every number is computed from the DB and live timing.

## Gotchas

- The worker loads the active model and prototypes **at startup only**. Restart it after a deploy.
- If neither the ONNX model nor `MOBILECLIP_S0_CHECKPOINT` is found, the worker logs one warning and silently stores records
  **without embeddings**. Check the `w/ embedding` count in `report_storage` after a real session.
- Labels need records ≥48h old **and** 48h of later data, so a fresh DB legitimately labels nothing.
- `*.db`, model versions, and weights are gitignored. Local `context*.db` files are real user activity
  data: don't delete, commit, or upload them. Back up before running migrations (`*.bak-*.db` are earlier backups).
- Files are LF in the working tree; git warns about CRLF conversion. That's harmless.

## Historical data: DesktopResearchCollector (DRC)

`D:\DesktopResearchCollector\` is the author's separate, rule-based data-gathering tool (not part of
this system): `collector.db` (tables `activity_events`, `browser_events`, `interaction_events`,
`screenshots`, `sessions`) plus `screenshots\*.jpg`. It is real personal activity data.

- DRC usually runs live (auto-starts with Windows). Open its DB **read-only** (`file:...?mode=ro`)
  or take a backup-API snapshot. Never write to it, and never copy the data into this repo.
- **`duration_ms` on an activity event is the time spent in the *previous* window**, not the one the
  row names (verified 2026-10-18: 15,424 of 15,463 events). Attribute it backwards.
- `LockApp.exe` is the lock screen (about 26 h of "dwell" by 2026-10-18). Treat it as away/idle, not
  a context.
- DRC's exclusion list differs from `collector.config.json`. An importer must apply this project's
  exclusions itself.
- `python-worker/offline/drc_replay.py` is the one place that reads DRC. It makes one capture per
  screenshot, computes dwell-so-far (never `duration_ms`), scales the per-minute interaction counts
  to the live 10 s window, and applies this project's exclusions fail-closed. It's tested in
  `tests/test_drc_replay.py`. `import_research_collector.py` is a thin CLI over it.
- DRC file name == `screenshot_id`, and the cloud bucket uses the same IDs
  (`<participant>/<yyyy>/<mm>/<dd>/<id>.jpg`). The bucket holds **screenshots only**, no events, so
  cloud screenshots can't be labelled without the matching `collector.db`.

## Offline training (notebooks)

- `Colab/01_train_local.ipynb` trains Model 1 on DRC data step by step, using `offline/training.py`
  (embed → features → labels → embargoed time split → train-only prototypes → train → grade →
  save/bundle). It's for a beginner, so keep its markdown plain.
- **Never commit executed notebook outputs.** They contain personal screenshots and titles. Execute
  copies outside the repo. Derived data (embedding cache, bundles, hand labels) lives in
  `%LOCALAPPDATA%\context-importance\drc-training`, not the repo.
- `Colab/02_train_colab_neon.ipynb` is the cloud version. DRC syncs every participant's events
  to **Neon PostgreSQL** (schema `drc`, same tables as DRC plus `participants`, `devices`,
  `retrieval_events`). Screenshots are in the bucket at `screenshots.file_reference`. The notebook
  reads only `PROFILE_ID`'s rows and the columns the replay needs, rebuilds a DRC-shaped SQLite file
  so `drc_replay` runs unchanged (verified identical to a direct replay on 5,049 captures), and
  never writes to Neon or the bucket. The connection string comes from the Colab secret `DATABASE_URL`;
  never hardcode it.
- `Colab/03_experiments_colab.ipynb` holds the experiments (02 is left as-is). It loads data through
  `offline/cloud_sources.py` and runs v3 vs v4 labels, MobileCLIP zero-shot screen categories, and
  rolling (expanding-window) evaluation. Everything experimental is **opt-in with unchanged
  defaults**: `title_normalizer` (`features/titles.py` → `v4` labels), `extra_feature_columns` (a model
  using them is offline-only; the live worker doesn't compute `vis_cat_*`), `encode_texts`.
- **Compare models with threshold-free metrics** (`ranking_metrics`: AUC, average precision, recall at
  a storage budget), not F1 at 0.5. The rules and LightGBM are calibrated differently, and F1 at 0.5
  rewards keeping more. Local 8 to 19 Oct preview: rules won on F1, while LightGBM ranked better
  (AP 0.85 vs 0.75, recall@30% 0.46 vs 0.40). Vision (no eyes vs eyes) made no difference.

## Status (as of 2026-10-18; re-verify before relying on it)

- Both suites green: C# 130/130, Python 461/461, dashboard 49/49 (2026-10-20).
- Live `context.db`: 1,128 records, only 28 with embeddings (env var not set during most runs), all
  unlabelled, no deployed model. The worker is still serving the rule-based baseline, which marks
  ~85% of contexts "save", so it is not yet selective.
- `context_imported.db` has one older LightGBM version, trained on pre-v3 labels.
- The learning loop has **not yet been shown to improve Model 1 on real data**. That is the main
  open evidence gap.
- A large body of work was uncommitted at that date. Check `git status` before assuming a file is in history.

## Open questions for the author (unanswered as of 2026-10-18)

1. Should "save" produce an image file in a screenshot folder (for Member 3), or only the DB record?
2. Is a gist-level visual fingerprint enough, or must the system describe screens in words (would
   need OCR or a small VLM, run only on kept frames)?
3. Minimum target hardware? On 8 GB RAM, MobileCLIP via torch (~1.25 GB peak RSS) should move to
   ONNX Runtime.
4. Must "Model 2" be a neural network for the proposal/supervisor, or is the learning pipeline fine?
5. Cloud training: may derived data (bundles without titles, embeddings, DB snapshots) be uploaded to
   the bucket? Did the second participant (`pasi-…`) consent to their screenshots being used for
   training? Where are the DRC events for 20 Sep – 8 Oct (the Cloud Run service `velo`?).

## DRC-rules baseline (added 2026-10-19)

`python-worker/models/drc_rules.py` is a frozen copy of DRC's *capture decision* (triggers, 15 s
cooldown, 20 s scroll cooldown, 1800/day quota, privacy filter, policy v7). It takes no screenshot and
needs no DRC folder. The worker records its verdict per snapshot in `context_records.drc_would_capture`
and `drc_trigger` (migration v3). It is logged, never enforced. **Back up `context.db` before the
worker's next start**, because `main.py` runs the migration.

- Do not retune it. Add a new named `DrcPolicy` instead, so old comparisons stay reproducible.
- `LiveDrcObserver` infers triggers from per-screen-change snapshots, so it is an approximation (periodic
  captures are only noticed when a snapshot arrives).
- Validate with `python -m eval.drc_agreement --drc D:\DesktopResearchCollector\collector.db`
  (read-only). Measured 2026-10-19 on 5,904 DRC screenshots: precision 0.925, recall 0.854, F1 0.888
  (matched within 3 s). Scroll bursts are not reproducible (123 of the misses; DRC stores counts per
  minute, not notch timing). Sleep gaps are handled by inferring when idle began from the last recorded input.
- Run the validation once while DRC's `collector.db` exists and keep the numbers; afterwards DRC can go.
- Its decisions are a baseline only. They are **not** labels (labels stay `v3/h48`, from delayed behaviour).

## Screen text (added 2026-10-20; plan in `docs/SCREEN_TEXT_PLAN.md`)

Built: the collector reads the foreground window's visible text and large images with UI Automation and sends them
in the snapshot header (protocol v2, `text` object); the worker puts them in a **separate** file, `raw_buffer.db`.
**Off by default** (`screenContent.enabled`), because consent does not cover extracted text yet.

- `Content/ForegroundScreenTextSource.cs` reads only while the context gate allows it, never for an excluded or
  unidentified context, and drops the text if the foreground changed during the read. `SnapshotPublisher` also
  never sends text with an excluded context.
- `storage/raw_buffer.py`: zlib then Windows DPAPI (current user), TTL 3 days, text cap 200 MB (lowest score dropped first,
  metadata kept). Never in `context.db`; created on the first capture with text. If the text can't be encrypted it is not stored.
  Don't log or print screen text (`ScreenText.content` is excluded from `repr`).
- `features/text_merge.py` (one copy per page from overlapping captures) and `adaptation/episodes.py` are pure and in-memory.
- **Deferred stage** `adaptation/screen_memory.py` (stage 6 of `scheduled_job`, opt-in `--screen-memory`, idle + AC like the rest):
  closed episodes -> tier by max Model 1 score (high >= 0.5, medium >= 0.2, low; guesses) -> per-capture `PrivacyFilter` ->
  merge into the page's stored document -> `memory.db` (third separate file: documents, episodes, `memory_entries`) -> delete the raw rows.
  low = one metadata line and no text; an episode still in progress (last capture < 30 min old) is left; re-running is a no-op.
- **Privacy boundary** `adaptation/privacy.py`: `PrivacyFilter.filter(text, ctx) -> FilterResult`. Member 2 implements it;
  `tests/test_privacy_contract.py` is the contract. **No filter = no text stored** (metadata-only entries). The stage fails
  closed (filter raises or blocks -> that capture's text is dropped). `--dev-passthrough-filter` stores text unredacted: your own data only.
  Deviation from the plan: text is filtered per capture *before* merging, so the stored page is always filtered.
- Entries have no summary yet: high-tier entries with text are flagged `needs_summary` for the future local-LLM stage.
- **Opt-in in the app (default OFF everywhere).** Dashboard tab *Screen text*: a checkbox that shows the consent text
  (`AppSettings.ConsentText`, versioned: change it and bump `CurrentConsentVersion` to ask everyone again) and only then
  sets `ScreenTextEnabled` + the consent version/time in `dashboard.json`. The switch alone is not consent. The choice is
  passed explicitly on every start: collector `--screen-text on|off` (wins over the read-only config file, both ways),
  worker `--no-screen-text` when off, learning job `--screen-memory` when on. Opting out also clears the consent and the
  developer option; *Delete all captured text* empties `raw_buffer.db` and `memory.db` (VACUUM). That delete is the one place the
  dashboard writes to those files. The tab shows counts and sizes of the buffer and the memory entries, never raw text.
  The *Developer* checkbox (`ScreenTextKeepUnredacted` -> `--dev-passthrough-filter`) keeps text with no redaction; warn before using it.
- Verified 2026-10-20 on a freshly built bundle (`build_bundle.ps1 -StageName ContextImportance-screentext`):
  `packaging/smoke_test_worker.py --screen-text` PASS (frozen worker buffers text encrypted in `raw_buffer.db`, `--no-screen-text`
  keeps nothing, frozen `adapt.exe --screen-memory` writes `memory.db` entries with no text, raw rows consumed); the packaged
  dashboard window starts. The click path (consent dialog, restart, delete button) is covered by unit tests of the settings and
  data classes but was **not clicked through by hand**; the Inno installer was not rebuilt.
- Flags: `python -m adaptation.scheduled_job --db context.db --screen-memory [--dev-passthrough-filter] [--raw-db] [--memory-db]`.
  The dashboard's *Run learning now* and the background learner pass it only when the user opted in.
- Worker flags: `--raw-db`, `--raw-ttl-days`, `--raw-max-mb`, `--no-screen-text`.
- Measured: merging a 50,000-character page takes about 8 ms (the plan guessed under 5 ms). Not yet run live against a real desktop.

## Dashboard (WPF, added 2026-10-19)

> **Moved (2026-10-21):** the dashboard now lives in `UI/ContextDashboard/` (repo root `UI/`, with the shared `UI/Suite.Core`
> and Member 2's `UI/CAPRLMComparison`): a sidebar shell whose Memory module holds the pages described below. The old
> `dashboard/` folder here was deleted. Wherever this section says `dashboard/ContextDashboard/`, read `UI/ContextDashboard/`;
> tests are in `UI/ContextDashboard.Tests/`.

`dashboard/ContextDashboard/` (.NET 8, WPF + a WinForms tray icon). One exe, two modes, so closing the
window really frees its memory:

- `--tray`: notification icon only (no WPF loaded, ~5 MB). Menu: open dashboard, start/stop capture,
  start with Windows. The tray starts the worker, waits for its pipe, then the collector, hidden and detached.
- default / `--dashboard`: the window. Closing it ends the process. It polls every 3 s only while open
  and opens `context.db` **read-only** for each read.
- Never put the worker or collector inside the UI process. Never write to `context.db` from the UI.
- "Worker running" = its named pipe exists. "Collector running" = process `CsharpCollector`. Stop uses
  PID files in `%LOCALAPPDATA%\context-importance\run` (so a worker started by hand cannot be stopped from the UI).
- Start with Windows is a per-user `Run` registry entry for the tray, not a Windows service: the
  collector needs the interactive desktop (hooks, screen capture).
- Settings live in `%LOCALAPPDATA%\context-importance\dashboard.json`, logs in `...\logs\`. Starting from the UI sets
  `MOBILECLIP_S0_CHECKPOINT`, which fixes the "records stored without embeddings" gotcha.
- Charts are drawn by hand (`Views/BarChart.cs`), no charting library.
- `dotnet test dashboard/ContextDashboard.Tests` (35 tests, use a temp SQLite file with the worker schema).
  Both frameworks define `Point`, `Brush`, `Color`: alias them in WPF files. The WinForms implicit-using set
  omits `System.IO`, so the csproj adds it.
- **Learning trigger (2026-10-20):** the Models tab has *Run learning now* (`Services/LearningRunner.cs`): it takes a
  consistent SQLite backup (`%LOCALAPPDATA%\context-importance\backups`, newest 3 kept), runs one `adapt.exe` cycle
  (frozen) or `python -m adaptation.scheduled_job` (repo), and shows what the job logged in `adaptation_runs.jsonl`.
  Options: quick test (1 h delay/horizon, noisy labels) and the KW label rule. Starting capture also starts a
  background learner (`adapt.exe --loop`: waits for an idle PC on AC power; setting `BackgroundLearning`, default on).
  The worker loads its model at startup only, so the Models tab shows a **Restart capture** banner when the active
  model differs from the `scoring with ...` line in `logs\worker.log`.
- Tests that run the real frozen job must set `CONTEXT_IMPORTANCE_DATA` to a temp folder, or it writes model
  versions into the real data folder (this happened once; the integration test now isolates it).
- Not built yet: deploy/rollback of a specific version, exclusions editor, pause/resume (needs a worker control
  channel), threshold-sweep and feature-importance charts.

## Model 2 learns the user (added 2026-10-19)

- `adaptation/user_profile.py`: `UserProfile.fit(records)`, learned from stored records, no labels needed:
  typing baseline per hour (typing in *other* contexts in the next 10 min), per-snapshot typing per hour,
  return rate and return time per app, app-to-app transitions, hourly routine. Shrunk towards global values.
  `user_features()` gives opt-in `usr_*` columns (`USER_FEATURE_COLUMNS`) for Model 1, offline only; the pinned
  `FEATURE_COLUMNS` is unchanged. Fit on the training split only, or the test period leaks in.
  Saved as `user_profile.json` in a model version folder (append-only, never overwritten).
- `adaptation/kw_labels.py`: `KWLabelConstructor`, the Keep-Worthiness label (`docs/Importnace Algorithm.md`),
  stamped `kw1/h<H>`. Opt-in: `scheduled_job --label-rule kw`, or `add_labels(constructor=KWLabelConstructor(...))`.
  `U` (reuse) is missing because no screen text is stored. Constants are starting guesses (`KWParams`); changing one
  means bumping `KW_RULE_VERSION`. Rows already labelled `v3` are never relabelled.
- Measured 2026-10-19 on the 5,049 DRC captures (3,848 labelled): `v3` positive 52.9%, KW positive 24.1%, they agree
  on 71.1%, KW says 0 where v3 says 1 for 1,110 rows and the opposite for 3. **The KW score has a cliff**: returned
  contexts the user visits very often are fully re-findable, so they land at exactly the floor 0.3, just under the 0.35
  threshold (positives: 50.3% at 0.30, 24.1% at 0.35). Choose the threshold deliberately (or train on the continuous
  score) and report it; do not tune it silently against the test split.
- Nothing here has been validated against hand labels yet: that is what decides whether KW beats `v3`.

## ONNX encoder (added 2026-10-19)

The shipped worker embeds frames with **ONNX Runtime, not torch** (`features/onnx_encoder.py`); torch is only
for training and the one-off export. `features.mobileclip_encoder.default_encoder()` prefers ONNX and falls back to
torch (`MOBILECLIP_BACKEND=auto|onnx|torch`). Importing the worker pulls in no torch, torchvision or mobileclip.

- Export (dev machine, once): `python -m features.onnx_export --checkpoint ...\mobileclip_s0.pt --out %LOCALAPPDATA%\context-importance\mobileclip_s0_image.onnx`
  (43.5 MB, image tower only, output already L2-normalized). Found via `MOBILECLIP_ONNX_MODEL`, `models/`, or that folder.
- Verify after any re-export: `python -m eval.verify_onnx_encoder --onnx ... --images <jpg folder> --checkpoint ... --cache drc_embeddings.npz --prototypes prototypes.npy --measure`.
  Measured 2026-10-19 (80 DRC frames, i5-11400H): cosine vs torch min 0.9999999, max abs diff 1.5e-6; vs the stored torch
  embeddings the same; nearest saved prototype identical for 100% of frames. So existing embeddings, prototypes and
  models stay valid. **Peak RAM 146 MB vs 1,098 MB, load 0.36 s vs 13 s, median 55.6 ms vs 78.6 ms per frame.**
- Preprocessing is reimplemented with pillow/numpy (bilinear resize of the short side to 256, centre crop, /255) and tested
  equal to torchvision's transform. Inference is capped to 2 threads with the memory arena off, on purpose.

## Packaging into one app (added 2026-10-19)

`packaging/` builds an installable folder (`build_bundle.ps1`) and an Inno Setup script (`ContextImportance.iss`; Inno
is **not installed here, so the installer .exe has not been built or tested**). See `packaging/README.md`.

- Layout: `ContextDashboard.exe` + runtime, `collector\`, `worker\` (frozen `worker.exe` and `adapt.exe`), `models\*.onnx`.
  Staged size 575 MB uncompressed (dashboard ~145, collector 160, worker 226, model 43); each .NET part carries its own runtime.
- The worker is frozen with PyInstaller from `packaging/.venv-build` (no torch). `app_paths.py`: a frozen worker (or
  `CONTEXT_IMPORTANCE_DATA`) keeps `context.db`, `models/versions/` and `adaptation_runs.jsonl` under
  `%LOCALAPPDATA%\context-importance`, never next to the program; from source the legacy repo layout is unchanged.
- The dashboard has an "installed mode" (`AppSettings.FindInstall`): it finds `worker\worker.exe` and `collector\`
  next to itself and uses `DataDir` for the database.
- Verified 2026-10-19: `packaging/smoke_test_worker.py` starts the frozen worker, sends 1080p frames over the named pipe:
  3/3 stored with 512-d embeddings, DRC verdicts recorded, schema v3, **113 MB working set**. `adapt.exe` ran a full
  learning cycle (370 labels, LightGBM trained, deploy gate kept the incumbent) and saved its version under the data folder.
- Staged dashboard: 126 MB while open, exits on close, tray 5 MB, nothing written into the install folder.
- `build_bundle.ps1 -StageName <name>` builds beside a running install (the default folder is locked while the app runs).
- NOT done: first-run consent screen, code signing, MobileCLIP licence check, Start/Stop capture tested from the installed
  app (it would record the desktop), and the collector still reads `collector.config.json` from its install folder (read-only
  there: an exclusions editor must write to the data folder instead).

## DRC history merged into the live database (2026-10-21)

`python-worker/merge_drc_into_live.py` copies DesktopResearchCollector history into the live `context.db`, keeping only DRC
captures **older than the first live record** (the overlap would double-count the same moments). Dry run by default;
`--apply` first writes a SQLite-backup copy (`backups\pre-drc-merge-*.db`), then replays (same exclusions as `drc_replay`),
embeds with the ONNX encoder and inserts. The cutoff is stored in a one-row table `record_sources`
(`ts_before`); records with `ts < ts_before` are DRC history, so the dashboard can show live and history apart
(Importance engine scope buttons, Live feed source filter, Overview "Data sources"). Re-running keeps the same cutoff and only
adds what is missing. Merged 2026-10-21: 5,049 DRC records (08 to 19 Oct, all with embeddings), 1,016 overlapping DRC captures dropped.
Their labels are NULL until the learning job runs. Tests: `tests/test_merge_drc.py`, `UI/ContextDashboard.Tests/DataScopeTests.cs`.
