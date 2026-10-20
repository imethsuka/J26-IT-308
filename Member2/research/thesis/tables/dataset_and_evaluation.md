# Table set for thesis draft

## Table 1. Annotation distribution

| Label | Count | Percentage |
|---|---:|---:|
| LOW | 38 | 76% |
| MEDIUM | 10 | 20% |
| HIGH | 2 | 4% |
| CRITICAL | 0 | 0% |
| **Total** | **50** | **100%** |

Source: `backend/data/training_log.json`; percentages calculated from 50 rows.

## Table 2. Recorded classifier comparison

| Classifier | Accuracy | Recorded macro F1 | Test rows |
|---|---:|---:|---:|
| Majority-class baseline | 0.80 | 0.4444 | 10 |
| Rule baseline | 0.80 | 0.4444 | 10 |
| Random Forest | 0.80 | 0.4444 | 10 |

Source: `backend/data/experiment_results.json`. The comparison is not evidence
of superiority. The test fold contains no HIGH examples.

## Table 3. Synthetic and regression validation

| Validation scope | Evidence | Result |
|---|---|---:|
| Original privacy/tokenization evaluation | `privacy_eval_results.json` | 26/26 passed |
| Stage 2 focused detection tests | Test run 2026-10-20 | 82 passed |
| Stage 3 focused redaction/API tests | Test run 2026-10-20 | 68 passed |
| Full backend suite after Stage 3 | Test run 2026-10-20 | 404 passed, 1 warning |

These are engineering and regression results, not estimates of real-world
privacy effectiveness.
