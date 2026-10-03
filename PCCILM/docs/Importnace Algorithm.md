Here's the algorithm, concretely. I'll call it **KW (Keep-Worthiness)**. The key structural point first:

> **KW is the teacher, Model 1 is the student.** KW looks at what happened *after* a capture (the future) to decide how important it was. Model 1 learns to predict that score from what's visible *at the moment* of capture. It replaces only the label (today's "came back within 48 h"); Model 1, the features and the training loop stay the same.

## Definitions
For a capture *x* at time *t*, with context key *k* = (app, domain, normalised title), visits and sessions as today, and a look-ahead window *H* = 48 h (with the same censoring as today):

### 1. Need, from returning (*N*)
Sooner and more frequent returns mean stronger need:

$$N(x) = \min\Big(1,\ \sum_{j \in \text{later visits of } k \text{ within } H} e^{-\Delta_j/\tau_N}\Big), \qquad \tau_N = 24\text{ h}$$

Δⱼ is the time until each later visit. Returning after 1 hour counts more than after 40 hours, and returning three times counts more than once. Today's label is just "≥ 1 return → 1".

### 2. Attention → Action (*A*)
Did looking at it lead to work?

$$A(x) = \begin{cases} 0 & \text{if active dwell of the visit} < d_{\min} = 10\text{ s} \\ 1 - e^{-\max(0,\ K_{\text{after}} - \bar K)/\kappa} & \text{otherwise} \end{cases}$$

- *K_after* = key presses in **other** contexts within *W* = 10 min after leaving *x*;
- *K̄* = your **usual** typing in that 10 minutes, at that hour of day (the baseline);
- κ = 200 key presses.

Only typing **above your normal level** counts, so a heavy typist doesn't make everything look important.

### 3. Reuse (*U*), when screen text is available
Did content from *x* flow into your work?

$$U(x) = 1 - e^{-\big(\sum_{g \in \text{reused}} \text{idf}(g)\big)/\rho}$$

- "Reused" = distinctive word-runs (identifiers, numbers, URLs, names: rare across *your* text, hence high idf) that **first appeared on screen in *x*** and **later appear in a context where you typed**, within *H*.
- ρ is a scale constant.
- If text isn't available, *U* is **missing** and drops out (the same rule as retrieval today).

### 4. Loss: how hard is it to find again? (*L*)
Let *r* be the re-findability, combined from independent cues:

$$r(x) = 1 - (1 - r_{\text{familiar}})(1 - r_{\text{own}})(1 - r_{\text{stable}})$$

- *r_familiar* = 1 − e^(−v/2), with *v* = your earlier visits to *k*: often visited means easy to find;
- *r_own* = 0.8 if it's your own file (editor, Office or Explorer with a file title): findable through the file system;
- *r_stable* = 0.5 for a stable web page (URL without search/session parameters).

Ephemeral sources (search results, social feeds, chat, notifications, content that changed between visits) halve *r*. Then:

$$L(x) = 1 - r(x)$$

### Combination
$$\text{Need}^*(x) = 1 - (1-N)(1-A)(1-U)(1-\text{retrieval}) \qquad \text{(any evidence of need suffices: "noisy-OR")}$$

$$\boxed{\ KW(x) = \text{Need}^*(x)\,\cdot\,\big(\lambda + (1-\lambda)\,L(x)\big)\ }, \qquad \lambda = 0.3$$

The floor λ = 0.3 keeps important-but-easy-to-find things from dropping to zero; they're just ranked below important-and-hard-to-find ones. Missing signals drop out. Member 3's retrieval feedback slots in later. KW is a score from 0 to 1. Model 1 can learn it directly as a ranking target, or after a threshold.

## Pseudocode
```
KW_LABELS(captures, now):
  T ← Timeline(captures, session_gap=30min, horizon=48h, title_normalizer)   # as today
  B ← typing baseline per hour of day (mean keys per 10 min, from all data)
  IDF ← token rarity over all captured text (if text available)
  for each capture x due and uncensored:
     N ← min(1, Σ exp(-Δ_j/24h) over later visits j of k(x) within 48h)
     if active_dwell(visit(x)) < 10s: A ← 0
     else: A ← 1 - exp(-max(0, keys_in_other_contexts(10min after visit) - B[hour]) / 200)
     U ← reuse_score(x, IDF)  or MISSING
     r ← 1 - (1-familiar(x))(1-own_file(x))(1-stable_url(x));  if ephemeral(x): r ← r/2
     L ← 1 - r
     need ← 1 - Π(1 - s) over available s in {N, A, U, retrieval}
     KW(x) ← need · (0.3 + 0.7·L)
  return KW scores (+ each component, stored separately)
```

## How it's validated (what makes it research)
1. Hand-label about 200 captures ("would I want to find this again?").
2. Compare **label quality**: which agrees better with your hand labels, today's revisit label or KW? Measure with AUC.
3. **Ablation:** N alone (≈ today) → N × L → (N or A) × L → full KW. This shows which idea adds what.
4. Train Model 1 on each label and compare **recall of your hand-labelled important captures under a 30% storage budget**.

If KW agrees better with you and the trained model keeps more of what you care about, that's your contribution: **a better self-supervised definition of memory importance, with evidence.**

## Honest notes
- The constants (24 h, 10 min, 200 keys, 0.3, 0.8, 0.5) are **starting guesses**. Report them, test sensitivity, and ideally fit them to your hand labels.
- The pieces rest on known ideas: survival-style decay, noisy-OR evidence combination, idf, re-finding research. **The KW formulation for deciding desktop memory capture is plausibly new**, but needs a literature check before you claim it.
- N, A and L work on your existing 4 weeks of data. U needs the new screen-text data.

Should I add KW to `THESIS_NOTES.md` and implement it as an opt-in label in `offline/` (like v4), with a notebook cell comparing it with the current labels? The full validation needs your ~200 hand labels.
