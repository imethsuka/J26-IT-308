"""Run CAPRLM and the four baselines over the same labelled scenario set and write
research/eval/results.json — the only file the .NET UI reads.

    cd CAPRLM
    python -m research.eval.run_comparison [--n-train 600 --n-test 250 --seed 7]

While research decision #1 (label protocol) is open the only dataset is the PLACEHOLDER
generator, and the output says so in its "dataset" field. That field must never be dropped
or renamed to something that reads like a real result.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from research.data.placeholder_labels import PLACEHOLDER_DATASET_NAME
from research.data.llm_dataset import DATA_DIR, load_dir, split
from research.data.placeholder_scenarios import generate
from research.detection import Detector
from research.filters import FILTER_NAMES, build_filters
from research.caprlm_filter import CAPRLMFilter
from research.model import ModelConfig, RiskModel
from research.model.training import ARTIFACT_PATH, build_pairs, train_model

from .metrics import SIMILARITY_METHOD, evaluate_filter

RESULTS_PATH = Path(__file__).resolve().parent / "results.json"
SCHEMA_VERSION = 1


LLM_DATASET_NAME = "llm-synthetic-unaudited"


def _coverage(scenarios, detector) -> dict:
    """Share of sensitive gold spans the detection stage finds at all — the ceiling on
    what any risk model downstream can protect."""
    total = hit = 0
    for sc in scenarios:
        cands = detector.detect(sc.text)
        for g in sc.gold:
            if g.tier == "none":
                continue
            i = sc.text.find(g.surface)
            total += 1
            hit += any(c.start < i + len(g.surface) and i < c.end for c in cands)
    return {"sensitive_spans": total, "detected": hit, "rate": hit / total if total else 0.0}


def run(n_train: int, n_test: int, seed: int, out: Path = RESULTS_PATH, save_model: bool = True,
        data_dir: Path | None = None) -> dict:
    detector = Detector.default()
    load_report = None
    if data_dir is not None:
        scenarios, report = load_dir(data_dir)
        load_report = report.summary()
        train, test = split(scenarios, 0.2, seed)
        dataset_name = LLM_DATASET_NAME
    else:
        train = generate(n_train, seed, test=False)
        test = generate(n_test, seed + 1, test=True)
        dataset_name = PLACEHOLDER_DATASET_NAME

    model, selection = train_model(train, detector, dataset_name)
    if save_model:
        model.save(ARTIFACT_PATH)

    filters = build_filters(model, detector)
    metrics = {name: evaluate_filter(filters[name], test) for name in FILTER_NAMES}

    # What the tuning bought: the same features with the untuned defaults (C=5, no class
    # weights, plain argmax), scored on the same untouched test set.
    untuned = RiskModel(ModelConfig()).fit(*build_pairs(train, detector), trained_on=dataset_name)
    untuned_metrics = evaluate_filter(CAPRLMFilter(untuned, detector), test)
    n_sensitive = sum(1 for sc in test for g in sc.gold if g.tier != "none")

    results = {
        "schema_version": SCHEMA_VERSION,
        "dataset": dataset_name,
        "dataset_detail": {
            "label_protocol": (
                "hand-written (category x context-kind) table; deterministic, so learnable by "
                "construction — a pipeline check, not a result" if data_dir is None else
                "labels written by an LLM from a tier-definition prompt; NOT human-audited"),
            "n_train_scenarios": len(train),
            "n_test_scenarios": len(test),
            "n_test_sensitive_spans": n_sensitive,
            "seed": seed,
            "split": ("disjoint value pools; one context variant per kind is test-only"
                      if data_dir is None else "random 80/20 by row; all rows from the same generator"),
            "load_report": load_report,
            "detection_coverage_test": _coverage(test, detector),
            "detection_coverage_train": _coverage(train, detector),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detection": detector.info(),
        "model": {"version": model.version, "selection": selection,
                  "config": {"kind": model.config.kind, "C": model.config.C,
                             "class_weight": model.config.class_weight,
                             "none_threshold": model.config.none_threshold},
                  "untuned_on_test": untuned_metrics},
        "similarity_method": SIMILARITY_METHOD,
        "filter_versions": {name: getattr(f, "version", name) for name, f in filters.items()},
        "metrics": metrics,
        "personalization": None,   # no personalization mechanism exists yet — nothing to compare
        "notes": [
            ("Placeholder dataset: do not quote these numbers as results." if data_dir is None else
             "Small LLM-generated, unaudited dataset: indicative only, not reportable until human-audited."),
            "detection_coverage_*: share of sensitive spans the detectors find at all. Categories "
            "with no detector (e.g. NATIONAL_ID, MEDICAL, BANK_ACCOUNT) are invisible to every "
            "detection-sharing filter, so recall is capped by this, not by the risk model.",
            "precision/recall/f1: over gold spans. A span counts as protected if its text no longer "
            "appears in the output; sensitive = gold tier above 'none'. Over-redaction lowers precision.",
            "leakage_rate: share of sensitive spans whose exact text survives in the output.",
            "reidentification_rate: share of sensitive spans where any identifying token (>=4 chars) "
            "survives — a crude token-level attacker, not a model-based attack.",
            "semantic_similarity is a lexical TF-IDF cosine proxy, not an embedding-based score.",
            "rule_based has its own regex-only detection; the other three baselines share CAPRLM's "
            "regex+NER detection. Compare CAPRLM with static_masking to isolate the risk model's "
            "contribution; a gap to rule_based also includes detection coverage.",
        ],
    }
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n-train", type=int, default=600)
    ap.add_argument("--n-test", type=int, default=250)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=RESULTS_PATH)
    ap.add_argument("--data", type=Path, nargs="?", const=DATA_DIR, default=None,
                    help="train/evaluate on the LLM JSONL files (default folder: CAPRLM/CAPRLM Data)")
    args = ap.parse_args()

    results = run(args.n_train, args.n_test, args.seed, args.out, data_dir=args.data)
    print(f"dataset: {results['dataset']}   model: {results['model']['version']}")
    header = f"{'filter':<18}{'P':>7}{'R':>7}{'F1':>7}{'leak':>7}{'reid':>7}{'sim':>7}{'blocked':>9}"
    print(header)
    for name, m in results["metrics"].items():
        print(f"{name:<18}{m['precision']:>7.3f}{m['recall']:>7.3f}{m['f1']:>7.3f}"
              f"{m['leakage_rate']:>7.3f}{m['reidentification_rate']:>7.3f}"
              f"{m['semantic_similarity']:>7.3f}{m['blocked_rate']:>9.3f}")
    print(f"detection coverage (test): {results['dataset_detail']['detection_coverage_test']}")
    sel = results["model"]["selection"]
    print(f"chosen: {sel['chosen']}  tau={results['model']['config']['none_threshold']}")
    u = results["model"]["untuned_on_test"]
    print(f"untuned (same features): P={u['precision']:.3f} R={u['recall']:.3f} F1={u['f1']:.3f} leak={u['leakage_rate']:.3f}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
