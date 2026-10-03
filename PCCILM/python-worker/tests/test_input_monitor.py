"""Tests for the label-free input-distribution monitor (covariate drift)."""

from __future__ import annotations

import pytest

from adaptation.input_monitor import InputDistributionMonitor


def feats(app: str, sim: float | None = 0.2) -> dict:
    return {"ctx_app": app, "vis_proto_similarity": sim}


def observe_many(monitor: InputDistributionMonitor, app: str, n: int, sim: float | None = 0.2):
    status = None
    for _ in range(n):
        status = monitor.observe(feats(app, sim))
    return status


# --------------------------------------------------------------------------- readiness / warm-up

def test_not_ready_until_reference_and_window_filled() -> None:
    monitor = InputDistributionMonitor(window_size=10)
    status = monitor.observe(feats("code"))
    assert status.ready is False
    assert status.drift is False
    assert monitor.has_reference is False

    observe_many(monitor, "code", 9)  # 10 total -> reference frozen, window full
    assert monitor.has_reference is True
    assert monitor.status().ready is True


# --------------------------------------------------------------------------- stable stream

def test_stable_stream_shows_no_drift() -> None:
    monitor = InputDistributionMonitor(window_size=20)
    observe_many(monitor, "code", 20)          # reference: all productivity, sim 0.2
    status = observe_many(monitor, "code", 20)  # window identical
    assert status.ready is True
    assert status.app_mix_divergence == pytest.approx(0.0)
    assert status.similarity_shift == pytest.approx(0.0)
    assert status.drift is False


# --------------------------------------------------------------------------- app-mix drift

def test_app_mix_shift_is_flagged() -> None:
    monitor = InputDistributionMonitor(window_size=20)
    observe_many(monitor, "code", 20)              # reference: productivity
    status = observe_many(monitor, "vlc", 20)       # window: media
    assert status.app_mix_divergence == pytest.approx(1.0)  # disjoint distributions
    assert status.app_mix_drift is True
    assert status.drift is True


def test_partial_app_mix_shift_below_threshold_is_not_flagged() -> None:
    monitor = InputDistributionMonitor(window_size=20, app_mix_threshold=0.5)
    observe_many(monitor, "code", 20)
    # Replace only a quarter of the window with media -> TVD 0.25 < 0.5.
    status = observe_many(monitor, "vlc", 5)
    assert status.app_mix_divergence == pytest.approx(0.25)
    assert status.app_mix_drift is False


# --------------------------------------------------------------------------- similarity drift

def test_similarity_shift_is_flagged_independently_of_app_mix() -> None:
    monitor = InputDistributionMonitor(window_size=20)
    observe_many(monitor, "code", 20, sim=0.2)       # reference sim 0.2
    status = observe_many(monitor, "code", 20, sim=0.8)  # same app, higher similarity
    assert status.app_mix_divergence == pytest.approx(0.0)
    assert status.app_mix_drift is False
    assert status.similarity_shift == pytest.approx(0.6)
    assert status.similarity_drift is True
    assert status.drift is True


def test_missing_similarity_disables_similarity_signal_but_keeps_app_mix() -> None:
    monitor = InputDistributionMonitor(window_size=20)
    observe_many(monitor, "code", 20, sim=None)
    status = observe_many(monitor, "vlc", 20, sim=None)
    assert status.similarity_shift is None
    assert status.similarity_drift is False
    assert status.app_mix_drift is True  # app-mix still monitored without labels or similarity


# --------------------------------------------------------------------------- rebase

def test_reset_reference_to_current_clears_drift() -> None:
    monitor = InputDistributionMonitor(window_size=20)
    observe_many(monitor, "code", 20)
    drifted = observe_many(monitor, "vlc", 20)
    assert drifted.drift is True

    monitor.reset_reference_to_current()  # accept the new regime
    assert monitor.status().drift is False
    assert monitor.status().app_mix_divergence == pytest.approx(0.0)


# --------------------------------------------------------------------------- explicit reference + injection

def test_explicit_set_reference() -> None:
    monitor = InputDistributionMonitor(window_size=10)
    monitor.set_reference([feats("code") for _ in range(10)])
    assert monitor.has_reference is True
    status = observe_many(monitor, "spotify", 10)  # media
    assert status.app_mix_drift is True


def test_injected_extractors_are_used() -> None:
    monitor = InputDistributionMonitor(
        window_size=10,
        category_fn=lambda f: f["bucket"],
        similarity_fn=lambda f: f["score"],
    )
    for _ in range(10):
        monitor.observe({"bucket": "A", "score": 0.1})
    status = None
    for _ in range(10):
        status = monitor.observe({"bucket": "B", "score": 0.1})
    assert status.app_mix_divergence == pytest.approx(1.0)
    assert status.app_mix_drift is True


def test_default_category_uses_app_category_mapping() -> None:
    # 'code' -> productivity, 'vlc' -> media, so a code->media shift is a full app-mix shift.
    monitor = InputDistributionMonitor(window_size=15)
    observe_many(monitor, "code", 15)
    status = observe_many(monitor, "vlc", 15)
    assert status.app_mix_divergence == pytest.approx(1.0)


def test_invalid_window_size_rejected() -> None:
    with pytest.raises(ValueError):
        InputDistributionMonitor(window_size=0)
