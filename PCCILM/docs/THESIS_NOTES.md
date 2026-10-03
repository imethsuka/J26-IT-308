# Thesis notes: PCCILM (Member 1)

Reference material for writing the thesis: the algorithms (formal + pseudocode), the value framing,
every result measured so far with where it came from, design decisions, limitations, and the
mapping to the charter. Numbers are copied from actual runs (dates and sources given). Re-run before
final submission, since more data will change them.

**Project:** J26-IT-308, *Adaptive Privacy-Preserving Cognitive Memory System for Personalized Desktop
Intelligence*. **Member 1 sub-objective:** *Personalized Continual Context Importance Learning
Framework for Adaptive Desktop Memory Acquisition*: the **PCCILM**.

---

## 1. Problem statement (for the introduction)

A desktop memory system can't keep everything: continuous capture produced **8,519 screenshots
(≈ 92 KB each) in 18 days for one participant** (20 Sep – 8 Oct 2026, DRC data), most of them
low value. Keeping everything costs storage and indexing, and buries useful memories. PCCILM decides **at
the moment of observation** whether a desktop context is worth committing to long-term memory, and
**in what form**. It learns what matters to *this* user from their later behaviour, without manual labels.

## 2. What counts as a successful memory (definition)

> A memory is valuable to the extent that it **helps the user when it is needed**: recovering a
> detail they saw, or resuming a task they left. Behaviour (revisits, continuation, retrieval) is
> **evidence** of that value, not the definition of it.

This separates the **target** (usefulness when needed) from the **observable proxies** used to learn
it. It's the main framing borrowed from the "Personalized Memory Value" (PMV) discussion, kept inside
PCCILM rather than introduced as a new model name, so the charter terminology stays intact.

### 2.1 Value decomposition (PMV framing)

For a candidate memory *x*, three kinds of future value:

| Symbol | Value | Evidence available | Source |
|---|---|---|---|
| **R(x)** | information **recovery**: the user later needs a detail from it | later retrieval/use of the memory | Member 3 (R-ACFR) retrieval events, not yet available |
| **T(x)** | **task resumption**: the user returns to the task | continuation in a later session | activity timeline (implemented) |
| **P(x)** | **personal** importance, incl. things never revisited | explicit ratings / hand labels | hand-labelling tool (planned, §9.3) |

Combined value (normalised, first version):

$$V(x) = w_R R(x) + w_T T(x) + w_P P(x), \qquad \textstyle\sum w = 1,\ w \ge 0$$

The weights are configurable; learning or justifying them is future work. **Status:** only T and
revisit evidence exist today. R depends on Member 3, and P on hand labels. The implemented label rule
(§3) is the special case of this with the evidence available now.

### 2.2 Cost-aware action choice

$$a^* = \arg\max_{a \in A}\ \big[\, \mathbb{E}[U \mid x, a] - C(a) \,\big]$$

with actions *A* = {discard, keep text only, keep text + image crops, keep full screenshot, update an
existing memory, defer}, and *C(a)* the storage/compute cost of each. **Privacy constraints override the
score:** excluded contexts are never sensed, whatever their value. The implemented counterpart is
the modality-selective policy (§7) plus the deferred tiering (§8.3).

---

## 3. Algorithm 1: delayed weak-supervision labels

**Code:** `python-worker/adaptation/label_construction.py` (`LabelConstructor`, `Timeline`,
`combine_importance_label`). Tests: `tests/test_label_construction.py`.

**Definitions.** Each capture *i* has timestamp *tᵢ* and context key
*k(i) = (app, domain, title)* (lower-cased; **v4**: title normalised, §3.2).
- **Session:** a maximal run of captures with gaps ≤ *g* = 30 min.
- **Visit:** a maximal run of consecutive captures (in time, across all contexts) with the same key
  within one session.
- **Horizon** *H* = 48 h; **delay** *D* = 48 h.

