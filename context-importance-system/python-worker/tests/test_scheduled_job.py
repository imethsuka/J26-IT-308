"""Tests for the scheduled adaptation job: host gating, the ordered five-stage cycle, the skip and
error branches, and structured summary logging.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from adaptation.host_state import HostReadiness, check_host_ready
from adaptation.scheduled_job import (
    CycleConfig,
    RunSummary,
    _temporal_split,
    log_summary,
    run_cycle,
    run_if_ready,
)
from features.feature_assembly import SensingEvent, assemble_features
from storage.context_store import ContextRecord, get_active_model_version, insert_context_record
from storage.database import connect, migrate

BASE = datetime(2026, 9, 20, 9, 0, 0, tzinfo=timezone.utc)
HORIZON = timedelta(hours=48)
NOW = BASE + timedelta(hours=200)
PROD_APPS = ["code", "excel", "winword"]
MEDIA_APPS = ["vlc", "spotify", "steam"]

# Label construction needs a full 48h horizon of following data per record, so the fixtures lay
# records out in blocks: a train block, a >=48h gap, a holdout block, then a trailing block that
# exists only to cover the holdout block's horizon (it stays censored itself).
TRAIN_BLOCK, HOLDOUT_BLOCK, TAIL_BLOCK = 0.0, 96.0, 152.0
N_TRAIN, N_HOLDOUT, N_TAIL = 24, 8, 2
N_LABELLABLE = N_TRAIN + N_HOLDOUT          # 32: the tail stays censored
N_IMPORTANT = (N_TRAIN + N_HOLDOUT) // 2    # retrieval fires for every other record


def seed(conn, n=N_TRAIN, *, start_hours=0.0, spacing_hours=1.0, seed_val=0) -> None:
    """Insert ``n`` records starting at ``BASE + start_hours``, ``spacing_hours`` apart."""
    rng = np.random.default_rng(seed_val)
    for j in range(n):
        productive = j % 2 == 0
        app = str(rng.choice(PROD_APPS if productive else MEDIA_APPS))
        ts = BASE + timedelta(hours=start_hours + spacing_hours * j)
        ev = SensingEvent(
            ts=ts, app=app, title=f"unique-{start_hours}-{j}", domain=None,
            dwell_seconds=float(rng.uniform(0, 120)), key_presses=int(rng.integers(0, 40)), window_seconds=10,
        )
        feats = assemble_features(ev, embedding=None, recent_context_history=[])
        emb = rng.standard_normal(512).astype(np.float32)
        insert_context_record(conn, ContextRecord(
            ts=ev.ts, decision="save" if productive else "discard", features=feats,
            embedding=emb, app=app, domain=None, title=ev.title,
        ))


def seed_blocks(conn) -> None:
    """The standard three-block layout: 24 train-side + 8 holdout-side labellable, 2 censored."""
    seed(conn, N_TRAIN, start_hours=TRAIN_BLOCK, seed_val=0)
    seed(conn, N_HOLDOUT, start_hours=HOLDOUT_BLOCK, seed_val=1)
    seed(conn, N_TAIL, start_hours=TAIL_BLOCK, seed_val=2)


def retrieval_by_app(record):
    """Mock retrieval oracle (Member 3): pretend productivity apps were used downstream.

    The rule-based baseline already favours productivity apps, so it scores perfectly here.
    """
    return record.app in PROD_APPS


def retrieval_media(record):
    """Mock oracle the rule-based baseline gets wrong (it penalises media apps), so a trained
    candidate can beat it at cold start."""
    return record.app in MEDIA_APPS


def cfg(tmp_path, **kw) -> CycleConfig:
    defaults = dict(prototype_k=3, min_train_rows=10, holdout_fraction=0.25,
                    num_boost_round=40, versions_dir=tmp_path / "versions", retrieval_fn=retrieval_media)
    defaults.update(kw)
    return CycleConfig(**defaults)


@pytest.fixture
def conn():
    c = connect(":memory:")
    migrate(c)
    yield c
    c.close()


# --------------------------------------------------------------------------- host gating

def test_host_ready_requires_idle_and_ac() -> None:
    ready = check_host_ready(idle_threshold_seconds=300, idle_fn=lambda: 600, power_fn=lambda: "ac")
    assert ready.ready and ready.idle and ready.on_ac_power

    on_battery = check_host_ready(idle_threshold_seconds=300, idle_fn=lambda: 600, power_fn=lambda: "battery")
    assert not on_battery.ready and "AC" in on_battery.reason

    active_user = check_host_ready(idle_threshold_seconds=300, idle_fn=lambda: 10, power_fn=lambda: "ac")
    assert not active_user.ready and "not idle" in active_user.reason


def test_unknown_power_allowed_by_default() -> None:
    r = check_host_ready(idle_threshold_seconds=300, idle_fn=lambda: 600, power_fn=lambda: "unknown")
    assert r.ready is True
    blocked = check_host_ready(idle_threshold_seconds=300, idle_fn=lambda: 600, power_fn=lambda: "unknown",
                               unknown_power_is_ok=False)
    assert blocked.ready is False


def test_no_require_ac_ignores_power() -> None:
    r = check_host_ready(idle_threshold_seconds=300, require_ac=False, idle_fn=lambda: 600, power_fn=lambda: "battery")
    assert r.ready is True


def test_run_if_ready_skips_when_not_ready(conn, tmp_path) -> None:
    seed_blocks(conn)
    not_ready = HostReadiness(ready=False, idle=False, on_ac_power=True, seconds_idle=5, power="ac", reason="not idle (5s)")
    summary = run_if_ready(conn, cfg(tmp_path), now=NOW, host_readiness=not_ready)
    assert summary.ran is False
    assert summary.skip_reason == "not idle (5s)"
    assert summary.stages == {}  # nothing ran
    # No labelling happened.
    from storage.context_store import list_context_records
    assert all(r.label is None for r in list_context_records(conn))


def test_run_if_ready_runs_when_forced(conn, tmp_path) -> None:
    seed_blocks(conn)
    summary = run_if_ready(conn, cfg(tmp_path), now=NOW, force=True,
                           host_readiness=HostReadiness(False, False, True, 5, "ac", "not idle"))
    assert summary.ran is True
    assert "label_construction" in summary.stages


# --------------------------------------------------------------------------- full cycle order

def test_full_cycle_runs_all_stages_in_order_and_deploys(conn, tmp_path) -> None:
    seed_blocks(conn)
    summary = run_cycle(conn, cfg(tmp_path), now=NOW)

    assert summary.error is None
    # All five stages present.
    assert list(summary.stages.keys()) == [
        "label_construction", "prototype_learning", "retrain", "drift_check", "validation",
    ]
    assert summary.contexts_labelled == N_LABELLABLE   # the tail block is not labelled
    assert summary.stages["label_construction"]["important"] == N_IMPORTANT
    # Of the two tail records, the one at +152h is due but censored; the one at +153h is not yet
    # due at NOW (+200h), so it is not counted as censored -- it simply has not come up for labelling.
    assert summary.stages["label_construction"]["censored"] == 1
    assert summary.stages["label_construction"]["label_version"] == "v3/h48"
    assert summary.stages["prototype_learning"]["learned"] is True
    assert summary.stages["retrain"]["trained"] is True
    # The blocks are a full horizon apart, so nothing needs embargoing here.
    assert summary.stages["retrain"]["n_embargoed"] == 0
    assert summary.stages["retrain"]["n_train"] == N_TRAIN
    assert summary.stages["retrain"]["n_holdout"] == N_HOLDOUT
    # Cold start: no deployed model, so drift is skipped and the candidate is validated against the
    # rule-based baseline the worker is serving -- which it beats on this oracle.
    assert summary.stages["drift_check"]["error_drift_detected"] is None
    assert summary.stages["validation"]["compared_against"] == "rules-1.0"
    assert summary.stages["validation"]["current_score"] is not None
    assert summary.decision == "deploy"
    assert get_active_model_version(conn).version == summary.candidate_version

    # Artifacts persisted under the versioned dir.
    version_dir = tmp_path / "versions" / summary.candidate_version
    assert (version_dir / "model.txt").exists()
    assert (version_dir / "prototypes.npy").exists()
    assert summary.duration_seconds is not None


def test_second_cycle_uses_incumbent_and_runs_drift_check(conn, tmp_path) -> None:
    seed_blocks(conn)
    first = run_cycle(conn, cfg(tmp_path), now=NOW)
    assert first.decision == "deploy"

    # New contexts arrive, plus a new tail that covers their horizon. That also uncovers the
    # previously-censored tail block, so 2 + 8 records become labellable.
    seed(conn, 8, start_hours=200.0, seed_val=3)
    seed(conn, 2, start_hours=260.0, seed_val=4)
    now2 = BASE + timedelta(hours=320)

    second = run_cycle(conn, cfg(tmp_path), now=now2)
    assert second.stages["label_construction"]["contexts_labelled"] == N_TAIL + 8  # only the new ones
    assert second.stages["drift_check"]["error_drift_detected"] in (True, False)   # incumbent evaluated
    assert second.stages["drift_check"]["n_evaluated"] == N_TAIL + 8
    assert second.stages["validation"]["current_score"] is not None  # compared against the incumbent
    assert second.stages["validation"]["compared_against"].endswith(first.candidate_version)
    assert second.decision in ("deploy", "keep")


def test_cold_start_keeps_the_baseline_when_the_candidate_does_not_beat_it(conn, tmp_path) -> None:
    seed_blocks(conn)
    # The baseline predicts this oracle perfectly, so the candidate can at best tie: keep serving it.
    summary = run_cycle(conn, cfg(tmp_path, retrieval_fn=retrieval_by_app), now=NOW)
    assert summary.stages["retrain"]["trained"] is True
    assert summary.stages["validation"]["compared_against"] == "rules-1.0"
    assert summary.stages["validation"]["current_score"] == 1.0
    assert summary.decision == "keep"
    assert get_active_model_version(conn) is None  # the candidate is recorded, but not activated


def test_cycle_skips_retrain_when_nothing_new_was_labelled(conn, tmp_path) -> None:
    seed_blocks(conn)
    first = run_cycle(conn, cfg(tmp_path), now=NOW)
    assert first.stages["retrain"]["trained"] is True

    # Same data, a later run (e.g. the next --loop poll while the host is still idle).
    again = run_cycle(conn, cfg(tmp_path), now=NOW + timedelta(minutes=5))
    assert again.contexts_labelled == 0
    assert again.stages["retrain"]["trained"] is False
    assert "no records labelled since" in again.stages["retrain"]["reason"]
    assert again.decision == "skipped"
    assert not (tmp_path / "versions" / again.candidate_version).exists()

    forced = run_cycle(conn, cfg(tmp_path, require_new_labels=False), now=NOW + timedelta(minutes=10))
    assert forced.stages["retrain"]["trained"] is True


def test_skip_reason_explains_censoring(conn, tmp_path) -> None:
    # 20 hours of data: everything due is censored under a 48h horizon, so nothing can be labelled.
    seed(conn, 21, start_hours=0.0)
    summary = run_cycle(conn, cfg(tmp_path), now=NOW)
    assert summary.contexts_labelled == 0
    assert summary.stages["label_construction"]["censored"] == 21
    assert summary.stages["label_construction"]["data_span_hours"] == 20.0
    reason = summary.stages["retrain"]["reason"]
    assert "waiting for a full 48h of later data" in reason and "spans 20.0h" in reason


# --------------------------------------------------------------------------- split embargo

@dataclass
class Row:
    id: int
    ts: datetime
    label: int = 0


class TestCycleSplitEmbargo:
    def test_train_records_are_a_horizon_clear_of_holdout_start(self) -> None:
        rows = [Row(id=i, ts=BASE + timedelta(hours=12 * i)) for i in range(10)]
        train, holdout = _temporal_split(rows, 0.3, horizon=HORIZON)

        holdout_start = min(r.ts for r in holdout)
        assert train, "the embargo must not empty this split"
        assert max(r.ts for r in train) < holdout_start           # chronological
        for r in train:
            assert r.ts + HORIZON <= holdout_start, f"record at {r.ts} is inside the embargo"
        assert len(train) < 7                                      # the embargo dropped rows

    def test_zero_horizon_keeps_the_plain_chronological_split(self) -> None:
        rows = [Row(id=i, ts=BASE + timedelta(hours=12 * i)) for i in range(10)]
        train, holdout = _temporal_split(rows, 0.3, horizon=timedelta(0))
        assert len(train) == 7 and len(holdout) == 3


def test_cycle_skips_when_the_embargo_empties_the_train_split(conn, tmp_path) -> None:
    # Dense data: every labellable record sits within 48h of the holdout start, so no training
    # row is horizon-clear. The cycle must skip rather than train on leaked labels.
    seed(conn, 30, start_hours=0.0, spacing_hours=0.5)
    seed(conn, 2, start_hours=60.0, seed_val=9)   # tail so the dense block is labellable
    summary = run_cycle(conn, cfg(tmp_path), now=NOW)

    assert summary.stages["retrain"]["trained"] is False
    assert "embargo" in summary.stages["retrain"]["reason"]
    assert summary.decision == "skipped"


# --------------------------------------------------------------------------- skip branch

def test_cycle_skips_retrain_without_enough_labelled_data(conn, tmp_path) -> None:
    seed(conn, 6, start_hours=0.0)        # below min_train_rows
    seed(conn, 2, start_hours=60.0, seed_val=9)
    summary = run_cycle(conn, cfg(tmp_path, min_train_rows=20), now=NOW)
    assert summary.stages["retrain"]["trained"] is False
    assert "need >=" in summary.stages["retrain"]["reason"]
    assert "validation" not in summary.stages  # gate not reached
    assert summary.decision == "skipped"


def test_cycle_skips_when_single_class(conn, tmp_path) -> None:
    seed_blocks(conn)
    # Retrieval never fires and no revisits -> every label is 0 (single class).
    summary = run_cycle(conn, cfg(tmp_path, retrieval_fn=lambda r: False), now=NOW)
    assert summary.stages["retrain"]["trained"] is False
    assert summary.decision == "skipped"


# --------------------------------------------------------------------------- error handling

def test_cycle_captures_errors_without_raising(conn, tmp_path) -> None:
    seed_blocks(conn)
    # An active incumbent must exist for the resolver (and thus the raising loader) to be called.
    from storage.context_store import ModelVersion, insert_model_version
    insert_model_version(conn, ModelVersion(version="incumbent", is_active=True))

    def boom(_version):
        raise RuntimeError("incumbent load exploded")

    summary = run_cycle(conn, cfg(tmp_path), now=NOW, load_incumbent_fn=boom)
    assert summary.error is not None
    assert "incumbent load exploded" in summary.error
    assert summary.decision == "error"


# --------------------------------------------------------------------------- summary logging

def test_log_summary_writes_json_line(conn, tmp_path) -> None:
    seed_blocks(conn)
    summary = run_cycle(conn, cfg(tmp_path), now=NOW)
    log_path = tmp_path / "runs.jsonl"
    log_summary(summary, log_path)
    log_summary(summary, log_path)  # appends

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["decision"] == "deploy"
    assert record["contexts_labelled"] == N_LABELLABLE
    assert record["duration_seconds"] is not None
    assert record["stages"]["retrain"]["trained"] is True
    assert record["candidate_version"] == summary.candidate_version


def test_run_summary_to_dict_is_json_serializable() -> None:
    s = RunSummary(started_at=NOW).finalize(NOW + timedelta(seconds=2))
    blob = json.dumps(s.to_dict())
    assert "started_at" in blob and "duration_seconds" in blob
