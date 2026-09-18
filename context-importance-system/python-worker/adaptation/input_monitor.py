"""Label-free input-distribution monitor (covariate-drift).

Watches the *inputs* the model sees, with no labels required, so it can run continuously as
contexts stream in — long before their labels exist. It tracks two cheap, interpretable signals
against a frozen reference:

- **app-mix**: the distribution over foreground app categories. Shift is the total-variation
  distance between the reference distribution and the current rolling window.
- **mean prototype-similarity**: the rolling mean of ``vis_proto_similarity``. Shift is the
  absolute change from the reference mean. A drop suggests incoming frames look unlike anything the
  current importance prototypes cover.

This is deliberately separate from ``drift_detection.ErrorDriftDetector`` (concept drift on the
labelled error stream). Covariate drift can appear with no change in error, and error drift can
appear with a stable input distribution; conflating them would hide which is happening.

This module only flags distribution shifts. It does not decide retraining. Whether a shift triggers
a retrain, and how that relates to any periodic schedule, is a separate policy not implemented here.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

Features = Mapping[str, Any]
CategoryFn = Callable[[Features], str]
SimilarityFn = Callable[[Features], "float | None"]


@dataclass(frozen=True)
class InputDriftStatus:
    ready: bool                       # reference frozen and the current window is full
    app_mix_divergence: float         # total-variation distance in [0, 1]
    app_mix_drift: bool
    similarity_shift: float | None    # |current mean - reference mean|, or None if unavailable
    similarity_drift: bool
    drift: bool                       # app_mix_drift or similarity_drift
    n_reference: int
    n_window: int


class InputDistributionMonitor:
    """
    Continuous covariate-drift monitor. Feed each context's feature dict to :meth:`observe`.

    A reference distribution is frozen from the first ``reference_size`` samples (or set explicitly
    via :meth:`set_reference`); thereafter the last ``window_size`` samples are compared against it.
    Defaults read the app category from ``ctx_app`` and similarity from ``vis_proto_similarity``;
    inject ``category_fn`` / ``similarity_fn`` to override.
    """

    def __init__(
        self,
        *,
        window_size: int = 200,
        reference_size: int | None = None,
        app_mix_threshold: float = 0.25,
        similarity_shift_threshold: float = 0.15,
        category_fn: CategoryFn | None = None,
        similarity_fn: SimilarityFn | None = None,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        self.window_size = window_size
        self.reference_size = reference_size or window_size
        self.app_mix_threshold = app_mix_threshold
        self.similarity_shift_threshold = similarity_shift_threshold
        self._category_fn = category_fn or _default_category
        self._similarity_fn = similarity_fn or _default_similarity

        self._window: deque[tuple[str, float | None]] = deque(maxlen=window_size)
        self._warmup: list[tuple[str, float | None]] = []
        self._reference_counts: Counter[str] | None = None
        self._reference_sim_mean: float | None = None
        self._n_reference = 0

    # ------------------------------------------------------------------ ingestion

    def observe(self, features: Features) -> InputDriftStatus:
        """Ingest one context; auto-freezes the reference during warm-up. Returns current status."""
        sample = (self._category_fn(features), self._similarity_fn(features))
        if self._reference_counts is None:
            self._warmup.append(sample)
            if len(self._warmup) >= self.reference_size:
                self._freeze_reference(self._warmup)
        self._window.append(sample)
        return self.status()

    def set_reference(self, samples: Iterable[Features]) -> None:
        """Explicitly set the reference baseline from a batch of feature dicts."""
        pairs = [(self._category_fn(f), self._similarity_fn(f)) for f in samples]
        if not pairs:
            raise ValueError("reference samples are empty")
        self._freeze_reference(pairs)

    def reset_reference_to_current(self) -> None:
        """Rebase the reference onto the current window (e.g. after a retrain accepts the new regime)."""
        if not self._window:
            raise RuntimeError("no observations yet to rebase onto")
        self._freeze_reference(list(self._window))

    # ------------------------------------------------------------------ status

    def status(self) -> InputDriftStatus:
        ready = self._reference_counts is not None and len(self._window) >= self.window_size
        if self._reference_counts is None:
            return InputDriftStatus(False, 0.0, False, None, False, False, self._n_reference, len(self._window))

        window_counts = Counter(cat for cat, _ in self._window)
        divergence = _total_variation(_distribution(self._reference_counts), _distribution(window_counts))

        window_sim = _mean([s for _, s in self._window if s is not None])
        if self._reference_sim_mean is None or window_sim is None:
            similarity_shift: float | None = None
        else:
            similarity_shift = abs(window_sim - self._reference_sim_mean)

        # Only raise drift once there is enough data to trust the comparison.
        app_mix_drift = ready and divergence >= self.app_mix_threshold
        similarity_drift = ready and similarity_shift is not None and similarity_shift >= self.similarity_shift_threshold

        return InputDriftStatus(
            ready=ready,
            app_mix_divergence=round(divergence, 6),
            app_mix_drift=app_mix_drift,
            similarity_shift=None if similarity_shift is None else round(similarity_shift, 6),
            similarity_drift=similarity_drift,
            drift=app_mix_drift or similarity_drift,
            n_reference=self._n_reference,
            n_window=len(self._window),
        )

    @property
    def has_reference(self) -> bool:
        return self._reference_counts is not None

    # ------------------------------------------------------------------ internals

    def _freeze_reference(self, pairs: list[tuple[str, float | None]]) -> None:
        self._reference_counts = Counter(cat for cat, _ in pairs)
        self._reference_sim_mean = _mean([s for _, s in pairs if s is not None])
        self._n_reference = len(pairs)


def _default_category(features: Features) -> str:
    # Reuse the single source of truth for app categories (lightweight import: no lightgbm/pandas).
    from models.vectorizer import category_of

    return category_of(features.get("ctx_app"))


def _default_similarity(features: Features) -> float | None:
    value = features.get("vis_proto_similarity")
    return None if value is None else float(value)


def _distribution(counts: Counter[str]) -> dict[str, float]:
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def _total_variation(p: dict[str, float], q: dict[str, float]) -> float:
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
