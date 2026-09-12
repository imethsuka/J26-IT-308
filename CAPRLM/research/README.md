# CAPRLM research — Wijesingha's component

The pipeline now runs end to end (detection → risk model → protection, compared against
four baselines — see `../docs/BUILD_LOG.md`). **Nothing below is ticked as a research
decision.** Where a default had to be picked to get it running, the item says so and points
at the code; those defaults are Wijesingha's to confirm, replace, or reject.

Checkbox key: `[ ]` open · `[~]` a placeholder/default is implemented, decision still open.

- [~] Label protocol for risk ground truth (constructed scenario-pairs, grounded in
      Contextual Integrity + an entity-type prior from GDPR/Presidio-style categories —
      see the conversation for the full reasoning, not repeated here)
      — *default:* a hand-written (category × context-kind) tier table,
      `research/data/placeholder_labels.py:37` (`gold_tier`). Deterministic, so any model
      that sees category and context learns it; it exists to exercise the pipeline.
- [~] Model choice (Random Forest vs. LightGBM vs. other) — decide empirically by
      training both against the rule-based baseline in `../shared/rule_based_filter.py`
      — *default:* logistic regression vs. random forest chosen by cross-validated
      macro-F1, simplest wins unless beaten by 0.02 — `research/model/risk_model.py:159`
      (`select_model`). Regularisation, class weights and a *none-threshold* (protect any span
      not confidently "none", tuned for F2 on out-of-fold predictions — a privacy-first default)
      are tuned the same way, on training data only. Logreg currently wins. LightGBM is not
      tried. The empirical decision still needs real, audited labels.
- [~] Dataset construction (your own scenario templates, not PCCILM's DRC corpus)
      — *default:* template generator with disjoint train/test value pools,
      `research/data/placeholder_scenarios.py:172` (`generate`). Output is tagged
      `"dataset": "placeholder-not-for-reporting"`.
- [~] Reversible tokenization / key-store design, if any category needs it
      — *default:* only the `pseudonymization` baseline is reversible, via an in-memory,
      per-session store kept inside `research/` — `research/baselines/key_store.py:11`.
      CAPRLM's own `tokenize` action (`[EMAIL_1]`) is not reversible and has no store.
      Whether any category should be reversible, and where a key would live, is open.
- [~] Leakage/re-identification evaluation method, now that the adversarial-attack
      simulation is out of scope — needs a stated replacement, even a lighter one
      — *default:* exact-string leakage + a token-level "re-identification" check,
      `research/eval/metrics.py:31` (`identifying_tokens`) and `:69` (`evaluate_filter`).
      A crude lexical attacker, not a model-based one.

New items surfaced while building (not in the original list):

- [ ] Personalization / continual adaptation. CAPRLM's premise is risk "personalized per
      user and adapted over time", but **no personalization mechanism exists** — the model
      is a single global classifier, and results.json reports `"personalization": null`
      rather than a before/after number. What signal, and how it updates, is undecided.
- [~] Detection-stage defaults: spaCy `en_core_web_sm` (not `_trf`) and a constant NER
      confidence of 0.8 because spaCy exposes no per-entity score —
      `research/detection/ner_detector.py:16`, `:31`. The `sm` model misses some money
      formats and mislabels some names; swap with `CAPRLM_SPACY_MODEL=en_core_web_trf`.
- [~] Tier → protection action (`none→pass_through, low→tokenize, medium→mask,
      high→drop`) — `research/caprlm_filter.py:24`. The research question is the tier;
      this mapping is a configurable default.
- [~] Context features: domain/title are reduced to ten keyword buckets by hand —
      `research/model/features.py:16`. Whether to learn this instead is open.
- [ ] Semantic similarity (utility) is currently a lexical TF-IDF cosine proxy, not an
      embedding-based score. Pick an embedding model if "semantic" must be literal.

Nothing in `../shared/` assumes any of these decisions — it only assumes you'll hand
back something that implements `PrivacyFilter` from `../shared/privacy_filter.py`.
