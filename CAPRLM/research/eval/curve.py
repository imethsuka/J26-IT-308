"""How the protection level trades precision against recall, and how well each kind of entity is caught.

Computed from the model's own scores on the held-out test scenarios, over the spans the detectors find: for each risk threshold, the
span is "protected" when its risk (1 - P(none)) is at or above it; the truth is whether the labelled tier is above "none". This is the
risk model's decision on its own, so detection misses (spans no detector finds) are not in these numbers; that coverage is reported
separately in results.json. The data is the same small, unaudited, LLM-labelled set the Metrics charts use.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.data.placeholder_scenarios import Scenario
from research.detection import Detector
from research.model import RiskModel
from research.model.training import build_pairs

TAUS = tuple(round(t, 2) for t in (0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95))


def _scores(tp: int, fp: int, fn: int, total: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "protected_share": round((tp + fp) / total, 3) if total else 0.0}


def build_curve(model: RiskModel, detector: Detector, scenarios: list[Scenario], presets: dict[str, float]) -> dict[str, Any]:
    pairs, tiers = build_pairs(scenarios, detector)
    risks = [r for r, _ in model.risk_scores(pairs)]
    positive = [t != "none" for t in tiers]
    total = len(pairs)

    def at(tau: float, rows=None) -> dict[str, float]:
        idx = range(total) if rows is None else rows
        tp = sum(1 for i in idx if risks[i] >= tau and positive[i])
        fp = sum(1 for i in idx if risks[i] >= tau and not positive[i])
        fn = sum(1 for i in idx if risks[i] < tau and positive[i])
        return _scores(tp, fp, fn, len(idx) if rows is not None else total)

    points = [{"tau": t, **at(t)} for t in sorted(set(TAUS) | set(presets.values()))]

    by_cat: dict[str, list[int]] = defaultdict(list)
    for i, (cand, _) in enumerate(pairs):
        by_cat[cand.category].append(i)
    balanced = presets.get("balanced", 0.5)
    categories = []
    for cat, rows in by_cat.items():
        sensitive = sum(1 for i in rows if positive[i])
        if sensitive == 0:
            continue
        s = at(balanced, rows)
        categories.append({"category": cat, "spans": len(rows), "sensitive": sensitive, "recall": s["recall"], "precision": s["precision"]})
    categories.sort(key=lambda c: (-c["sensitive"], c["category"]))

    return {"n_spans": total, "n_sensitive": sum(positive), "presets": presets, "points": points, "categories": categories,
            "basis": "spans the detectors find, held-out test scenarios"}
