"""End-to-end pipeline test for the LightGBM ImportanceModel: features -> train -> predict, plus
versioned save/load. This checks the machinery runs and round-trips; it does NOT assert real
accuracy (the data is synthetic).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from features.feature_assembly import SensingEvent, assemble_features
from models.base import ImportanceScorer
from models.lightgbm_model import ImportanceModel
from models.vectorizer import FEATURE_COLUMNS

T0 = datetime(2026, 9, 24, 14, 0, 0, tzinfo=timezone.utc)
PROD_APPS = ["code", "winword", "excel"]
MEDIA_APPS = ["vlc", "spotify", "steam"]


def _synthetic_frame(n: int = 300, seed: int = 0) -> pd.DataFrame:
    """Feature dicts assembled from synthetic sensing events, with a learnable latent label."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        productive = bool(rng.integers(0, 2))
        app = rng.choice(PROD_APPS if productive else MEDIA_APPS)
        dwell = float(rng.uniform(0, 120))
        seen_before = bool(rng.integers(0, 2))
        ev = SensingEvent(
            ts=T0 + timedelta(minutes=i),
            app=str(app),
            title=f"session {i}",
            domain=None,
            dwell_seconds=dwell,
            key_presses=int(rng.integers(0, 50)),
            mouse_clicks=int(rng.integers(0, 20)),
            window_seconds=10,
        )
        history = [ev] if seen_before else []  # a prior identical context -> revisit
        feats = assemble_features(ev, embedding=None, recent_context_history=history)

        # Latent importance: productive app + long dwell + revisit, plus noise.
        score = (1.5 * productive) + (dwell / 120.0) + (0.7 * seen_before) + rng.normal(0, 0.3)
        rows.append({"features": feats, "label": int(score > 1.4)})
    return pd.DataFrame(rows)


@pytest.fixture
def trained(tmp_path):
    model = ImportanceModel(versions_dir=tmp_path / "versions", num_boost_round=60)
    summary = model.train(_synthetic_frame())
    return model, summary


def test_pipeline_runs_features_train_predict(trained) -> None:
    model, summary = trained
    assert model.is_trained
    assert summary["n_train"] == 300
    assert set(summary["class_counts"]) == {0, 1}

    sample = assemble_features(
        SensingEvent(ts=T0, app="code", title="x", dwell_seconds=90, window_seconds=10),
        embedding=None, recent_context_history=[],
    )
    p = model.predict_proba(sample)
    assert isinstance(p, float) and 0.0 <= p <= 1.0
    assert model.predict(sample) in (0, 1)


def test_model_implements_shared_interface(trained) -> None:
    model, _ = trained
    assert isinstance(model, ImportanceScorer)


def test_batch_matches_single(trained) -> None:
    model, _ = trained
    samples = [
        assemble_features(SensingEvent(ts=T0, app=a, dwell_seconds=d, window_seconds=10), embedding=None, recent_context_history=[])
        for a, d in (("code", 100.0), ("vlc", 2.0))
    ]
    batch = model.predict_proba_batch(samples)
    singles = np.array([model.predict_proba(s) for s in samples])
    np.testing.assert_allclose(batch, singles, rtol=1e-6)


def test_feature_importances_reported(trained) -> None:
    model, summary = trained
    gain = model.feature_importances("gain")
    assert set(gain).issubset(set(FEATURE_COLUMNS))
    assert sum(gain.values()) > 0
    # Sorted highest-first.
    values = list(gain.values())
    assert values == sorted(values, reverse=True)
    # Reported in the training summary too, and human-formattable.
    assert summary["feature_importances_gain"] == gain
    assert "Feature importances (gain)" in model.format_importances(top=5)


def test_save_is_append_only(trained, tmp_path) -> None:
    model, _ = trained
    path = model.save("0.1.0")
    assert (path / "model.txt").exists()
    assert (path / "metadata.json").exists()

    # Never overwrite an existing version.
    with pytest.raises(FileExistsError):
        model.save("0.1.0")

    # A different version id coexists (history kept for rollback).
    model.save("0.2.0")
    assert set(ImportanceModel.list_versions(tmp_path / "versions")) == {"0.1.0", "0.2.0"}


def test_load_round_trips_predictions(trained, tmp_path) -> None:
    model, _ = trained
    model.save("1.0.0")

    reloaded = ImportanceModel.load("1.0.0", versions_dir=tmp_path / "versions")
    assert reloaded.name == "lightgbm-1.0.0"

    samples = _synthetic_frame(n=20, seed=99)["features"].tolist()
    before = model.predict_proba_batch(samples)
    after = reloaded.predict_proba_batch(samples)
    np.testing.assert_allclose(before, after, rtol=1e-9)


def test_metadata_records_feature_columns(trained, tmp_path) -> None:
    import json

    model, _ = trained
    path = model.save("0.3.0")
    meta = json.loads((path / "metadata.json").read_text())
    assert meta["feature_columns"] == list(FEATURE_COLUMNS)
    assert meta["version_id"] == "0.3.0"
    assert "feature_importances_gain" in meta


def test_predict_before_train_raises(tmp_path) -> None:
    model = ImportanceModel(versions_dir=tmp_path / "versions")
    with pytest.raises(RuntimeError, match="not trained"):
        model.predict_proba({"tmp_dwell_seconds": 10})


def test_single_class_training_rejected(tmp_path) -> None:
    df = _synthetic_frame(n=30)
    df["label"] = 1  # collapse to one class
    model = ImportanceModel(versions_dir=tmp_path / "versions")
    with pytest.raises(ValueError, match="both classes"):
        model.train(df)


def test_load_missing_version_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        ImportanceModel.load("nope", versions_dir=tmp_path / "versions")