**Signals for capture i** (look only inside *(tᵢ, tᵢ + H]*):
- *revisit(i)* = 1 if some capture *j* with *k(j) = k(i)* lies in a **different visit** in that window;
- *continuation(i)* = 1 if such a *j* lies in a **later session**;
- *retrieval(i)* ∈ [0,1] or **missing**: from Member 3, injected callback (currently always missing).

**Combination rule** (weights *w_r* = 0.5, *w_v* = 0.3, *w_c* = 0.2, threshold θ = 0.5):

$$s(i) = \frac{w_v\,\text{revisit}(i) + w_c\,\text{cont}(i) + [\text{retrieval available}]\; w_r\,\text{retr}(i)}{w_v + w_c + [\text{retrieval available}]\; w_r}, \qquad y(i) = \mathbb{1}[s(i) \ge \theta]$$

A **missing** retrieval drops out of numerator and denominator (neither rewarded nor penalised).
Consequence: while retrieval is missing, *y = revisit* (revisit alone gives 0.6). Once retrieval exists,
revisit alone (0.3) is not enough.

**Censoring.** A capture is labelled only if *now − tᵢ ≥ D* **and** the data reaches *tᵢ + H*.
Otherwise it stays unlabelled. ⇒ **labels are immutable once written** (a re-run over more data gives
the same label), stamped `label_version` (e.g. `v3/h48`, `v4/h48`).

```
LABEL(captures, now):
  T ← Timeline(captures, session_gap=30min, horizon=48h)     # sessions + visits, oldest first
  for each capture i:
    if already labelled(i) or now − t_i < D or t_i + H > T.newest: continue   # due + uncensored only
    v ← ∃ j: k(j)=k(i), visit(j) ≠ visit(i), t_i < t_j ≤ t_i + H
    c ← ∃ j: k(j)=k(i), session(j) > session(i), t_i < t_j ≤ t_i + H
    r ← retrieval(i)  (or missing)
    y(i) ← combine(v, c, r) ;  stamp label_version
```

### 3.1 Why visits, not records
The collector sends one snapshot per screen change, so a single stretch in one window yields many
consecutive records with the same key. Counting records would mark almost everything "revisited".

### 3.2 Title normalisation (labels v4)
**Code:** `features/titles.py` (`normalize_title`); opt-in via `title_normalizer`.
Measured on 17,027 window titles (local DRC, 2026-10-19): Edge's `… and N more pages` suffix
(**6,179** titles) changes when *other* tabs open/close; invisible zero-width characters (**6,510**);
leading counters `(8)` (**700**); unsaved markers `●`/`*` (**80**). These hide real revisits.
v4 removes only these dynamic parts. **Effect (Neon data, 6,273 labelled):** 409 labels 0→1
(**400 of them Edge**), 4 labels 1→0; positive rate 59.3% → **65.8%**.

### 3.3 Known limitations of the label (state them)
- A one-off but useful item that is never revisited is labelled 0 (e.g. a product seen once).
- Habitual revisits of low-value contexts (an inbox) are labelled 1.
- It's a **behavioural proxy** (weak supervision / implicit feedback), not usefulness itself.
  Hand labels (§9.3) measure the gap.

---

## 4. Algorithm 2: features (the "senses") and replay

**Code:** `features/feature_assembly.py` (`assemble_features`), `models/vectorizer.py`
(`FEATURE_COLUMNS`, 33 columns); offline replay `offline/drc_replay.py`, `offline/training.py`.

At each capture, all signals are read **at the same moment** (sensor fusion):

| Group | Features (examples) |
|---|---|
| behavioural `beh_*` | key presses, clicks, scrolls in a trailing 10 s window; interactions/s; app switches and rate; distinct apps |
| temporal `tmp_*` | **dwell so far** in the current context; hour, sin/cos time of day; weekend; idle; time since last save |
| contextual `ctx_*` | app category (one-hot); title token count; seen before / revisit count / revisit ratio (in visits) |
| visual `vis_*` | similarity to nearest importance prototype; novelty vs recently saved embeddings; (experiment) 18 zero-shot screen-category probabilities |

