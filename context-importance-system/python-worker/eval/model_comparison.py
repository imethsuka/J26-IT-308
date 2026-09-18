"""Side-by-side model comparison for the results chapter.

Given a labelled dataset, computes for each model — rule-based baseline, static-threshold baseline,
Model 1 (LightGBM), plus a "save-everything" reference — the metrics:

- precision / recall / F1 on importance prediction,
- projected storage reduction vs saving everything (what the model *would* save if discards were
  dropped; the worker currently stores every context, since labelling and training need them all),
- relevant-context retention rate (share of truly-important contexts the model keeps),
- mean / p95 decision latency (measured live on this machine).

Every number is computed from the data provided (the labelled context store) and from real timing
of ``predict_proba`` — nothing is hardcoded or fabricated. If required data is missing (empty test
set, single-class training split, missing model version) the script fails with a clear error rather
than inventing a value.

Usage:
    # Train Model 1 on a temporal split of the labelled records and evaluate all models on the rest:
    python -m eval.model_comparison --db context.db [--test-fraction 0.3] [--csv comparison.csv]

    # Or evaluate a saved Model 1 version on the whole labelled set:
    python -m eval.model_comparison --db context.db --model-version lightgbm-0.1.0
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptation.deployment import ClassificationMetrics  # noqa: E402
from adaptation.label_construction import DEFAULT_LABEL_HORIZON  # noqa: E402
from models.base import ImportanceScorer  # noqa: E402


@dataclass
class EvalRecord:
    features: dict
    label: int
    size_bytes: int | None = None
    ts: object = None  # used only for the temporal train/test split


@dataclass
class ModelMetrics:
    model: str
    n: int
    precision: float
    recall: float
    f1: float
    save_rate: float
    storage_reduction: float
    retention_rate: float | None
    latency_mean_ms: float
    latency_p95_ms: float
    threshold: float
    storage_basis: str  # "bytes" or "count"

    def to_row(self) -> dict:
        return {
            "model": self.model,
            "n": self.n,
            "precision": _fmt(self.precision),
            "recall": _fmt(self.recall),
            "f1": _fmt(self.f1),
            "save_rate": _fmt(self.save_rate),
            "storage_reduction": _fmt(self.storage_reduction),
            "retention_rate": "" if self.retention_rate is None else _fmt(self.retention_rate),
            "latency_mean_ms": _fmt(self.latency_mean_ms, 4),
            "latency_p95_ms": _fmt(self.latency_p95_ms, 4),
            "threshold": _fmt(self.threshold),
            "storage_basis": self.storage_basis,
        }


COLUMNS = [
    "model", "n", "precision", "recall", "f1", "save_rate", "storage_reduction",
    "retention_rate", "latency_mean_ms", "latency_p95_ms", "threshold", "storage_basis",
]


def evaluate(
    model: ImportanceScorer,
    records: Sequence[EvalRecord],
    *,
    threshold: float,
    sizes: Sequence[int],
    total_size: int,
    storage_basis: str,
    latency_repeats: int = 3,
) -> ModelMetrics:
    """Compute all metrics for one model on the given records. Timing is measured separately."""
    if not records:
        raise ValueError("cannot evaluate on an empty record set")

    tp = fp = fn = tn = 0
    saved_size = 0
    n_saved = 0
    for record, size in zip(records, sizes):
        predicted = 1 if model.predict_proba(record.features) >= threshold else 0
        actual = int(record.label)
        if predicted == 1:
            n_saved += 1
            saved_size += size
        if predicted == 1 and actual == 1:
            tp += 1
        elif predicted == 1 and actual == 0:
            fp += 1
        elif predicted == 0 and actual == 1:
            fn += 1
        else:
            tn += 1

    cls = ClassificationMetrics.from_confusion(tp, fp, fn, tn, threshold=threshold)
    n = len(records)
    positives = tp + fn

    latency_mean, latency_p95 = _measure_latency_ms(model, records, latency_repeats)

    return ModelMetrics(
        model=model.name,
        n=n,
        precision=cls.precision,
        recall=cls.recall,
        f1=cls.f1,
        save_rate=n_saved / n,
        storage_reduction=1.0 - (saved_size / total_size) if total_size else 0.0,
        # Share of truly-important contexts retained (= positive-class recall); None if no positives.
        retention_rate=(tp / positives) if positives else None,
        latency_mean_ms=latency_mean,
        latency_p95_ms=latency_p95,
        threshold=threshold,
        storage_basis=storage_basis,
    )


def compare(
    records: Sequence[EvalRecord],
    named_models: dict[str, ImportanceScorer],
    *,
    threshold: float = 0.5,
    latency_repeats: int = 3,
) -> list[ModelMetrics]:
    """Evaluate every model on the same records and return their metrics."""
    if not records:
        raise ValueError("no labelled records to evaluate on")

    have_sizes = all(r.size_bytes is not None for r in records)
    storage_basis = "bytes" if have_sizes else "count"
    sizes = [int(r.size_bytes) for r in records] if have_sizes else [1] * len(records)
    total_size = sum(sizes)

    results = []
    for model in named_models.values():
        results.append(evaluate(
            model, records, threshold=threshold, sizes=sizes, total_size=total_size,
            storage_basis=storage_basis, latency_repeats=latency_repeats,
        ))
    return results


def _measure_latency_ms(model: ImportanceScorer, records: Sequence[EvalRecord], repeats: int) -> tuple[float, float]:
    feature_dicts = [r.features for r in records]
    for f in feature_dicts:  # warm up (JIT, caches, first-call allocations)
        model.predict_proba(f)

    per_call_ms: list[float] = []
    for _ in range(max(1, repeats)):
        for f in feature_dicts:
            start = perf_counter()
            model.predict_proba(f)
            per_call_ms.append((perf_counter() - start) * 1000.0)

    per_call_ms.sort()
    return statistics.fmean(per_call_ms), _percentile(per_call_ms, 95)


def _percentile(sorted_values: list[float], pct: int) -> float:
    if not sorted_values:
        return float("nan")
    rank = max(0, min(len(sorted_values) - 1, (pct * len(sorted_values) + 99) // 100 - 1))
    return sorted_values[rank]


# --------------------------------------------------------------------------- output

def to_markdown(results: Iterable[ModelMetrics]) -> str:
    header = ["Model", "N", "Precision", "Recall", "F1", "Save rate", "Storage reduction (projected)",
              "Retention", "Latency mean (ms)", "Latency p95 (ms)"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for m in results:
        row = m.to_row()
        lines.append("| " + " | ".join([
            row["model"], str(row["n"]), row["precision"], row["recall"], row["f1"],
            row["save_rate"], row["storage_reduction"], row["retention_rate"] or "n/a",
            row["latency_mean_ms"], row["latency_p95_ms"],
        ]) + " |")
    return "\n".join(lines)


def write_csv(results: Iterable[ModelMetrics], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for m in results:
            writer.writerow(m.to_row())


def _fmt(value: float, places: int = 4) -> str:
    return f"{value:.{places}f}"


# --------------------------------------------------------------------------- data loading + models


class AlwaysSaveScorer(ImportanceScorer):
    """The "save everything" reference policy: keeps every context."""

    name = "save-everything (reference)"

    def predict_proba(self, features) -> float:
        return 1.0


def load_labelled_from_db(conn) -> list[EvalRecord]:
    """Load labelled context records as EvalRecords, with real per-record storage size."""
    from storage.context_store import list_context_records

    records = []
    for r in list_context_records(conn):
        if r.label is None:
            continue
        embedding_bytes = int(r.embedding.nbytes) if r.embedding is not None else 0
        features_bytes = len(json.dumps(r.features, separators=(",", ":")))
        records.append(EvalRecord(
            features=r.features, label=int(r.label),
            size_bytes=embedding_bytes + features_bytes, ts=r.ts,
        ))
    return records


def _temporal_split(
    records: list[EvalRecord],
    test_fraction: float,
    *,
    horizon: timedelta = DEFAULT_LABEL_HORIZON,
) -> tuple[list[EvalRecord], list[EvalRecord]]:
    """Oldest rows train, most-recent ``test_fraction`` test, with a label-horizon embargo.

    A training row's label was built from events in ``(ts, ts + horizon]``. If that window reaches
    into the test period, the training label encodes test-period behaviour, so the row is dropped
    rather than leaked. Training rows therefore satisfy ``ts + horizon <= test_start``.
    """
    ordered = sorted(records, key=lambda r: r.ts)
    n_test = max(1, int(round(len(ordered) * test_fraction)))
    n_test = min(n_test, len(ordered) - 1)
    train, test = ordered[:-n_test], ordered[-n_test:]

    test_start = test[0].ts
    embargoed = [r for r in train if r.ts + horizon <= test_start]
    return embargoed, test


def build_models(train_records, model_version, versions_dir, static_kwargs) -> tuple[dict, list[EvalRecord] | None]:
    """
    Assemble the three models (+ reference). Returns (named_models, trained_on) where trained_on is
    the train split used for Model 1 (None if a saved version was loaded).
    """
    from models.lightgbm_model import ImportanceModel
    from models.rule_based import RuleBasedImportanceScorer
    from models.static_threshold import StaticThresholdScorer

    if model_version is not None:
        model1 = ImportanceModel.load(model_version, versions_dir=versions_dir)
        trained_on = None
    else:
        import pandas as pd

        if not train_records:
            raise ValueError("no training records for Model 1; provide more data or --model-version")
        if {r.label for r in train_records} != {0, 1}:
            raise ValueError("Model 1 training split is single-class; cannot train a classifier")
        df = pd.DataFrame([{"features": r.features, "label": r.label} for r in train_records])
        model1 = ImportanceModel()
        model1.train(df)
        trained_on = train_records

    models = {
        "rule-based": RuleBasedImportanceScorer(),
        "static-threshold": StaticThresholdScorer(**static_kwargs),
        "model-1": model1,
        "save-everything": AlwaysSaveScorer(),
    }
    return models, trained_on


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="SQLite context store containing labelled records")
    parser.add_argument("--model-version", default=None, help="load a saved Model 1 version (else train on a split)")
    parser.add_argument("--versions-dir", default=None, help="model versions directory (for --model-version)")
    parser.add_argument("--test-fraction", type=float, default=0.3, help="temporal holdout for the test set when training Model 1")
    parser.add_argument("--label-horizon-hours", type=float, default=DEFAULT_LABEL_HORIZON.total_seconds() / 3600.0,
                        help="label horizon; train rows within this of the test start are embargoed")
    parser.add_argument("--threshold", type=float, default=0.5, help="save/discard probability threshold")
    parser.add_argument("--static-feature", default="tmp_dwell_seconds")
    parser.add_argument("--static-threshold", type=float, default=30.0)
    parser.add_argument("--static-direction", default="ge", choices=["ge", "le"])
    parser.add_argument("--latency-repeats", type=int, default=3)
    parser.add_argument("--csv", default="model_comparison.csv")
    args = parser.parse_args(argv)

    from storage.database import connect

    conn = connect(args.db, read_only=True)
    try:
        records = load_labelled_from_db(conn)
    finally:
        conn.close()

    if not records:
        print("error: no labelled records found in the database", file=sys.stderr)
        return 2

    versions_dir = Path(args.versions_dir) if args.versions_dir else _default_versions_dir()
    static_kwargs = dict(feature_key=args.static_feature, threshold=args.static_threshold, direction=args.static_direction)

    horizon = timedelta(hours=args.label_horizon_hours)
    n_embargoed = 0
    if args.model_version is not None:
        test_records = records  # evaluate the saved model on all provided labelled records
        train_records: list[EvalRecord] = []
    else:
        train_records, test_records = _temporal_split(records, args.test_fraction, horizon=horizon)
        n_embargoed = len(records) - len(train_records) - len(test_records)

    try:
        models, _ = build_models(train_records, args.model_version, versions_dir, static_kwargs)
        results = compare(test_records, models, threshold=args.threshold, latency_repeats=args.latency_repeats)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(to_markdown(results))
    print("\nStorage reduction is projected: the worker stores every context regardless of the "
          "decision, because labelling and retraining need them.")
    csv_path = Path(args.csv)
    write_csv(results, csv_path)
    print(f"\nEvaluated {len(test_records)} held-out records"
          + ("" if args.model_version else f" ({len(train_records)} used to train Model 1, "
                                           f"{n_embargoed} embargoed within {args.label_horizon_hours:g}h "
                                           f"of the test period)")
          + f". CSV written to {csv_path.resolve()}")
    return 0


def _default_versions_dir() -> Path:
    from models.lightgbm_model import DEFAULT_VERSIONS_DIR

    return DEFAULT_VERSIONS_DIR


if __name__ == "__main__":
    raise SystemExit(main())
