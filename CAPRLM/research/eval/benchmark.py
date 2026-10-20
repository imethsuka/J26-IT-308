"""Benchmark: CAPRLM feature ablations and the baselines, over repeated k-fold splits of one
labelled dataset. The notebook CAPRLM/Colab/CAPRLM_benchmark_colab.ipynb drives this.

Questions it answers (and the experiment each maps to):
  1. Does context help?  -> ablations: category-only vs + app/domain/title vs + surrounding words
                            vs full, and "full minus app/domain/title".
  2. Is it better than the baselines, and by more than fold-to-fold noise?  -> mean +/- std over
                            folds, and the paired per-fold difference.
  3. Does it generalise to a context it never saw?  -> split="kind": each fold holds out one whole
                            context kind (banking, health, ...) from training.

Nothing is tuned on a test fold: regularisation / class weights / the none-threshold are chosen
inside each fold from its own training rows (research/model/risk_model.py).
"""

from __future__ import annotations

import random
from collections import defaultdict
from statistics import mean, pstdev

from shared.privacy_filter import PrivacyFilter

from research.baselines import AnonymizationFilter, PseudonymizationFilter, StaticMaskingFilter
from research.caprlm_filter import CAPRLMFilter
from research.data.placeholder_scenarios import Scenario
from research.detection import Detector
from research.model import ModelConfig, RiskModel
from research.model.features import ALL_GROUPS
from research.model.risk_model import select_model
from research.model.training import build_pairs
from shared.rule_based_filter import RuleBasedPrivacyFilter

from .metrics import evaluate_filter

# name -> feature groups. Order matters: it is the order of the ablation table.
ABLATIONS: dict[str, tuple[str, ...]] = {
    "CAPRLM: category only": ("base",),
    "CAPRLM: + source/app/domain/title": ("base", "source", "app", "context"),
    "CAPRLM: + surrounding words": ("base", "source", "app", "context", "window"),
    "CAPRLM: full (+ shape, doc)": ALL_GROUPS,
    "CAPRLM: full minus app/domain/title": ("base", "source", "window", "shape", "doc"),
}
FULL = "CAPRLM: full (+ shape, doc)"
BASELINES = ("rule_based", "static_masking", "anonymization", "pseudonymization")
METRICS = ("precision", "recall", "f1", "leakage_rate", "reidentification_rate", "semantic_similarity")


MIN_KIND_ROWS = 20   # a held-out kind smaller than this is too noisy to score (and may have no sensitive spans)


def make_folds(scenarios: list[Scenario], k: int, seed: int, split: str = "random",
               min_kind_rows: int = MIN_KIND_ROWS):
    """Yield (fold_name, train, test). split="random": shuffled k-fold by row.
    split="kind": leave-one-context-kind-out (k is ignored); kinds with fewer than
    `min_kind_rows` rows are skipped."""
    if split == "kind":
        for kind in sorted({s.kind for s in scenarios}):
            test = [s for s in scenarios if s.kind == kind]
            train = [s for s in scenarios if s.kind != kind]
            if len(test) >= min_kind_rows and train:
                yield kind, train, test
        return
    if split != "random":
        raise ValueError(f"unknown split: {split}")
    idx = list(range(len(scenarios)))
    random.Random(seed).shuffle(idx)
    for f in range(k):
        test_idx = set(idx[f::k])
        yield (f"fold{f + 1}",
               [scenarios[i] for i in idx if i not in test_idx],
               [scenarios[i] for i in idx if i in test_idx])


