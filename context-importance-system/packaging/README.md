# Packaging

Builds one installable app: dashboard + tray, collector, frozen worker (no torch), and the ONNX MobileCLIP model.

```
packaging\stage\ContextImportance\
  ContextDashboard.exe (+ runtime)   window and tray
  collector\                         C# collector, self-contained
  worker\                            worker.exe (capture-time) and adapt.exe (Model 2 learning job)
  models\mobileclip_s0_image.onnx    image encoder, 43.5 MB
  caprlm\                            CAPRLM privacy service (frozen Python, Member 2) + Tesseract OCR
```

The dashboard is now the `UI\ContextDashboard\` project (sidebar shell: Memory pages from Member 1, Privacy pages from Member 2).
`build_bundle.ps1` publishes that one (the old `context-importance-system\dashboard\` copy was deleted).
On start the dashboard launches `caprlm\caprlm-service.exe` (loopback port 8787) by itself and stops it on exit. Capture (collector + worker)
is started only from Start capture or the setting *Also start capture when this app opens* (off by default: there is still no first-run consent screen).
`build_bundle.ps1 -SkipCaprlm` reuses `CAPRLM\packaging\dist\caprlm-service`; build it alone with `CAPRLM\packaging\build_service.ps1`.

## Build

1. Once, a clean build venv (no torch, so none can be bundled by accident):
   `py -3.13 -m venv packaging\.venv-build` then
   `packaging\.venv-build\Scripts\python.exe -m pip install numpy pandas scikit-learn lightgbm river pillow onnxruntime pyinstaller`
2. Once, export the encoder (dev venv, needs torch): `python -m features.onnx_export --checkpoint <mobileclip_s0.pt> --out %LOCALAPPDATA%\context-importance\mobileclip_s0_image.onnx`, then check it with `python -m eval.verify_onnx_encoder ...`.
3. `powershell -File packaging\build_bundle.ps1` (add `-SkipWorker` to reuse `packaging\dist\worker`).
4. Test the frozen worker: `python packaging\smoke_test_worker.py --dist packaging\dist --onnx <onnx path>` (uses a throw-away data folder).
5. Installer: install [Inno Setup 6](https://jrsoftware.org/isdl.php), then `iscc packaging\ContextImportance.iss` -> `packaging\installer\ContextImportance-Setup.exe`.

## Where things go when installed

- Program files: the install folder (per-user, `%LOCALAPPDATA%\Programs\ContextImportance`). Read-only use.
- User data: `%LOCALAPPDATA%\context-importance\` (`context.db`, `models\versions\`, `logs\`, `adaptation_runs.jsonl`, `dashboard.json`).
  A frozen worker (`app_paths.py`) and the dashboard both use it; nothing is written next to the program.
- No screenshots are ever written to disk.

## Before sharing the installer with anyone else

- **MobileCLIP licence:** the weights come from Apple's `ml-mobileclip`; check its licence permits redistributing the exported model.
- **Consent:** there is no first-run consent screen yet. Do not give this to another person until there is one.
  Screen text capture is separate and off by default; turning it on shows its own consent text (Screen text tab),
  and it adds `raw_buffer.db` (encrypted, 3 days) and `memory.db` to the data folder.
  After rebuilding, run `smoke_test_worker.py --dist dist --onnx <onnx> --screen-text`.
  Screen text capture is separate and off by default; turning it on shows its own consent text (Screen text tab).
  Check `smoke_test_worker.py --dist dist --onnx <onnx> --screen-text` after rebuilding.
- **Code signing:** unsigned, so Windows SmartScreen will warn.
- The shipped worker serves the **rule-based baseline** until a model is trained and deployed on that user's own data.
