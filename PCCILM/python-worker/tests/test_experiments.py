"""Tests for the opt-in experiment features: title normalization (v4 labels + features), extra model
columns, MobileCLIP category features, rolling evaluation, and the cloud loaders (fake Neon rows,
fake bucket). Each opt-in leaves the default behaviour unchanged, which the existing suites cover.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from adaptation.label_construction import LabelConstructor
from features.feature_assembly import SensingEvent, assemble_features
from features.titles import normalize_title
from offline import training

T0 = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
DIM = 8


# ------------------------------------------------------------------ titles

@pytest.mark.parametrize("raw,expected", [
    ("(8) YouTube - Personal - Microsoft​ Edge", "YouTube - Personal - Microsoft Edge"),
    ("Inbox and 3 more pages - Personal - Microsoft​ Edge", "Inbox - Personal - Microsoft Edge"),
    ("Docs and 1 more page - SLIIT - Microsoft Edge", "Docs - SLIIT - Microsoft Edge"),
    ("● main.py - Research - Visual Studio Code", "main.py - Research - Visual Studio Code"),
    ("*notes.txt - Notepad", "notes.txt - Notepad"),
    ("(3) ● file.py - Code", "file.py - Code"),
    ("[12] Mail", "Mail"),
    ("Pages and more pages - Wiki", "Pages and more pages - Wiki"),  # not the Edge pattern
    ("Rate (5) stars - Shop", "Rate (5) stars - Shop"),              # counters only at the start
    ("   ", None),
    (None, None),
])
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


# ------------------------------------------------------------------ v4 labels and features

@dataclass
class Rec:
    id: int
    ts: datetime
    app: str = "msedge"
    domain: str = "docs.example.com"
    title: str = "Doc"


def test_v4_labels_see_revisit_hidden_by_dynamic_title():
    records = [
        Rec(1, T0, title="Doc and 2 more pages - Microsoft Edge"),
        Rec(2, T0 + timedelta(minutes=5), domain="other.example.com", title="Other"),
        Rec(3, T0 + timedelta(hours=1), title="Doc and 5 more pages - Microsoft Edge"),
        Rec(4, T0 + timedelta(hours=60), domain="x.example.com", title="later"),
    ]
    now = T0 + timedelta(days=5)
    v3 = LabelConstructor()
    v4 = LabelConstructor(title_normalizer=normalize_title)

    assert dict(v3.construct_batch(records, now=now))[1].label == 0
    assert dict(v4.construct_batch(records, now=now))[1].label == 1
    assert v3.label_version == "v3/h48" and v4.label_version == "v4/h48"


def test_feature_revisit_uses_normalizer_only_when_given():
    history = [{"ts": T0, "app": "Code", "title": "● a.py - Code"},
               {"ts": T0 + timedelta(minutes=1), "app": "msedge", "title": "x"}]
    event = SensingEvent(ts=T0 + timedelta(minutes=2), app="Code", title="a.py - Code")

    exact = assemble_features(event, None, history)
    normalized = assemble_features(event, None, history, title_normalizer=normalize_title)

    assert exact["ctx_seen_before"] == 0
    assert normalized["ctx_seen_before"] == 1


# ------------------------------------------------------------------ extra model columns

def _frame(n=80, seed=0):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, n)
    feats = [{"tmp_dwell_seconds": float(rng.random()), "ctx_app": "Code", "vis_cat_code": float(v)} for v in y]
    return pd.DataFrame({"features": feats, "label": y})


def test_extra_columns_train_predict_save_load(tmp_path):
    from models.lightgbm_model import ImportanceModel
    from models.vectorizer import FEATURE_COLUMNS, vectorize

    assert len(vectorize({})) == len(FEATURE_COLUMNS)  # default layout unchanged
    assert vectorize({"vis_cat_code": 0.7}, ("vis_cat_code",))[-1] == pytest.approx(0.7)

    model = ImportanceModel(params={"min_data_in_leaf": 2}, num_boost_round=10,
                            versions_dir=tmp_path, extra_feature_columns=("vis_cat_code",))
    model.train(_frame())
    assert "vis_cat_code" in model.feature_importances()
    model.save("v-extra")

    loaded = ImportanceModel.load("v-extra", versions_dir=tmp_path)
    assert loaded.extra_feature_columns == ("vis_cat_code",)
    probe = {"tmp_dwell_seconds": 0.5, "ctx_app": "Code", "vis_cat_code": 1.0}
    assert loaded.predict_proba(probe) == pytest.approx(model.predict_proba(probe))


# ------------------------------------------------------------------ categories

def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_category_features_are_probabilities_and_pick_closest():
    text = np.vstack([unit(np.eye(DIM)[0]), unit(np.eye(DIM)[1])])
    feats = training.category_features(unit(np.eye(DIM)[1] + 0.1), text, ["code", "video"])

    assert set(feats) == {"vis_cat_code", "vis_cat_video"}
    assert sum(feats.values()) == pytest.approx(1.0, abs=1e-5)
    assert feats["vis_cat_video"] > feats["vis_cat_code"]
    assert training.category_features(None, text, ["code", "video"]) == {"vis_cat_code": 0.0, "vis_cat_video": 0.0}


def test_without_vision_blanks_categories_too():
    df = pd.DataFrame({"features": [{"vis_cat_code": 0.9, "vis_novelty": 0.4, "tmp_dwell_seconds": 3}]})
    blank = training.without_vision(df)["features"].iloc[0]
    assert blank == {"vis_cat_code": 0, "vis_novelty": 0, "tmp_dwell_seconds": 3}


# ------------------------------------------------------------------ rolling evaluation

@dataclass
class Cap:
    screenshot_id: str
    ts: datetime
    event: SensingEvent
    session_id: str = "s"
    capture_reason: str = "application_change"
    file_size: int = 100
    image_path: Path = Path("x.jpg")


def _dataset(days=10):
    caps, emb = [], {}
    rng = np.random.default_rng(1)
    work, noise = unit(np.eye(DIM)[0] + 0.1), unit(np.eye(DIM)[1] + 0.1)
    i = 0
    for step in range(days * 12):
        for app, title, base, dwell, offset in (("Code", "report.py", work, 120.0, 0), ("msedge", f"page {step}", noise, 3.0, 60)):
            ts = T0 + timedelta(minutes=step * 120 + offset)
            caps.append(Cap(f"s{i}", ts, SensingEvent(ts=ts, app=app, title=title, dwell_seconds=dwell, window_seconds=10)))
            emb[f"s{i}"] = unit(base + 0.05 * rng.standard_normal(DIM))
            i += 1
    return training.add_labels(training.build_dataset(caps, emb))


def test_rolling_evaluation_folds_are_ordered_embargoed_and_summarized():
    from models.rule_based import RuleBasedImportanceScorer

    data = _dataset()
    configs = {
        "rules": lambda tr, te: (RuleBasedImportanceScorer(), te),
        "lgbm": lambda tr, te: (training.train_model1(tr, params={"min_data_in_leaf": 2}, num_boost_round=10)[0], te),
    }
    results = training.rolling_evaluation(data, configs, n_folds=3, test_fraction=0.5, prototype_k=2, min_train=20)

    assert sorted(results["fold"].unique()) == [0, 1, 2]
    assert set(results["name"]) == {"rules", "lgbm"}
    per_fold = results.drop_duplicates("fold").sort_values("fold")
    assert per_fold["test_from"].is_monotonic_increasing
    assert per_fold["train_rows"].is_monotonic_increasing  # expanding window
    assert "latency_mean_ms" in results.columns

    for col in ("roc_auc", "avg_precision", "recall_at_keep_30", "recall_at_keep_50"):
        assert results[col].between(0, 1).all()

    summary = training.summarize_folds(results)
    assert ("roc_auc", "mean") in summary.columns and summary[("avg_precision", "folds_won")].sum() == 3


def test_ranking_metrics_budget_recall():
    class Fixed:
        def predict_proba(self, f):
            return f["s"]

    df = pd.DataFrame({"features": [{"s": v} for v in (0.9, 0.8, 0.7, 0.2)], "label": [1, 0, 1, 1]})
    m = training.ranking_metrics(Fixed(), df, keep_budgets=(0.5,))
    assert m["recall_at_keep_50"] == pytest.approx(1 / 3)  # top 2 = one of three positives
    assert 0 <= m["roc_auc"] <= 1


# ------------------------------------------------------------------ cloud loaders

def test_clean_database_url_variants():
    from offline.cloud_sources import clean_database_url

    assert clean_database_url("psql 'postgresql://u:p@h.neon.tech/db?sslmode=require'") == \
        "postgresql+psycopg2://u:p@h.neon.tech/db?sslmode=require"
    assert clean_database_url("postgres://u:p#a@b@h/db\n") == "postgresql+psycopg2://u:p%23a%40b@h/db"
    with pytest.raises(ValueError):
        clean_database_url("host=x user=y")


def test_write_drc_sqlite_round_trips_through_replay(tmp_path):
    from offline.cloud_sources import write_drc_sqlite
    from offline.drc_replay import Exclusions, replay_drc

    ts = lambda s: pd.Timestamp(T0 + timedelta(seconds=s))
    tables = {
        "activity_events": pd.DataFrame([{"event_id": "e1", "session_id": "s", "timestamp": ts(0), "event_type": "application_switch",
                                          "application_name": "Code", "process_name": "Code", "window_title": "a.py", "duration_ms": 5}]),
        "browser_events": pd.DataFrame(columns=["browser_event_id", "session_id", "timestamp", "domain"]),
        "interaction_events": pd.DataFrame([{"interaction_id": "i1", "session_id": "s", "timestamp": ts(30),
                                             "interaction_type": "mouse_click", "count": 6}]),
        "screenshots": pd.DataFrame([
            {"screenshot_id": "abc", "session_id": "s", "timestamp": ts(45), "capture_reason": "periodic_capture",
             "file_reference": "p/2026/10/01/abc.jpg", "file_size": 10},
            {"screenshot_id": "nofile", "session_id": "s", "timestamp": ts(50), "capture_reason": "periodic_capture",
             "file_reference": None, "file_size": 10},
        ]),
    }
    images = tmp_path / "img"
    images.mkdir()
    (images / "abc.jpg").write_bytes(b"jpg")

    db = write_drc_sqlite(tables, tmp_path / "neon_as_drc.db")
    captures, stats = replay_drc(db, exclusions=Exclusions(), screenshots_dir=images)

    assert [c.screenshot_id for c in captures] == ["abc"]
    assert captures[0].app == "Code" and captures[0].event.dwell_seconds == pytest.approx(45)
    assert captures[0].event.mouse_clicks == 1  # 6 per minute -> 1 per 10 s
    assert stats.screenshots == 1  # the row without a file reference is never written


class FakeBlob:
    def __init__(self, name, data):
        self.name, self.data, self.size = name, data, len(data)

    def download_to_filename(self, path):
        Path(path).write_bytes(self.data)


class FakeClient:
    def __init__(self, blobs):
        self.blobs = blobs
        self.prefixes = []

    def list_blobs(self, bucket, prefix):
        self.prefixes.append(prefix)
        return [b for b in self.blobs if b.name.startswith(prefix)]


def test_download_profile_screenshots_flat_and_size_checked(tmp_path):
    from offline.cloud_sources import download_profile_screenshots

    client = FakeClient([
        FakeBlob("me/2026/09/20/a.jpg", b"aaaa"),
        FakeBlob("me/2026/09/21/b.jpg", b"bb"),
        FakeBlob("me/notes.txt", b"x"),
        FakeBlob("other/2026/09/20/c.jpg", b"c"),
    ])
    (tmp_path / "b.jpg").write_bytes(b"b")  # half-downloaded: wrong size -> fetched again

    first = download_profile_screenshots(client, "bucket", "me", tmp_path, progress=False)
    second = download_profile_screenshots(client, "bucket", "me", tmp_path, progress=False)

    assert client.prefixes == ["me/", "me/"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jpg", "b.jpg"]
    assert (tmp_path / "b.jpg").read_bytes() == b"bb"
    assert first == {"listed": 2, "skipped": 0, "downloaded": 2}
    assert second == {"listed": 2, "skipped": 2, "downloaded": 0}
