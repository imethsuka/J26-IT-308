"""Shadow mode and champion choice: every model saved/loaded by ``models.registry``, the worker recording what each model
would have saved (``model_decisions``), the Make champion command, the cycle never replacing a chosen champion with
another family, and the backup before a migration."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from adaptation.champion import make_champion
from adaptation.scheduled_job import run_cycle
from models import registry
from models.base import ImportanceScorer
from pipeline import ShadowModel, SnapshotPipeline, load_shadows
from sensing.codec import encode_message, read_message
from storage.context_store import (
    ModelVersion, get_active_model_version, insert_model_version, list_context_records, list_model_decisions,
    list_model_versions,
)
from storage.database import connect, migrate
from tests.mock_snapshot import mock_header, mock_payload
from tests.test_scheduled_job import NOW, cfg, seed_blocks

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = connect(":memory:")
    migrate(c)
    yield c
    c.close()


def frame(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        dwell = float(rng.exponential(60))
        rows.append({
            "ts": T0 + timedelta(minutes=10 * i),
            "features": {"tmp_dwell_seconds": dwell, "beh_key_presses": int(rng.poisson(10)), "beh_window_seconds": 10},
            "embedding": rng.normal(size=512).astype(np.float32),
            "label": int(dwell + rng.normal(0, 20) > 50),
        })
    return pd.DataFrame(rows)


@pytest.mark.parametrize("algorithm", registry.CHALLENGERS + (registry.LIGHTGBM,))
def test_every_model_saves_loads_and_scores_the_same(algorithm, tmp_path) -> None:
    df = frame()
    scorer, _ = registry.train_scorer(algorithm, df)
    registry.save_scorer(scorer, algorithm, f"{algorithm}-v1", tmp_path)
    loaded = registry.load_scorer(algorithm, f"{algorithm}-v1", tmp_path)
    before = registry.score_many(scorer, list(df["features"][:20]), list(df["embedding"][:20]))
    after = [registry.score(loaded, f, e) for f, e in zip(df["features"][:20], df["embedding"][:20])]
    assert np.allclose(before, after)
    assert loaded.name == f"{algorithm}-{algorithm}-v1"
    with pytest.raises(FileExistsError):  # versions are append-only
        registry.save_scorer(scorer, algorithm, f"{algorithm}-v1", tmp_path)


def test_river_continues_with_only_new_labels(tmp_path) -> None:
    df = frame(200)
    scorer = registry.new_scorer("river_arf")
    assert scorer.learn(df.iloc[:100]) == 100
    registry.save_scorer(scorer, "river_arf", "river_arf-v1", tmp_path)
    again = registry.load_scorer("river_arf", "river_arf-v1", tmp_path)
    assert again.learn(df) == 100  # the first 100 were learned already
    assert again.metadata["n_learned"] == 200


def test_embedding_models_score_without_an_embedding() -> None:
    scorer, _ = registry.train_scorer("mobileclip_logreg", frame())
    assert 0.0 <= registry.score(scorer, {"tmp_dwell_seconds": 5}, None) <= 1.0


# --------------------------------------------------------------------------- the worker records every model


class Fixed(ImportanceScorer):
    def __init__(self, value: float, name: str) -> None:
        self.value, self.name = value, name

    def predict_proba(self, features) -> float:
        return self.value


class Broken(ImportanceScorer):
    name = "broken"

    def predict_proba(self, features) -> float:
        raise RuntimeError("damaged model file")


def snapshot(seq: int):
    h = mock_header(seq=seq)
    return read_message(io.BytesIO(encode_message(h, mock_payload())))


def test_champion_and_shadows_are_all_recorded_and_a_broken_shadow_changes_nothing(conn) -> None:
    shadows = [ShadowModel(Fixed(0.9, "high"), "high", "logreg", fallback_cutoff=0.5),
               ShadowModel(Fixed(0.1, "low"), "low", "mlp", fallback_cutoff=0.5),
               ShadowModel(Broken(), "broken", "random_forest")]
    pipeline = SnapshotPipeline(conn, Fixed(0.7, "lightgbm-champ"), embed=False, shadows=shadows, champion_algorithm="lightgbm")
    pipeline(snapshot(1))
    [record] = list_context_records(conn)
    assert record.decision == "save"
    decisions = {d.model: d for d in list_model_decisions(conn, record.id)}
    assert set(decisions) == {"lightgbm-champ", "high", "low"}
    assert decisions["lightgbm-champ"].role == "champion" and decisions["lightgbm-champ"].would_save
    assert decisions["high"].would_save and not decisions["low"].would_save
    assert decisions["high"].role == "shadow" and decisions["high"].cutoff == 0.5
    assert decisions["low"].latency_us is not None


def test_shadow_cutoffs_refill_from_recorded_scores_after_a_restart(conn) -> None:
    shadow = lambda: ShadowModel(Fixed(0.4, "s"), "s", "logreg")
    first = SnapshotPipeline(conn, Fixed(0.7, "c"), embed=False, shadows=[shadow()], redundancy_window_s=0)
    for seq in range(1, 251):
        first(snapshot(seq))
    second_shadow = shadow()
    second = SnapshotPipeline(conn, Fixed(0.7, "c"), embed=False, shadows=[second_shadow], keep_rate=0.3)
    second.restore_state(now=datetime.now(timezone.utc))
    assert len(second_shadow.scores) == 250  # from model_decisions, not rescored


# --------------------------------------------------------------------------- choosing the champion


def saved_version(conn, tmp_path, algorithm: str, version: str, *, active: bool = False) -> None:
    scorer, _ = registry.train_scorer(algorithm, frame())
    registry.save_scorer(scorer, algorithm, version, tmp_path)
    insert_model_version(conn, ModelVersion(version=version, algorithm=algorithm, is_active=active, metrics={"keep_rate": 0.3}))


def test_make_champion_switches_records_the_event_and_keeps_the_old_one(conn, tmp_path) -> None:
    saved_version(conn, tmp_path, "lightgbm", "lightgbm-a", active=True)
    saved_version(conn, tmp_path, "logreg", "logreg-a")
    result = make_champion(conn, "logreg-a", versions_dir=tmp_path, reason="test")
    assert result == {"champion": "logreg-a", "changed": True, "previous": "lightgbm-a", "algorithm": "logreg"}
    assert get_active_model_version(conn).version == "logreg-a"
    assert len(list_model_versions(conn)) == 2  # nothing deleted
    event = conn.execute("SELECT from_version, to_version, reason FROM champion_events").fetchone()
    assert tuple(event) == ("lightgbm-a", "logreg-a", "test")
    # the old champion now runs in shadow mode, the new one does not shadow itself
    assert [s.model for s in load_shadows(conn, champion_version="logreg-a", versions_dir=tmp_path)] == [
        "rules-1.0", "lightgbm-lightgbm-a"]


def test_make_champion_refuses_a_version_that_cannot_load(conn, tmp_path) -> None:
    saved_version(conn, tmp_path, "lightgbm", "lightgbm-a", active=True)
    insert_model_version(conn, ModelVersion(version="logreg-missing", algorithm="logreg"))
    with pytest.raises(ValueError, match="cannot be loaded"):
        make_champion(conn, "logreg-missing", versions_dir=tmp_path)
    assert get_active_model_version(conn).version == "lightgbm-a"


def test_the_cycle_never_replaces_a_chosen_champion_with_another_family(conn, tmp_path) -> None:
    seed_blocks(conn)
    first = run_cycle(conn, cfg(tmp_path), now=NOW)
    assert first.error is None
    chosen = next(v.version for v in list_model_versions(conn) if v.algorithm == "mlp")
    make_champion(conn, chosen, versions_dir=tmp_path / "versions")

    second = run_cycle(conn, cfg(tmp_path, require_new_labels=False), now=NOW + timedelta(hours=1))
    assert second.error is None
    assert second.stages["validation"]["deployed"] is False
    assert "challenger: the champion is mlp" in second.stages["validation"]["reason"]
    assert registry.algorithm_of(get_active_model_version(conn).algorithm) == "mlp"
    assert {r.get("error") for r in second.stages["challengers"].values()} == {None}


# --------------------------------------------------------------------------- no data loss on migration


def test_a_migration_backs_the_database_up_first(tmp_path, monkeypatch) -> None:
    from storage import database

    path = tmp_path / "context.db"
    c = connect(str(path))
    migrate(c)
    c.execute("INSERT INTO model_versions (version, created_at) VALUES ('keep-me', 0)")
    c.commit()
    monkeypatch.setattr(database, "MIGRATIONS", database.MIGRATIONS + [(99, "test", "CREATE TABLE t99 (x INTEGER);")])
    assert migrate(c) == [99]
    c.close()
    [backup] = list((tmp_path / "backups").glob("context.bak-before-v99-*.db"))
    b = connect(str(backup))
    assert b.execute("SELECT version FROM model_versions").fetchone()[0] == "keep-me"
    assert b.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 4
    b.close()
