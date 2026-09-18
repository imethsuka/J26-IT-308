"""Static-threshold baseline.

The simplest non-learned policy: save a context iff a single chosen feature is on the keep side of a
fixed threshold (default: dwell time >= 30s). No training, no feature combination — a deliberately
naive engineering baseline to compare the rule-based and learned models against.

Implements the same ``ImportanceScorer`` interface, so it drops into the same evaluation and
deployment code.
"""

from __future__ import annotations

from .base import Features, ImportanceScorer


class StaticThresholdScorer(ImportanceScorer):
    name = "static-threshold"

    def __init__(self, *, feature_key: str = "tmp_dwell_seconds", threshold: float = 30.0, direction: str = "ge") -> None:
        if direction not in ("ge", "le"):
            raise ValueError("direction must be 'ge' (keep when feature >= threshold) or 'le'")
        self.feature_key = feature_key
        self.threshold = threshold
        self.direction = direction
        self.name = f"static-threshold({feature_key}{'>=' if direction == 'ge' else '<='}{threshold:g})"

    def predict_proba(self, features: Features) -> float:
        value = features.get(self.feature_key)
        if value is None:
            return 0.0
        value = float(value)
        keep = value >= self.threshold if self.direction == "ge" else value <= self.threshold
        return 1.0 if keep else 0.0
