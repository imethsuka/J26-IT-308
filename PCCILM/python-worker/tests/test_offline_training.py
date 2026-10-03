"""Tests for offline.training: dataset replay, labels, embargoed split, train-only prototypes with
recomputed visual features, training/evaluation, embedding cache, and privacy-reduced bundles.

Uses synthetic captures and short synthetic embeddings; no MobileCLIP needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from features.feature_assembly import SensingEvent, prototype_features
from offline import training

T0 = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
DIM = 8


@dataclass
class Cap:
    screenshot_id: str
    ts: datetime
    event: SensingEvent
    session_id: str = "s"
    capture_reason: str = "application_change"
    file_size: int = 100
    image_path: Path = field(default_factory=lambda: Path("missing.jpg"))


def cap(i: int, minutes: float, app: str, title: str, dwell: float = 30.0) -> Cap:
    ts = T0 + timedelta(minutes=minutes)
    return Cap(f"s{i}", ts, SensingEvent(ts=ts, app=app, title=title, dwell_seconds=dwell, window_seconds=10, key_presses=5))


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def synthetic(days: int = 6):
    """Every 2 h: a recurring 'report' context (revisited -> important) and one-off pages (not)."""
    caps, emb = [], {}
    rng = np.random.default_rng(0)
    work, noise = unit(np.eye(DIM)[0] + 0.1), unit(np.eye(DIM)[1] + 0.1)
    i = 0
    for step in range(days * 12):
        minutes = step * 120
        for app, title, base, dwell in (("Code", "report.py", work, 120.0), ("msedge", f"page {step}", noise, 3.0)):
            c = cap(i, minutes + (0 if app == "Code" else 60), app, title, dwell)
            caps.append(c)
            emb[c.screenshot_id] = unit(base + 0.05 * rng.standard_normal(DIM))
            i += 1
    return caps, emb


@pytest.fixture
def labelled():
    caps, emb = synthetic()
    return training.add_labels(training.build_dataset(caps, emb))


def test_build_dataset_rows_in_time_order_with_features(labelled):
    assert list(labelled["ts"]) == sorted(labelled["ts"])
    assert {"tmp_dwell_seconds", "vis_novelty", "ctx_app"} <= set(labelled["features"].iloc[0])
    assert set(labelled["rule_decision"]) <= {"save", "discard"}


def test_build_dataset_without_embeddings_marks_visual_neutral():
    caps, _ = synthetic(days=1)
    df = training.build_dataset(caps, None)
    assert all(e is None for e in df["embedding"])
    assert all(f["vis_has_prototypes"] == 0 for f in df["features"])


def test_labels_recurring_context_important_one_offs_not_and_tail_censored(labelled):
    done = labelled[labelled["label"].notna()]
    assert set(done[done["app"] == "Code"]["label"]) == {1}
    assert set(done[done["app"] == "msedge"]["label"]) == {0}
    newest = labelled["ts"].max()
    assert labelled[labelled["ts"] > newest - timedelta(hours=48)]["label"].isna().all()
    assert set(done["label_version"]) == {"v3/h48"}


def test_labels_with_missing_domains_and_titles():
    # pandas 3 turns missing text into NaN in a str column; labelling must treat it as None.
    caps, emb = synthetic(days=4)
    for i, c in enumerate(caps):
        c.event.domain = "docs.example.com" if i % 2 else None
        if i % 5 == 0:
            c.event.title = None
    df = training.add_labels(training.build_dataset(caps, emb))
    assert df["label"].notna().any()


def test_temporal_split_is_ordered_and_embargoed(labelled):
    train, test = training.temporal_split(labelled, test_fraction=0.3)
    assert train["ts"].max() < test["ts"].min()
    assert (train["ts"] + timedelta(hours=48) <= test["ts"].min()).all()
    assert train["label"].notna().all() and test["label"].notna().all()


def test_prototypes_learned_from_train_only_and_recomputed(labelled):
    train, test = training.temporal_split(labelled, test_fraction=0.3)
    assert all(f["vis_proto_similarity"] == 0.0 for f in train["features"])  # cold start

    proto = training.learn_prototypes(train, k=2)
    assert proto is not None and proto.n_samples == int((train["label"] == 1).sum())

    updated = training.with_prototypes(test, proto.centroids)
    code_sims = [f["vis_proto_similarity"] for f, a in zip(updated["features"], updated["app"]) if a == "Code"]
    web_sims = [f["vis_proto_similarity"] for f, a in zip(updated["features"], updated["app"]) if a == "msedge"]
    assert min(code_sims) > max(web_sims)
    assert all(f["vis_has_prototypes"] == 1 for f in updated["features"])
    assert all(f["vis_proto_similarity"] == 0.0 for f in test["features"])  # input not mutated


def test_learn_prototypes_returns_none_when_too_few_important(labelled):
    train, _ = training.temporal_split(labelled, test_fraction=0.3)
    assert training.learn_prototypes(train, k=10_000) is None


def test_train_and_evaluate_end_to_end(labelled):
    from eval.model_comparison import AlwaysSaveScorer
    from models.rule_based import RuleBasedImportanceScorer

    train, test = training.temporal_split(labelled, test_fraction=0.3)
    proto = training.learn_prototypes(train, k=2)
    train, test = training.with_prototypes(train, proto.centroids), training.with_prototypes(test, proto.centroids)

    model, summary = training.train_model1(train, params={"min_data_in_leaf": 2}, num_boost_round=20)
    table = training.evaluate_models(test, {"rules": RuleBasedImportanceScorer(), "model-1": model, "keep-all": AlwaysSaveScorer()})

    assert summary["n_train"] == len(train)
    assert list(table["name"]) == ["rules", "model-1", "keep-all"]
    keep_all = table.set_index("name").loc["keep-all"]
    assert keep_all["keep_rate"] == 1.0 and keep_all["storage_reduction"] == 0.0
    assert table.set_index("name").loc["model-1", "f1"] == 1.0  # trivially separable synthetic data

    curve = training.threshold_curve(test, model)
    assert curve["keep_rate"].is_monotonic_decreasing
    assert ((curve["storage_saved"] >= 0) & (curve["storage_saved"] <= 1)).all()


def test_save_version_is_append_only(labelled, tmp_path):
    train, _ = training.temporal_split(labelled, test_fraction=0.3)
    model, _ = training.train_model1(train, params={"min_data_in_leaf": 2}, num_boost_round=5)
    proto = training.learn_prototypes(train, k=2)

    target = training.save_version(model, proto, "v-test", versions_dir=tmp_path, notes={"a": 1})

    assert (target / "model.txt").exists() and (target / "prototypes.npy").exists()
    assert (target / "training_notes.json").exists()
    with pytest.raises(FileExistsError):
        training.save_version(model, proto, "v-test", versions_dir=tmp_path)


def test_register_version_deploys_only_if_better_than_cold_start_baseline(labelled, tmp_path):
    from storage.context_store import get_active_model_version
    from storage.database import connect

    train, test = training.temporal_split(labelled, test_fraction=0.3)
    model, _ = training.train_model1(train, params={"min_data_in_leaf": 2}, num_boost_round=20)
    training.save_version(model, None, "v-reg", versions_dir=tmp_path)
    db = tmp_path / "context.db"

    decision = training.register_version(db, model, test, "v-reg")

    conn = connect(db)
    try:
        active = get_active_model_version(conn)
    finally:
        conn.close()
    assert decision.current_metrics is not None  # compared against the rule-based baseline
    assert (active is not None and active.version == "v-reg") == decision.deployed


class FakeEncoder:
    def __init__(self):
        self.calls = 0

    def embed_frame(self, image):
        self.calls += 1
        return unit(np.ones(DIM))


def test_embedding_cache_embeds_once_and_skips_unreadable(tmp_path):
    from PIL import Image

    good = tmp_path / "a.jpg"
    Image.new("RGB", (4, 4)).save(good)
    caps = [cap(0, 0, "Code", "a"), cap(1, 1, "Code", "b")]
    caps[0].image_path = good
    caps[1].image_path = tmp_path / "missing.jpg"
    cache_file = tmp_path / "emb.npz"

    encoder = FakeEncoder()
    first = training.embed_captures(caps, cache_file, encoder=encoder, progress=False)
    again = training.embed_captures(caps[:1], cache_file, encoder=FakeEncoder(), progress=False)

    assert set(first) == {"s0"} and encoder.calls == 1
    assert np.allclose(again["s0"], first["s0"])
    assert set(training.load_embedding_cache(cache_file)) == {"s0"}


def test_bundle_round_trip_drops_text(labelled, tmp_path):
    path = training.export_bundle(labelled, tmp_path / "b.parquet", participant="p1")

    raw = pd.read_parquet(path)
    assert not {"title", "domain", "image_path"} & set(raw.columns)
    assert "report" not in "".join(raw["features_json"])

    back = training.load_bundle(path)
    assert len(back) == len(labelled)
    assert back["label"].isna().sum() == labelled["label"].isna().sum()
    assert np.allclose(back["embedding"].iloc[0], labelled["embedding"].iloc[0])
    assert "tmp_dwell_seconds" in back["features"].iloc[0] and "ctx_title_tokens" not in back["features"].iloc[0]


def test_prototype_features_matches_assemble_features():
    from features.feature_assembly import assemble_features

    emb, protos = unit(np.arange(1, DIM + 1)), np.vstack([unit(np.ones(DIM)), unit(np.eye(DIM)[0])])
    event = SensingEvent(ts=T0, app="Code", title="x")
    full = assemble_features(event, emb, [], prototypes=protos)
    part = prototype_features(emb, protos)
    assert part == {k: full[k] for k in part}
    assert prototype_features(None, protos)["vis_has_prototypes"] == 0
