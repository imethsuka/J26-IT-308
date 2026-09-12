"""Concept-drift detection on Model 1's error stream (label-dependent).

Wraps River's ADWIN to watch the stream of prediction errors on *newly labelled* data. When the
mean error changes significantly, ADWIN flags drift. This needs labels, so it can only run once
label construction has produced ground truth (48h+ after capture).

This is one of two independent drift components (the other is
``input_monitor.InputDistributionMonitor``, which is label-free). They are intentionally separate:
this one detects *concept* drift (P(y|x) changed — the model is now wrong more often); the input
monitor detects *covariate* drift (P(x) changed — the incoming data looks different).

This module only *detects* drift. It does not decide what to do about it: whether a drift signal
triggers a retrain, and how that relates to any periodic retraining schedule, is a policy decision
that lives elsewhere. Nothing here schedules or performs retraining.
"""

from __future__ import annotations

from typing import Any


class ErrorDriftDetector:
    """
    ADWIN over a stream of error values. Feed one error per newly labelled example via
    :meth:`detect`; it returns ``True`` on the update where drift is flagged.

    The error can be anything whose mean shifting is meaningful — typically the 0/1
    misclassification indicator (1 = wrong), or a per-example loss. ``delta`` is ADWIN's
    confidence: smaller = fewer false alarms but slower detection (River's default is 0.002).
    """

    def __init__(self, *, delta: float = 0.002) -> None:
        if not 0.0 < delta < 1.0:
            raise ValueError("delta must be in (0, 1)")
        self.delta = delta
        self._adwin = self._new_adwin()
        self._n_updates = 0
        self._n_drifts = 0
        self._last_drift_index: int | None = None
        self._last_result = False

    def detect(self, error_value: float) -> bool:
        """Update ADWIN with one error value; return True iff drift is flagged on this update."""
        self._n_updates += 1
        self._adwin.update(float(error_value))
        detected = bool(self._adwin.drift_detected)
        if detected:
            self._n_drifts += 1
            self._last_drift_index = self._n_updates
        self._last_result = detected
        return detected

    def observe_prediction(self, y_true: int, y_proba: float, *, threshold: float = 0.5) -> bool:
        """
        Convenience for Model 1's error stream: turn a (label, predicted probability) pair into a
        0/1 misclassification error and feed it to :meth:`detect`.
        """
        predicted = 1 if y_proba >= threshold else 0
        error = 0.0 if predicted == int(y_true) else 1.0
        return self.detect(error)

    @property
    def drift_detected(self) -> bool:
        """Whether the most recent :meth:`detect` flagged drift."""
        return self._last_result

    @property
    def estimated_error(self) -> float:
        """ADWIN's current estimate of the mean error over its adaptive window."""
        return float(self._adwin.estimation)

    @property
    def window_width(self) -> int:
        return int(self._adwin.width)

    @property
    def n_updates(self) -> int:
        return self._n_updates

    @property
    def n_drifts(self) -> int:
        return self._n_drifts

    @property
    def last_drift_index(self) -> int | None:
        """1-based index (in the error stream) of the most recent drift, or None."""
        return self._last_drift_index

    def reset(self) -> None:
        """Forget all history and start a fresh window (e.g. after retraining on the new regime)."""
        self._adwin = self._new_adwin()
        self._n_updates = 0
        self._n_drifts = 0
        self._last_drift_index = None
        self._last_result = False

    def _new_adwin(self) -> Any:
        from river.drift import ADWIN

        return ADWIN(delta=self.delta)
