# CAPRLM — Context-Aware Privacy Risk Learning Model

Member 2's component (W.L.S.A. Wijesingha). Privacy detection, risk prediction,
protection, and tokenization for context PCCILM has decided to acquire.

```
CAPRLM/
  shared/     Interface contract + rule-based baseline + dev pass-through + contract
              tests — joint infrastructure both members rely on, not research content.
  docs/       PRIVACY_HANDOFF.md — the one-pager spec for the interface above.
              BUILD_LOG.md — what was built, run commands, defaults vs. open decisions.
  research/   Detection, risk model, baselines, evaluation harness and the local API the
              comparison UI (../UI/CAPRLMComparison) talks to. Runs on a PLACEHOLDER
              dataset until the label protocol is decided.
```

See `docs/PRIVACY_HANDOFF.md` for the interface itself and the fail-closed rules.
See `docs/BUILD_LOG.md` for how to run everything, and `research/README.md` for which
research decisions are still open (all five are).
