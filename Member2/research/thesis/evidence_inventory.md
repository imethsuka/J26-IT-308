# CAPRLM Member 2 Evidence Inventory

**Project:** J26-IT-308 — Context-Aware Privacy Risk Learning and Management Framework  
**Workspace:** `Member2/research/thesis/`  
**Inventory status:** Review draft, generated 2026-10-20.  

This inventory distinguishes repository evidence from interpretation. Protected
artifacts were inspected read-only and were not copied into this thesis
workspace.

| Artifact | Type | What it proves | Limitations | Safe for thesis? |
|---|---|---|---|---|
| `README.md` | Project documentation | Intended architecture, setup, scope, known limitations, dataset summary | Documentation can become stale; source code is authoritative | Yes, with source cross-check |
| `backend/services/sensitive_detection.py` | Source | Deterministic regex entity detector and output schema | Pattern coverage is limited; no real-world recall estimate | Yes |
| `backend/services/ner_service.py` | Source | Contextual heuristic scores layered on regex matches | Not a trained NER model; scores are heuristic | Yes, carefully labelled |
| `backend/services/feature_extraction.py` | Source | Features supplied to the classifier | Feature representation is small and partly binary | Yes |
| `backend/services/train_model.py` | Source | Split, feature construction, Random Forest training procedure | Does not establish external validity | Yes |
| `backend/services/rule_baseline.py` | Source | Deterministic comparison baseline and rule rationale | Baseline shares the same limited features | Yes |
| `backend/services/baseline_eval.py` | Source | Same-fold majority, rule, and ML comparison | One 10-row test fold; no significance test | Yes |
| `backend/services/protection.py` | Source | Risk-to-action mapping and fail-safe fallback | Action selection is not equivalent to user study effectiveness | Yes |
| `backend/services/redaction.py` | Source | PIL blur, block, monitor actions, OCR-box mapping, statistics | Synthetic/unit validation does not prove OCR robustness | Yes |
| `backend/services/tokenizer.py` | Source | Per-request tokenization policy and ephemeral map | No persistent restoration; not cryptographic protection | Yes |
| `backend/services/cv_features.py` | Source | OCR-bounding-box layout features | Not fed into the trained RF model | Yes |
| `backend/main.py` | Source | FastAPI pipeline, response fields, temporary-file cleanup, in-memory redacted store | Prototype boundary; no authentication | Yes |
| `backend/database.py` | Source | SQLite schema and privacy-relevant persistence design | Does not prove deployment hardening | Yes |
| `backend/data/CAPRLM_annotation_50.csv` | Protected dataset | 50-row annotation schema and labels | Single annotator; 16 unresolved anomalies; do not reproduce sensitive rows | Aggregate statistics only |
| `backend/data/caprlm_model.joblib` | Protected model | Existing trained model artifact | Binary artifact is not independently interpretable | Metadata only |
| `backend/data/training_log.json` | Evaluation artifact | Model version, 40/10 split, CV and test metrics | Test fold contains no HIGH examples | Yes |
| `backend/data/experiment_results.json` | Evaluation artifact | Three-way baseline comparison and threshold sensitivity | Results are tied to one small split and current labels | Yes |
| `backend/data/baseline_eval_log.json` | Evaluation artifact | Append-only repeated baseline records | Repeated records are not independent experiments | Yes, as audit trail |
| `backend/data/detection_eval_results.json` | Evaluation artifact | 26 synthetic entity/tokenization cases, all passed | Synthetic fixtures are not representative OCR data | Yes, as engineering validation |
| `backend/data/privacy_eval_results.json` | Evaluation artifact | Six original entity categories tested across risk paths | Tests API tokenization, not visual privacy | Yes, with scope limit |
| `backend/data/latency_results.json` | Evaluation artifact | 50-run synthetic post-OCR timing benchmark | Excludes EasyOCR; hardware/environment specific | Yes |
| `backend/data/docs/final_validation_report.md` | Validation report | Prior integrated validation and limitations | Historical counts predate current Stage 2/3 totals | Use only as historical evidence |
| `backend/data/docs/requirements_traceability_matrix.md` | Traceability report | Requirement status, gaps, live/API evidence, unresolved annotation review | AI-generated; human/supervisor review still required | Yes, labelled provisional |
| `backend/data/docs/annotation_guidelines_DRAFT.md` | Draft protocol | Proposed label definitions and unresolved policies | Not supervisor-approved | Yes only as draft methodology |
| `backend/data/docs/annotation_review_report.md` | AI review | 16-row anomaly queue and provisional observations | Not human annotation decisions | Limitations only; do not treat as ground truth |
| `backend/data/docs/annotation_visual_review_2026-10-15.md` | AI review | Provisional visual observations for flagged rows | Requires human confirmation; sensitive screenshots not copied | Limitations only |
| `backend/tests/` | Regression tests | Implemented behavior and failure-path contracts | Passing tests do not measure population effectiveness | Yes |
| `backend/data/processing_output/manifest.json` | Pipeline artifact | OCR run status for 1,000 images | Raw screenshots are outside this thesis workspace | Aggregate count only |
| `backend/data/processing_output/candidates_deduped.csv` | Derived data | Deduplicated OCR candidate count | Contains OCR-derived content; do not reproduce raw rows | Aggregate count only |
| `PROJECT_CONTROL_CENTER.md` | Project control record | Stated objectives, protected files, unresolved decisions | Not a university-approved proposal | Internal traceability only |

## Template and approval check

No formal university thesis template, approved proposal, supervisor rubric, or
citation-style instruction was found under the repository root or `Member2`.
This draft therefore uses a provisional chapter structure and author-date
numeric bibliography placeholders. The title page, institutional formatting,
declaration, ethics approval wording, supervisor details, and final reference
style require confirmation before submission.

## Protected artifacts

The original annotation CSV and trained model were not modified. Raw screenshots,
OCR text, database contents, and redacted image outputs were not copied into
this workspace.
