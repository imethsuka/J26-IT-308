"""Tests for the model-comparison eval.

Metrics are checked against hand-computed values for tiny datasets and controlled models, so we know
the script *computes* them (nothing hardcoded). Storage/retention/latency are exercised on their
edges.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from eval.model_comparison import (
    AlwaysSaveScorer,
    EvalRecord,
    _temporal_split,
    compare,
    evaluate,
    load_labelled_from_db,
    to_markdown,
    write_csv,
)
from models.base import ImportanceScorer
from models.static_threshold import StaticThresholdScorer


class ScriptedModel(ImportanceScorer):
    """Returns a preset probability keyed by a feature value, for exact confusion control."""

    def __init__(self, mapping: dict, name: str = "scripted") -> None:
        self._map = mapping
        self.name = name

    def predict_proba(self, features):
        return self._map[features["id"]]


def rec(id_, label, size=None):
    return EvalRecord(features={"id": id_}, label=label, size_bytes=size)


# --------------------------------------------------------------------------- metric correctness

def test_perfect_model_metrics():
    records = [rec(0, 1, 100), rec(1, 1, 100), rec(2, 0, 100), rec(3, 0, 100)]
    model = ScriptedModel({0: 0.9, 1: 0.9, 2: 0.1, 3: 0.1}, name="perfect")

    m = evaluate(model, records, threshold=0.5, sizes=[100, 100, 100, 100], total_size=400, storage_basis="bytes")

    assert (m.precision, m.recall, m.f1) == (1.0, 1.0, 1.0)
    assert m.save_rate == 0.5                 # 2 of 4 saved
    assert m.storage_reduction == 0.5         # saved 200 of 400 bytes
    assert m.retention_rate == 1.0            # kept both important contexts
    assert m.latency_mean_ms >= 0.0


def test_metrics_match_hand_computed_for_mixed_predictions():
    # labels:   1,1,1,0  ; predictions (proba>=0.5): 1,0,1,1
    records = [rec(0, 1), rec(1, 1), rec(2, 1), rec(3, 0)]
    model = ScriptedModel({0: 0.8, 1: 0.2, 2: 0.9, 3: 0.7})
    m = evaluate(model, records, threshold=0.5, sizes=[1, 1, 1, 1], total_size=4, storage_basis="count")

    # TP=2 (ids 0,2), FP=1 (id 3), FN=1 (id 1), TN=0
    assert m.precision == pytest.approx(2 / 3, abs=1e-6)   # 2/(2+1)
    assert m.recall == pytest.approx(2 / 3, abs=1e-6)      # 2/(2+1)
    assert m.f1 == pytest.approx(2 / 3, abs=1e-6)
    assert m.save_rate == pytest.approx(3 / 4)             # 3 saved
    assert m.storage_reduction == pytest.approx(1 / 4)     # kept 3 of 4
    assert m.retention_rate == pytest.approx(2 / 3)        # 2 of 3 important kept


def test_storage_reduction_weights_by_bytes_when_available():
    # Save only the small record -> big storage reduction despite 50% save rate.
    records = [rec(0, 1, size=10), rec(1, 0, size=1000)]
    model = ScriptedModel({0: 0.9, 1: 0.1})
    m = evaluate(model, records, threshold=0.5, sizes=[10, 1000], total_size=1010, storage_basis="bytes")
    assert m.save_rate == 0.5
    assert m.storage_reduction == pytest.approx(1 - 10 / 1010, abs=1e-6)


def test_retention_is_none_without_positives():
    records = [rec(0, 0), rec(1, 0)]
    m = evaluate(ScriptedModel({0: 0.9, 1: 0.1}), records, threshold=0.5, sizes=[1, 1], total_size=2, storage_basis="count")
    assert m.retention_rate is None


def test_save_everything_reference_keeps_all():
    records = [rec(0, 1, 5), rec(1, 0, 5), rec(2, 1, 5)]
    m = evaluate(AlwaysSaveScorer(), records, threshold=0.5, sizes=[5, 5, 5], total_size=15, storage_basis="bytes")
    assert m.save_rate == 1.0
    assert m.storage_reduction == 0.0     # saves everything -> no reduction
    assert m.retention_rate == 1.0        # retains every important context
    assert m.recall == 1.0


# --------------------------------------------------------------------------- static-threshold baseline

def test_static_threshold_decisions():
    model = StaticThresholdScorer(feature_key="tmp_dwell_seconds", threshold=30.0, direction="ge")
    assert model.predict_proba({"tmp_dwell_seconds": 45}) == 1.0
    assert model.predict_proba({"tmp_dwell_seconds": 30}) == 1.0   # boundary kept (>=)
    assert model.predict_proba({"tmp_dwell_seconds": 10}) == 0.0
    assert model.predict_proba({}) == 0.0                          # missing feature -> discard


def test_static_threshold_le_direction():
    model = StaticThresholdScorer(feature_key="tmp_dwell_seconds", threshold=5.0, direction="le")
    assert model.predict_proba({"tmp_dwell_seconds": 3}) == 1.0
    assert model.predict_proba({"tmp_dwell_seconds": 9}) == 0.0


# --------------------------------------------------------------------------- comparison + output

def test_compare_runs_all_models_and_outputs_table():
    records = [rec(i, i % 2, size=100) for i in range(20)]
    models = {
        "static": StaticThresholdScorer(feature_key="id", threshold=10, direction="ge"),
        "save-all": AlwaysSaveScorer(),
    }
    results = compare(records, models, threshold=0.5, latency_repeats=1)
    assert len(results) == 2

    md = to_markdown(results)
    assert "| Model |" in md and "Storage reduction" in md
    assert md.count("\n") >= 3  # header + separator + 2 rows

    # save-everything row has zero storage reduction and full retention.
    save_all = next(r for r in results if r.model.startswith("save-everything"))
    assert save_all.storage_reduction == 0.0
    assert save_all.retention_rate == 1.0


def test_compare_uses_count_basis_when_sizes_missing():
    records = [rec(0, 1), rec(1, 0)]  # no size_bytes
    results = compare(records, {"save-all": AlwaysSaveScorer()}, latency_repeats=1)
    assert results[0].storage_basis == "count"


def test_write_csv_has_all_columns(tmp_path):
    records = [rec(i, i % 2, size=50) for i in range(10)]
    results = compare(records, {"save-all": AlwaysSaveScorer()}, latency_repeats=1)
    path = tmp_path / "out.csv"
    write_csv(results, path)

    import csv as csvmod
    rows = list(csvmod.DictReader(path.open()))
    assert len(rows) == 1
    for col in ("model", "precision", "recall", "f1", "storage_reduction", "retention_rate",
                "latency_mean_ms", "latency_p95_ms"):
        assert col in rows[0]


def test_empty_records_rejected():
    with pytest.raises(ValueError):
        compare([], {"save-all": AlwaysSaveScorer()})


# --------------------------------------------------------------------------- split embargo

class TestTemporalSplitEmbargo:
    """Train records must be a full label horizon clear of the test period, so no training
    label was built from events inside the test window."""

    H = timedelta(hours=48)
    BASE = datetime(2026, 9, 20, 9, 0, 0, tzinfo=timezone.utc)

    def _records(self, n: int, spacing_hours: float):
        return [
            EvalRecord(features={"id": i}, label=i % 2, ts=self.BASE + timedelta(hours=spacing_hours * i))
            for i in range(n)
        ]

    def test_split_is_chronological(self):
        train, test = _temporal_split(self._records(10, 12), 0.3, horizon=self.H)
        assert train and test
        assert max(r.ts for r in train) < min(r.ts for r in test)

    def test_train_records_are_a_horizon_clear_of_test_start(self):
        records = self._records(10, 12)  # 12h apart -> spans 108h
        train, test = _temporal_split(records, 0.3, horizon=self.H)

        test_start = min(r.ts for r in test)
        assert train, "the embargo must not empty this train split"
        for r in train:
            assert r.ts + self.H <= test_start, f"record at {r.ts} is inside the embargo"

    def test_embargo_actually_drops_records(self):
        records = self._records(10, 12)
        train, test = _temporal_split(records, 0.3, horizon=self.H)
        # Chronological split alone would give 7 train rows; the embargo removes the last few.
        assert len(train) < 7
        assert len(train) + len(test) < len(records)

    def test_zero_horizon_keeps_the_plain_chronological_split(self):
        records = self._records(10, 12)
        train, test = _temporal_split(records, 0.3, horizon=timedelta(0))
        assert len(train) == 7 and len(test) == 3


# --------------------------------------------------------------------------- db loading

def test_load_labelled_from_db_computes_sizes(tmp_path):
    from storage.context_store import ContextRecord, insert_context_record, set_label
    from storage.database import connect, migrate
    from datetime import datetime, timezone

    conn = connect(":memory:")
    migrate(conn)
    rid = insert_context_record(conn, ContextRecord(
        ts=datetime(2026, 9, 24, tzinfo=timezone.utc), decision="save",
        features={"tmp_dwell_seconds": 40}, embedding=np.zeros(512, dtype=np.float32), app="code",
    ))
    set_label(conn, rid, 1)
    insert_context_record(conn, ContextRecord(  # unlabelled -> excluded
        ts=datetime(2026, 9, 24, tzinfo=timezone.utc), decision="discard", features={}, app="vlc",
    ))

    records = load_labelled_from_db(conn)
    conn.close()

    assert len(records) == 1                    # only the labelled row
    assert records[0].label == 1
    assert records[0].size_bytes == 512 * 4 + len('{"tmp_dwell_seconds":40}')