def category_protection(filt: PrivacyFilter, scenarios: list[Scenario]) -> dict[str, list[int]]:
    """category -> [sensitive spans, protected]. Same notion of "protected" as metrics.py."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for sc in scenarios:
        try:
            res = filt.filter(sc.text, sc.ctx)
            text = "" if res.blocked else res.text
        except Exception:
            text = ""
        for g in sc.gold:
            if g.tier == "none":
                continue
            out[g.category][0] += 1
            out[g.category][1] += g.surface not in text
    return out


def run_benchmark(scenarios: list[Scenario], detector: Detector, *, k: int = 5, seeds: tuple[int, ...] = (7,),
                  split: str = "random", tune: bool = True, ablations: dict[str, tuple[str, ...]] | None = None,
                  progress=print) -> tuple[list[dict], list[dict]]:
    """Returns (metric_rows, category_rows). One metric row per (seed, fold, filter)."""
    ablations = ablations or ABLATIONS
    rows: list[dict] = []
    cat_rows: list[dict] = []
    for seed in (seeds if split == "random" else (seeds[0],)):
        for fold, train, test in make_folds(scenarios, k, seed, split):
            progress(f"[{split}] seed {seed} {fold}: train {len(train)} / test {len(test)}")
            pairs, tiers = build_pairs(train, detector)

            filters: dict[str, PrivacyFilter] = {}
            for name, groups in ablations.items():
                if tune:
                    # Logreg only: an ablation compares feature sets, so the model family is held fixed.
                    cfg, _ = select_model(pairs, tiers, groups, families=("logreg",))
                else:
                    cfg = ModelConfig("logreg", C=3.0, class_weight="balanced", none_threshold=0.9, features=groups)
                filters[name] = CAPRLMFilter(RiskModel(cfg).fit(pairs, tiers), detector)
            filters["CAPRLM: full, untuned"] = CAPRLMFilter(
                RiskModel(ModelConfig(features=ALL_GROUPS)).fit(pairs, tiers), detector)
            filters["rule_based"] = RuleBasedPrivacyFilter()
            filters["static_masking"] = StaticMaskingFilter(detector)
            filters["anonymization"] = AnonymizationFilter(detector)
            filters["pseudonymization"] = PseudonymizationFilter(detector)

            for name, filt in filters.items():
                m = evaluate_filter(filt, test)
                rows.append({"split": split, "seed": seed, "fold": fold, "name": name,
                             "n_test": len(test), **{key: m[key] for key in METRICS}})
                if name in (FULL, "static_masking", "rule_based"):
                    for cat, (n, ok) in category_protection(filt, test).items():
                        cat_rows.append({"split": split, "seed": seed, "fold": fold, "name": name,
                                         "category": cat, "sensitive": n, "protected": ok})
    return rows, cat_rows


def summarize(rows: list[dict]) -> list[dict]:
    """Mean and std of each metric over folds, per filter, in first-seen order."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["name"]].append(r)
    out = []
    for name, rs in by.items():
        entry = {"name": name, "n_folds": len(rs)}
        for m in METRICS:
            vals = [r[m] for r in rs]
            entry[m] = mean(vals)
            entry[m + "_std"] = pstdev(vals)
        out.append(entry)
    return out


def paired_difference(rows: list[dict], a: str, b: str, metric: str = "f1") -> dict:
    """metric(a) - metric(b), per fold. If mean is within about one std of the per-fold
    differences, the gap is not distinguishable from fold-to-fold noise."""
    key = lambda r: (r["seed"], r["fold"])
    ra = {key(r): r[metric] for r in rows if r["name"] == a}
    rb = {key(r): r[metric] for r in rows if r["name"] == b}
    diffs = [ra[k] - rb[k] for k in ra if k in rb]
    if not diffs:
        return {"a": a, "b": b, "metric": metric, "mean_diff": None, "std": None, "n": 0, "wins": 0}
    return {"a": a, "b": b, "metric": metric, "mean_diff": mean(diffs), "std": pstdev(diffs),
            "n": len(diffs), "wins": sum(d > 0 for d in diffs)}


def summarize_categories(cat_rows: list[dict]) -> list[dict]:
    agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for r in cat_rows:
        agg[(r["name"], r["category"])][0] += r["sensitive"]
        agg[(r["name"], r["category"])][1] += r["protected"]
    return [{"name": n, "category": c, "sensitive": s, "protected": p, "recall": p / s if s else 0.0}
            for (n, c), (s, p) in sorted(agg.items())]
