"""The Privacy Review engine: one captured text -> per-entity decisions the UI can show and correct.

Same detection and risk model as CAPRLMFilter, but it keeps what the filter's audit trail
deliberately drops — each entity's risk score, offsets and reason — because the review screen
needs them. That detail goes only to the local UI; it is never logged or stored (the profile
store keeps counts and (category, decision) pairs only).

Decision order per entity: your override for this entity > an enforced category policy > a rule
learned from your earlier corrections > the model (risk >= tau).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from shared.types import PrivacyContext

from research.caprlm_filter import DEFAULT_TIER_ACTIONS
from research.detection import Detector
from research.model import RiskModel
from research.model.explain import explain_pairs
from research.profile_store import GROUPS, LEVELS, ProfileStore, group_of

log = logging.getLogger(__name__)

FLAG_RISK = 0.5            # an entity at/above this risk counts as "flagged" even if left visible
POLICY_ACTIONS = {"never": "drop", "tokenize": "tokenize", "mask": "mask"}


def level_taus(model: RiskModel) -> dict[str, float]:
    """Risk threshold per protection level. An entity is masked when risk (= 1 - P(none)) >= tau.

    Balanced is the evaluated model's own operating point: its none-threshold t means "visible
    only if P(none) >= t", i.e. masked when risk > 1 - t. Strict halves that; relaxed raises it
    to at least 0.5. If the model has no none-threshold (plain argmax) balanced is 0.5."""
    t = model.config.none_threshold
    balanced = round(1.0 - t, 2) if t is not None else 0.5
    return {"relaxed": round(max(0.5, balanced + 0.25), 2), "balanced": balanced,
            "strict": round(balanced / 2, 2)}


@dataclass
class _Decision:
    masked: bool
    action: str          # pass_through | tokenize | mask | drop
    by: str              # model | policy | learned | override
    reason: str


def _decide(category: str, risk: float, tier: str, eid: str, *, tau: float, policy: dict,
            rules: dict, overrides: dict[str, str]) -> _Decision:
    pretty = category.replace("_", " ").lower()
    group = group_of(category)
    protected_action = DEFAULT_TIER_ACTIONS.get(tier, "mask")
    if protected_action == "pass_through":
        protected_action = "mask"

    if eid in overrides:
        if overrides[eid] == "masked":
            return _Decision(True, protected_action, "override", "Masked by you")
        return _Decision(False, "pass_through", "override", "Kept visible by you")
    if group and policy["groups"][group]["enabled"]:
        act = POLICY_ACTIONS[policy["groups"][group]["action"]]
        return _Decision(True, act, "policy", f"Policy · {GROUPS[group][0]} · {policy['groups'][group]['action']}")
    if category in rules:
        rule = rules[category]
        n = rule["evidence"]
        if rule["decision"] == "masked":
            return _Decision(True, protected_action, "learned", f"Learned from {n} of your corrections · mask {pretty}")
        return _Decision(False, "pass_through", "learned", f"Learned from {n} of your corrections · allow {pretty}")
    if risk >= tau:
        return _Decision(True, protected_action, "model", f"risk {risk:.2f} ≥ τ {tau:.2f} · {protected_action} {pretty}")
    return _Decision(False, "pass_through", "model", f"risk {risk:.2f} < τ {tau:.2f} · allow {pretty}")


def review(text: str, ctx: PrivacyContext, *, detector: Detector, model: RiskModel,
           store: ProfileStore, level: str | None = None, overrides: dict[str, str] | None = None) -> dict:
    policy = store.policy()
    level = level or policy["level"]
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    taus = level_taus(model)
    tau = taus[level]
    rules = store.learned_rules()
    overrides = overrides or {}

    cands = detector.detect(text)
    pairs = [(c, ctx) for c in cands]
    scores = model.risk_scores(pairs)
    # the model's own reasons for each score, from its learned weights; None when the model is not a linear one
    why = explain_pairs(model, pairs, [t for _, t in scores]) or [[] for _ in cands]

    entities: list[dict] = []
    replacements: list[tuple[int, int, str, str]] = []   # start, end, replacement, kind
    token_ids: dict[tuple[str, str], int] = {}
    for c, (risk, tier), reasons in zip(cands, scores, why):
        eid = f"{c.start}:{c.end}"
        d = _decide(c.category, risk, tier, eid, tau=tau, policy=policy, rules=rules, overrides=overrides)
        token = None
        if d.masked:
            if d.action == "tokenize":
                key = (c.category, c.surface(text))
                if key not in token_ids:
                    token_ids[key] = 1 + sum(1 for k in token_ids if k[0] == c.category)
                token = f"[{c.category}_{token_ids[key]}]"
                replacements.append((c.start, c.end, token, "token"))
            elif d.action == "mask":
                token = "[REDACTED]"
                replacements.append((c.start, c.end, token, "mask"))
            else:
                replacements.append((c.start, c.end, "", "dropped"))
        entities.append({
            "id": eid, "category": c.category, "group": group_of(c.category),
            "risk": round(risk, 3), "tier": tier, "masked": d.masked, "action": d.action,
            "decided_by": d.by, "reason": d.reason,
            # The original value is returned only for entities left visible (it is already in
            # the output); a masked entity travels as its token alone.
            "token": token, "value": None if d.masked else c.surface(text),
            "why": reasons,
        })

    redacted_text, segments = _render(text, replacements)

    masked_n = sum(e["masked"] for e in entities)
    flagged_n = sum(e["masked"] or e["risk"] >= FLAG_RISK for e in entities)
    keep = 1.0
    for e in entities:
        if not e["masked"]:
            keep *= 1.0 - e["risk"]
    nonspace = sum(not ch.isspace() for ch in text) or 1
    masked_chars = sum(sum(not ch.isspace() for ch in text[s:t]) for s, t, _, _ in replacements)
    utility = max(0.0, 1.0 - masked_chars / nonspace)

    llm = policy["llm"]
    outbound_text = redacted_text if llm["mask_before_send"] else text
    return {
        "level": level, "tau": tau, "taus": taus,
        "entities": entities,
        "redacted_text": redacted_text, "segments": segments,
        "residual_risk": round(100 * (1.0 - keep)),
        "flagged": flagged_n, "masked": masked_n, "total": len(entities),
        "utility_kept": round(utility, 3),
        "outbound": {
            "mask_before_send": llm["mask_before_send"], "provider": llm["provider"], "model": llm["model"],
            "connected": bool(llm["provider"]), "text": outbound_text,
            "masked": masked_n if llm["mask_before_send"] else 0, "total": len(entities),
        },
        "filter_version": model.version,
    }


def _render(text: str, reps: list[tuple[int, int, str, str]]) -> tuple[str, list[dict]]:
    out: list[str] = []
    segs: list[dict] = []
    pos = 0
    for start, end, repl, kind in sorted(reps, key=lambda r: r[0]):
        if start > pos:
            out.append(text[pos:start])
            segs.append({"text": text[pos:start], "kind": "plain"})
        out.append(repl)
        segs.append({"text": repl, "kind": kind})
        pos = end
    if pos < len(text):
        out.append(text[pos:])
        segs.append({"text": text[pos:], "kind": "plain"})
    return "".join(out), segs
