"""Tests for the ADWIN-based concept-drift detector (label-dependent error stream).

ADWIN is deterministic for a given input sequence, so these assertions are reproducible.
"""

from __future__ import annotations

import pytest

from adaptation.drift_detection import ErrorDriftDetector


def feed(detector: ErrorDriftDetector, values) -> list[int]:
    """Return the stream indices (1-based) at which drift was flagged."""
    return [i for i, v in enumerate(values, start=1) if detector.detect(v)]


def test_stationary_low_error_stream_has_no_drift() -> None:
    detector = ErrorDriftDetector()
    flags = feed(detector, [0.0] * 1000)
    assert flags == []
    assert detector.n_drifts == 0
    assert detector.estimated_error == pytest.approx(0.0, abs=1e-6)


def test_stationary_high_error_stream_has_no_drift() -> None:
    detector = ErrorDriftDetector()
    flags = feed(detector, [1.0] * 1000)
    assert flags == []
    assert detector.estimated_error == pytest.approx(1.0, abs=1e-6)


def test_error_rate_increase_is_detected() -> None:
    detector = ErrorDriftDetector()
    flags = feed(detector, [0.0] * 1000 + [1.0] * 1000)
    assert flags, "expected drift after the error rate jumped"
    # Drift is flagged shortly after the regime change at index 1000, not before it.
    assert all(idx > 1000 for idx in flags)
    assert detector.n_drifts == len(flags)
    assert detector.last_drift_index == flags[-1]


def test_error_rate_decrease_is_detected() -> None:
    detector = ErrorDriftDetector()
    flags = feed(detector, [1.0] * 1000 + [0.0] * 1000)
    assert flags
    assert all(idx > 1000 for idx in flags)


def test_detect_returns_bool_and_tracks_state() -> None:
    detector = ErrorDriftDetector()
    result = detector.detect(0.0)
    assert result is False
    assert detector.drift_detected is False
    assert detector.n_updates == 1


def test_observe_prediction_computes_misclassification_error() -> None:
    detector = ErrorDriftDetector()
    # Correct predictions -> error 0, so the estimate stays near 0.
    for _ in range(200):
        detector.observe_prediction(y_true=1, y_proba=0.9)
        detector.observe_prediction(y_true=0, y_proba=0.1)
    assert detector.estimated_error == pytest.approx(0.0, abs=1e-6)


def test_observe_prediction_detects_a_model_that_starts_failing() -> None:
    detector = ErrorDriftDetector()
    drift = False
    # First the model is right; then it flips to always wrong.
    for _ in range(600):
        drift = detector.observe_prediction(y_true=1, y_proba=0.9) or drift
    for _ in range(600):
        drift = detector.observe_prediction(y_true=1, y_proba=0.1) or drift  # now wrong
    assert drift is True
    assert detector.n_drifts >= 1


def test_reset_clears_history() -> None:
    detector = ErrorDriftDetector()
    feed(detector, [0.0] * 500 + [1.0] * 500)
    detector.reset()
    assert detector.n_updates == 0
    assert detector.n_drifts == 0
    assert detector.last_drift_index is None
    assert detector.drift_detected is False


def test_invalid_delta_rejected() -> None:
    with pytest.raises(ValueError):
        ErrorDriftDetector(delta=0.0)
    with pytest.raises(ValueError):
        ErrorDriftDetector(delta=1.0)


def test_smaller_delta_is_not_more_trigger_happy_on_noise() -> None:
    # A conservative delta must not flag drift on a stationary noisy stream.
    import random

    rng = random.Random(0)
    noise = [1.0 if rng.random() < 0.2 else 0.0 for _ in range(2000)]
    conservative = ErrorDriftDetector(delta=0.0001)
    assert feed(conservative, noise) == []
