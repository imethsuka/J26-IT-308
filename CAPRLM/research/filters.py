"""The five filters compared everywhere (eval harness, API, UI), under one set of names."""

from __future__ import annotations

from shared.privacy_filter import PrivacyFilter
from shared.rule_based_filter import RuleBasedPrivacyFilter

from research.baselines import AnonymizationFilter, PseudonymizationFilter, StaticMaskingFilter
from research.caprlm_filter import CAPRLMFilter
from research.detection import Detector
from research.model import RiskModel

# Display order: CAPRLM first, then the baselines in the order the proposal lists them.
FILTER_NAMES = ("caprlm", "rule_based", "static_masking", "anonymization", "pseudonymization")


def build_filters(model: RiskModel, detector: Detector) -> dict[str, PrivacyFilter]:
    return {
        "caprlm": CAPRLMFilter(model, detector),
        "rule_based": RuleBasedPrivacyFilter(),
        "static_masking": StaticMaskingFilter(detector),
        "anonymization": AnonymizationFilter(detector),
        "pseudonymization": PseudonymizationFilter(detector),
    }
