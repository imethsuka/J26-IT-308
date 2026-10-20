# Appendix A — Reproducibility and review checklist

## Commands

From `C:\Users\ASUS\OneDrive\Desktop\J26-IT-308\Member2`:

```powershell
.venv\Scripts\python.exe -m pytest backend\tests --tb=short -q
.venv\Scripts\python.exe -m backend.services.baseline_eval
.venv\Scripts\python.exe -m backend.services.train_model
```

The training command writes a model and therefore must not be run against the
protected artifact during review without an explicit backup and supervisor
decision. The recorded thesis results use the existing `training_log.json` and
`experiment_results.json`; the original model and CSV were not retrained or
modified while preparing this draft.

## Protected evidence

- `backend/data/CAPRLM_annotation_50.csv`
- `backend/data/caprlm_model.joblib`
- raw screenshots and OCR outputs containing potentially sensitive content

## Before submission

1. Confirm the official university template and required reference style.
2. Obtain supervisor approval for the research questions and contribution
   wording.
3. Complete human review of all 16 flagged annotation rows.
4. Decide whether the current 50-row dataset may be used as final ground truth.
5. Recompute model metrics after any approved annotation corrections.
6. Add a documented second annotator or explicitly justify its absence.
7. Replace provisional live-environment and AI-review statements with signed
   laboratory records where required.
