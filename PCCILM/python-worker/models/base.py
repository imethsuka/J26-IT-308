"""Shared importance-scorer interface.

Both the rule-based baseline and the learned Model 1 (LightGBM) implement this, so they are
interchangeable behind the same call. The unit of scoring is a single feature dict as produced by
``features.feature_assembly.assemble_features``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

Features = Mapping[str, Any]


class ImportanceScorer(ABC):
    """A scorer that maps one context's features to a probability of being important (0..1)."""

    #: Human-readable identifier, e.g. "rules-1.0". Model 1 will carry its trained version here.
    name: str = "scorer"

    @abstractmethod
    def predict_proba(self, features: Features) -> float:
        """Probability in [0, 1] that this context is important."""

    def predict(self, features: Features, threshold: float = 0.5) -> int:
        """Binary decision at ``threshold`` (1 = save, 0 = discard)."""
        return int(self.predict_proba(features) >= threshold)
