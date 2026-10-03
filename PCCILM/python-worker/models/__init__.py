"""Models.

Responsibility: importance scorers behind a shared interface (``base.ImportanceScorer``:
``predict_proba(features) -> float``).
- ``rule_based`` — the interpretable rule-based baseline to compare against.
- the LightGBM importance classifier (Model 1) will implement the same interface here.
"""

from .base import ImportanceScorer  # noqa: F401
from .rule_based import RuleBasedImportanceScorer  # noqa: F401
from .static_threshold import StaticThresholdScorer  # noqa: F401

# ImportanceModel is imported lazily via models.lightgbm_model to avoid importing lightgbm/pandas
# for callers that only need the rule-based baseline.
