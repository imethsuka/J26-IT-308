# CAPRLM Claims Register

| ID | Claim | Evidence | Evidence type | Verification | Limitation | Thesis use |
|---|---|---|---|---|---|---|
| C01 | CAPRLM processes screenshots through OCR, entity detection, feature extraction, risk prediction, tokenization, protection selection, and API response generation. | `backend/main.py`, `ocr_service.py`, related services | Implementation | Verified by source and tests | OCR quality is not independently benchmarked here | Allowed |
| C02 | Sensitive detection is deterministic regex matching, not trained NER. | `sensitive_detection.py`, `ner_service.py` | Implementation | Verified | Pattern coverage is limited | Allowed |
| C03 | Contextual confidence values are heuristic scores layered on regex matches. | `ner_service.py` | Implementation | Verified | Not calibrated probabilities | Allowed |
| C04 | The annotation dataset contains 50 rows: LOW 38, MEDIUM 10, HIGH 2, CRITICAL 0. | `training_log.json`, CSV | Measured artifact | Verified | Single annotator; unresolved anomalies | Allowed with caveat |
| C05 | The RF test fold contains 40 training and 10 test rows; HIGH support in the test fold is zero. | `training_log.json`, `experiment_results.json` | Measured artifact | Verified | A single split cannot estimate generalisation robustly | Allowed |
| C06 | RF, rule, and majority baseline have identical reported accuracy 0.80 and macro F1 0.4444 in the recorded comparison. | `experiment_results.json`, `baseline_eval_log.json` | Measured artifact | Verified | The reported 0.4444 is the project's comparison value; class support and macro definition must be stated | Allowed, not as superiority |
| C07 | The model demonstrates reliable privacy-risk classification. | No supporting evidence | Unsupported | Not verified | Small, imbalanced, anomalous dataset and no HIGH test examples | Prohibited |
| C08 | Pixel redaction is implemented using PIL Gaussian blur, all-text blur, and block actions. | `redaction.py`, tests, traceability matrix | Implementation/test | Verified | Synthetic tests do not establish real-world effectiveness | Allowed |
| C09 | Stage 2/3 synthetic tests prove real-world privacy protection. | No supporting evidence | Unsupported | False | Synthetic fixtures do not model OCR errors or adversarial screenshots | Prohibited |
| C10 | Incomplete entity mapping triggers API fail-safe block or HTTP 503 on fallback failure. | `main.py`, `test_api_integration.py` | Implementation/integration test | Verified | Authentication and deployment controls are outside scope | Allowed |
| C11 | OCR text is not intentionally persisted in `analysis_log`, and token maps are not returned. | `database.py`, `main.py`, privacy tests | Implementation/test | Verified | Does not prove no process-memory or infrastructure leakage | Allowed carefully |
| C12 | Post-OCR non-OCR pipeline mean latency is 6.566 ms in a 50-run synthetic benchmark. | `latency_results.json` | Measured artifact | Verified | Excludes EasyOCR and is environment-specific | Allowed with scope |
| C13 | EasyOCR is the production bottleneck at approximately 30–90 s per image on CPU. | `latency_results.json`, traceability matrix | Measured artifact | Partly verified | Measurement context/hardware should be recorded before final submission | Allowed as provisional |
| C14 | Annotation quality is established. | No second annotator; 16 anomalies unresolved | Missing evidence | False | Human review and agreement are absent | Prohibited |
| C15 | The system is production-ready or provides a security guarantee. | No supporting deployment/security audit | Unsupported | Not verified | No authentication; prototype storage and threat boundaries remain | Prohibited |
| C16 | The contribution is a complete adaptive privacy solution. | Implementation plus limitations | Interpretation | Partly supported | Adaptation is threshold recalibration, not per-user model retraining; persistent restoration absent | Use cautious wording |
| C17 | The work contributes an auditable prototype combining classification, adaptive selection, tokenization, and pixel redaction. | Source, tests, evaluation artifacts | Synthesis | Supported at prototype level | Novelty relative to literature and other group components needs supervisor review | Allowed as provisional contribution |

## Claim policy

Claims marked **Prohibited** must not appear as factual conclusions. They may
appear only in a limitations section as hypotheses that were not established.
The current draft uses “prototype”, “measured on the recorded split”, and
“synthetic engineering validation” deliberately.