**Replay of recorded data (DRC / Neon).** One capture per screenshot; the context = the latest window
event at/before it (**99.7%** agree with the screenshot's own app; trigger delay ≈ 0.07 s).
Important corrections, measured:
- DRC's `duration_ms` is the time spent in the **previous** window (15,424 of 15,463 events), so it's
  never used as dwell. Dwell is recomputed as time since the last context change.
- DRC writes input counts **per minute**, while the live collector uses a 10 s window, so counts are scaled.
- Exclusions applied again, fail closed. Neon replay: 8,519 screenshots → **6,969 captures**
  (dropped: 1,406 browser pages with unknown domain, 118 lock screen, 23 excluded domains, 3 no context).

**Visual features use train-only prototypes.** Prototypes = spherical KMeans (k = 8) over embeddings
of *important training* captures; `vis_proto_*` are **recomputed** against them for train and test.
Without this, every row has the cold-start similarity 0 and the model can't use vision at all.

---

## 5. Algorithm 3: Model 1 (the importance estimator)

**Code:** `models/lightgbm_model.py` (`ImportanceModel`). LightGBM binary classifier
(`num_leaves` 15, learning rate 0.05, 100 rounds, `min_data_in_leaf` 5). Output *p(x)* = estimated
probability the context is important. Decision: keep iff *p(x) ≥ τ* (τ = 0.5 by default; better chosen
for a **storage budget**, §6.3). Baselines: rule-based log-odds scorer (dwell ramp + revisit flag +
app category; `models/rule_based.py`), static dwell ≥ 30 s, keep-everything.

**Why LightGBM:** tabular features, small per-user data, CPU training in seconds, sub-millisecond
inference (measured 0.09–0.18 ms mean per decision), interpretable feature importance. Neural sequence
models would need far more data per user.

---

## 6. Algorithm 4: Model 2, the continual learning loop

**Code:** `adaptation/scheduled_job.py`, `deployment.py`, `drift_detection.py`, `input_monitor.py`,
`host_state.py`.

```
NIGHTLY (only when host idle AND on AC power):
  1. LABEL(new due records)                                   # §3
  2. prototypes ← KMeans(embeddings of important training captures)
  3. candidate ← train LightGBM on labelled data (time-ordered)
  4. drift ← ADWIN on the model's error stream (concept drift);
             input monitor on app mix / similarity (covariate drift, label-free)
  5. if metric(candidate, held-out) > metric(current, held-out): deploy candidate
     else keep current                    # strictly better; at cold start "current" = rule baseline
  6. log comparison; every version kept (append-only) → rollback possible
```

**Honest status of "continual":** today Model 1 is **retrained from scratch** each cycle (periodic
retraining). The charter promises *continual* learning, so the remaining work is an incremental/online
variant (LightGBM continued training or a River learner) compared against periodic retraining, with
prequential (test-then-train) evaluation over weeks, plus forgetting and drift recovery (§10).

### 6.1 Selection bias, and exploration (proposed)
Revisit/continuation labels exist for **discarded** contexts too (metadata is logged for every
non-excluded capture), so learning from them is unbiased. **Retrieval** feedback (Member 3) will exist
only for **kept** memories, so the model could never learn that a discarded item would have been useful.
**Fix:** keep an **exploration sample**: with probability ε (e.g. 5%), keep a context the model would
discard, and mark it as such. This makes the missed-value rate estimable (inverse-propensity weighting).
This is the one contextual-bandit idea worth adopting early; a full bandit policy only matters for
partial-feedback signals.

### 6.2 Store signals separately (proposed)
Store *revisit*, *continuation* and (later) *retrieval* per record, not only the merged label, so
separate R/T/P estimators (§2.1) or new weights can be tried without relabelling.

### 6.3 Evaluation protocol (implemented, use this in the thesis)
**Code:** `offline/training.py` (`temporal_split`, `rolling_evaluation`, `ranking_metrics`,
`summarize_folds`); `Colab/03_experiments_colab.ipynb`.
- **Chronological** splits only. **Embargo:** a training row whose label window *(t, t + 48 h]* reaches
  into the test period is dropped (its label would encode test-period behaviour). This explains why
  train + test < labelled (e.g. 3,516 + 1,882 < 6,273: 875 rows embargoed).
- **Rolling (expanding-window) evaluation:** the newest 50% of labelled data is cut into *n* = 4
  consecutive test periods; each trains only on earlier data. Report mean ± std and folds won.
- **Prototypes per fold** from that fold's training rows only.
- **Threshold-free metrics** as the primary comparison: ROC-AUC, average precision, and **recall at a
  storage budget** (share of important contexts kept when only the top 30% / 50% may be stored).
  **F1 at τ = 0.5 is misleading here:** the rules and LightGBM are calibrated differently, so at 0.5 the
  rules keep more and F1 rewards keeping more. (Observed: rules "won" F1 while ranking worse.)

---

### 6b. Model 2 learns the user: user profile and Keep-Worthiness (implemented 2026-10-19)

Model 2 now learns habits from the user's own records (`adaptation/user_profile.py`) and uses them two ways.

1. **A better teacher.** `adaptation/kw_labels.py` replaces "came back within 48 h" with a score,
   `KW = noisyOR(N, A, U, R) * (0.3 + 0.7 * L)`, where N = exponentially-weighted returns, A = typing above *this
   user's* usual for that hour after leaving, L = how hard the context is to find again, U (reuse) and R (retrieval)
   missing for now. Full definition: `Importnace Algorithm.md`. Stamped `kw1/h48`; opt-in (`--label-rule kw`).
2. **Personal features for Model 1.** `usr_keys_vs_usual`, `usr_app_return_rate`, `usr_app_return_hours`,
   `usr_hour_share`, `usr_transition_prob`: how unusual this moment is for this user. Offline, opt-in extra columns.

**Result so far (DRC replay, 3,848 labelled captures):** KW marks 24.1% positive against 52.9% for `v3`, agreeing on
71.1%; it turns 1,110 `v3` positives to 0 and 3 the other way. The score has a cliff at 0.30 to 0.35 (frequent, very
familiar contexts sit exactly on the floor), so the binary threshold must be chosen deliberately and reported.
**Not yet shown:** that KW labels agree better with the user's own judgement. That needs about 200 hand labels and the
ablation in `Importnace Algorithm.md` (N alone, N x L, (N or A) x L, full KW).

## 7. Algorithm 5: modality-selective acquisition (what form to keep)

**Plan:** `SCREEN_TEXT_PLAN.md` §2b, §4. **Principle:** *don't screenshot what can be read; keep images
only of what can't be put into words.*

| Situation (from the UI Automation read) | Keep |
|---|---|
| Model 1 says discard | nothing (metadata only, for labels; exploration sample excepted) |
| text read, no large images | **text only** (code, articles, documents) |
| text + large images (≥ 64 px per side) | **text + image crops** (product photos, charts, diagrams), cropped from the in-memory frame |
| little/no text but a visual screen (video, canvas, game, dashboard) | **full screenshot** |
| nothing exposed | full screenshot; MobileCLIP decides later |

Example (shopping page): text ≈ 3 KB + one product-photo crop ≈ 25 KB + URL, instead of a 92 KB
screenshot, and **searchable** both by text and by the picture (MobileCLIP text–image matching works
on photos, which is what it was trained on).

**Reading screen content (implemented, step 1):** `csharp-collector/src/CsharpCollector/Content/`.
Exclusions first; password fields and the field being typed into are never read; off-screen elements
and window chrome (menus, toolbars incl. address bar, tabs, scroll/status bars) are skipped.
**Measured (Edge page, i5-11400H, 2026-10-19):** the standard TextPattern visible-text call took
**2,168 ms**; walking the page element by element about 700 ms; a **single bulk request** with cached
properties: **median 221 ms, p95 278 ms** (8/8 reads, none truncated). About 14× faster. Runs off the
capture thread.

---

## 8. Algorithm 6: text merging and episodes (planned)

**Plan:** `SCREEN_TEXT_PLAN.md` §6–8.

### 8.1 One copy per page (overlap merge)
Each capture shows a window onto a longer text that loads/unloads while scrolling. Per document
(context key), keep ordered **fragments**. For a new capture *N*:
```
MERGE(document, N):
  index fragments by 8-word shingles (hash of each run of 8 consecutive words)
  F ← fragment sharing shingles with N; verify the overlap word by word
  if N ⊆ F: nothing new
  elif suffix(F) = prefix(N): append N's remainder        # scrolling down (works mid-sentence)
  elif suffix(N) = prefix(F): prepend                     # scrolling up
  elif F ⊆ N: replace F by N                              # more of the page loaded
  else: new fragment                                      # jumped far down
  join fragments that now overlap; never delete text that scrolled away
  tolerate ≤ 10% mismatched words in an overlap (live counters, small edits); min overlap 8 words
```
Linear in text length (well under 1 ms for a few KB). Output per capture: `new_words`, `new_ratio`
(reading progress, a candidate Model 1 feature).

### 8.2 Capture now, understand later
Real time (cheap): screenshot in RAM, window info, UIA read (~220 ms, async), Model 1 (< 1 ms) →
encrypted, time-limited **raw buffer**. Deferred (idle + AC): episodes → merge → Member 2 privacy
filter → embeddings / OCR on kept frames → local LLM entry → memory layer → buffer deleted.

### 8.3 Model 1 controls compute too
High importance: full processing (merged text, crops, embeddings, LLM summary). Medium: text +
metadata. Low: one metadata line. This gives a second measurable claim: **compute saved at a given
recall of important episodes**.

---

## 9. Results so far

### 9.1 Single split, Neon data (notebook 02, 2026-10-19)
Train 20–27 Sep (3,516 captures), test 29 Sep – 5 Oct (1,882), labels v3, τ = 0.5:

| Model | Precision | Recall | F1 | Keep rate | Storage saved |
|---|---|---|---|---|---|
| Rules (live baseline) | 0.68 | 0.84 | 0.75 | 0.65 | 32% |
| Static dwell ≥ 30 s | 0.60 | 0.33 | 0.43 | 0.29 | 68% |
| **LightGBM (PCCILM)** | **0.78** | 0.75 | **0.77** | 0.50 | **47%** |
| LightGBM without vision | 0.78 | 0.76 | 0.77 | 0.51 | 47% |
| Keep every screenshot (DRC) | 0.52 | 1.00 | 0.69 | 1.00 | 0% |

### 9.2 Rolling evaluation, Neon data (notebook 03, 2026-10-19). The main result
6,969 captures, 6,273 labelled, 20 Sep – 8 Oct; 4 test periods (26 Sep – 5 Oct, 784 rows each).
Mean ± std over periods.

**Labels v3 (exact titles):**

| Model | ROC-AUC | Avg precision | Recall @ keep 30% | Recall @ keep 50% | F1 @ 0.5 | Folds won (AP) |
|---|---|---|---|---|---|---|
| Rules | 0.666 ± 0.087 | 0.668 ± 0.127 | 0.344 ± 0.061 | 0.596 ± 0.040 | 0.755 | 0 |
| **LightGBM** | **0.816 ± 0.050** | **0.863 ± 0.058** | **0.488 ± 0.019** | **0.709 ± 0.018** | 0.762 | 1 |
| LightGBM w/o vision | 0.813 ± 0.051 | 0.860 ± 0.058 | 0.487 ± 0.038 | 0.699 ± 0.028 | 0.753 | 1 |
| LightGBM + screen categories | 0.817 ± 0.050 | 0.862 ± 0.060 | 0.489 ± 0.036 | 0.703 ± 0.023 | 0.764 | 2 |

**Labels v4 (normalised titles):**

| Model | ROC-AUC | Avg precision | Recall @ 30% | Recall @ 50% | F1 @ 0.5 |
|---|---|---|---|---|---|
| Rules | 0.649 ± 0.077 | 0.716 ± 0.088 | 0.346 ± 0.030 | 0.572 ± 0.034 | 0.782 |
| **LightGBM** | **0.781 ± 0.044** | **0.869 ± 0.051** | **0.438 ± 0.006** | **0.651 ± 0.019** | 0.767 |
| LightGBM w/o vision | 0.782 ± 0.042 | 0.870 ± 0.044 | 0.436 ± 0.020 | 0.651 ± 0.023 | 0.773 |
| LightGBM + categories | 0.774 ± 0.054 | 0.866 ± 0.054 | 0.440 ± 0.016 | 0.644 ± 0.021 | 0.765 |

**Thesis sentence (v3):** *Under a fixed 30% storage budget, PCCILM retained 49% of important contexts
versus 34% for the rule baseline (+42% relative), and ranked contexts better than the baseline in every
one of four consecutive test periods (mean AUC 0.82 vs 0.67).*
In every period, LightGBM's AUC and AP exceed the rules' (v3 per-fold AUC: 0.79/0.80/0.78/0.89 vs
0.68/0.59/0.61/0.78).

### 9.3 Findings to report
1. **PCCILM beats the rule baseline on ranking and under storage budgets, consistently**, and with less
   variance. F1 at 0.5 is roughly tied (calibration effect, §6.3).
2. **Vision (MobileCLIP) adds no measurable value for revisit-based labels**: with/without/categories
   all within ±0.01, far below between-period variation. Explanation: the label is computed from
   (app, domain, title), so title-derived features already carry what the screen's look would add
   ("Visual Studio Code" ≈ "looks like code"). Vision should be **re-tested against hand labels**, and on
   **image crops**, where its strengths (photos, products) apply. Zero-shot categories on UI screenshots
   were partly sensible (VS Code → code 810×, Explorer → files 360×) but noisy; "shopping" occurred 5×.
3. **Title cleanup corrects labels** (409 changed, 400 Edge); conclusions hold under both v3 and v4.
4. **Title length is the #2 input under both v3 and v4**, so it isn't a labelling artefact: short,
   stable titles (apps) vs long, one-off titles (web pages).
5. **Top inputs** (gain): revisit ratio (habit), title token count, prototype similarity, dwell,
   time of day, app-switch rate.

### 9.4 Latency and resources
| Component | Measured | Where |
|---|---|---|
| Screen-change probe (grab + 64×36 thumbnail + diff) | p50 31.3 ms, p95 41.8 ms | i5-11400H, 1920×1080, `benchmarks` |
| Model 1 decision | 0.09–0.18 ms mean | Colab CPU, rolling eval |
| MobileCLIP-S0 embedding | median 107 ms, p95 128 ms; peak RSS ≈ 1.25 GB | i5-11400H CPU, `eval/measure_encoder.py` |
| MobileCLIP-S0 embedding | median 185 ms, p95 213 ms | Colab CPU |
| Screen content read (UIA, bulk) | median 221 ms, p95 278 ms | Edge, i5-11400H (async) |

Implication: the **decision** is far below any interactive budget. Vision is the expensive part,
which supports moving it out of the real-time path (deferred, on kept crops only).

### 9.5 Pending results (to fill in)
- Hand-label agreement with the automatic labels (≈ 200 test captures), and all models graded on them.
- Transfer learning (general model on other consenting participants → fine-tuned per user) vs personal vs general.
- Continual (incremental) vs periodic retraining; prequential curve; drift recovery.
- Storage per episode under the modality-selective policy (§7) vs screenshots; compute saved by tiering (§8.3).
- UIA read latency across apps (Chrome, VS Code, Word, Explorer, a shopping page with images).

---

## 10. Mapping to the charter (Member 1 tasks)

| # | Charter task | Status | Evidence / where |
|---|---|---|---|
| 1 | C# observation + temporary buffering layer | ✅ | `csharp-collector/` (114 tests); screen-content reading step 1 |
| 2 | PCCILM from behavioural, visual, contextual signals | ✅ trained and evaluated | §4–5, §9.2; vision evaluated (finding 2) |
| 3 | Adaptive learning of evolving work patterns | 🟡 | Model 2 loop + drift detection (§6); needs evaluation over weeks |
| 4 | Self-supervised labels: revisit, task continuity, session duration, interaction | 🟡 | §3 (revisit, continuity); duration/interaction are features, not label inputs |
| 5 | Acquisition policy refined by retrieval feedback | ❌ (interface ready) | retrieval slot in the label rule (§3); exploration sample (§6.1); needs Member 3 |
| 6 | Compare vs rule-based, conventional ML, **transfer**, **continual** | 🟡 | rules + static + LightGBM done (§9); transfer & continual pending (§9.5) |

## 11. Design decisions (with reasons, for the methodology chapter)

- **Exclusion first, fail closed**; counts never content for keyboard/mouse; privacy constraints
  override any learned score.
- **Delayed weak supervision** instead of manual labels; bounded horizon + censoring ⇒ reproducible,
  immutable labels.
- **Visits, not records**, for revisits; **title normalisation** (v4) for dynamic titles.
- **LightGBM** over neural models: data size, CPU cost, interpretability.
- **Train-only prototypes** + recomputed visual features (avoid leakage; make vision usable at all).
- **Chronological, embargoed, rolling evaluation** with **threshold-free metrics**.
- **Strictly-better deployment gate**, append-only versions, rollback.
- **Modality-selective acquisition**: text where readable, image crops where not.
- **Capture now, understand later**: cheap real-time path, heavy work deferred to idle + AC.
- **Bulk UI Automation reads** (measured 14× faster than the standard text call).

## 12. Limitations and threats to validity

- **Single participant so far**, ~4 weeks; personalisation claims need more participants (with consent).
- **Proxy labels:** revisit ≠ usefulness (§3.3); hand labels and Member 3's retrieval feedback are the remedies.
- **Selection bias** for retrieval feedback (§6.1).
- **Label–feature coupling:** title-based labels favour title-based features, so vision is under-tested.
- **Weights** of the label rule / value combination are hand-set.
- **Replay vs live:** offline results come from replaying DRC data; the replay was checked identical
  between local SQLite and Neon (5,049/5,049 captures), but DRC's capture triggers differ from the live
  collector's screen-change trigger.
- **Windows-only**, primary monitor; Electron apps expose little text to UI Automation.

## 13. Glossary (for the reader)

| Term | Meaning here |
|---|---|
| Capture | one observation: a screenshot + the context at that moment |
| Context key | (app, domain, normalised title): "the same thing" |
| Visit / session | consecutive same-key captures / activity separated by > 30 min gaps |
| Weak supervision | labels from behaviour, not from manual annotation |
| Censoring | not labelling until the 48 h window is fully observed |
| Embargo | dropping training rows whose label window overlaps the test period |
| Prototype | cluster centre of embeddings of important captures |
| Recall @ keep X% | share of important contexts retained if only the top X% may be stored |
| Episode | a continuous stretch on one page/document, the unit of a memory entry |
