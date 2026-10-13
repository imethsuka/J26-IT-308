"""
Rule-based privacy risk baseline classifier.

This module provides a deterministic, threshold-based risk classifier
that operates on the same extracted features as the ML model.  It is
retained as a baseline for experimental comparison; it is NOT the
primary research model.

Rule rationale
--------------
Rules are ordered from highest to lowest risk.  Any match short-circuits.
- CRITICAL  : email AND phone both present  (multi-category PII combination)
- HIGH      : email OR phone OR IP address present  (direct identifiers)
- MEDIUM    : URL OR Windows-path present, or application context signals
              a browser/content-sharing context with any entity present
- LOW       : no rule matched

Limitation: several annotated rows in the current dataset have all-zero
entity flags yet carry MEDIUM or HIGH labels.  These cases are invisible
to this rule set and will be classified as LOW.  This is documented as a
known limitation of the rule-based approach.
"""

from __future__ import annotations

from collections.abc import Mapping

# Application contexts considered elevated-risk for MEDIUM promotion
_BROWSER_CONTEXTS = frozenset(
    {"Google Chrome", "YouTube", "Google Chrome + YouTube", "Brave", "Firefox", "Edge"}
)


def predict_rule_based(extracted_features: Mapping[str, object]) -> dict[str, object]:
    """
    Classify privacy risk using fixed threshold rules.

    Parameters
    ----------
    extracted_features:
        Dict produced by feature_extraction.extract_features().
        Expected keys: email_count, phone_count, url_count, ip_count,
        windows_path_count, sensitive_entity_count, application_context.

    Returns
    -------
    dict with keys: risk_level, confidence, model_type, rule_matched
    """

    def _count(key: str) -> int:
        val = extracted_features.get(key, 0)
        return int(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else 0

    email = _count("email_count")
    phone = _count("phone_count")
    url = _count("url_count")
    ip = _count("ip_count")
    windows_path = _count("windows_path_count")
    sensitive_total = _count("sensitive_entity_count")
    app_context = str(extracted_features.get("application_context") or "").strip()

    # Rule evaluation — ordered highest to lowest
    if email >= 1 and phone >= 1:
        risk_level = "CRITICAL"
        rule_matched = "email AND phone both present"
        confidence = 0.90

    elif email >= 1 or phone >= 1 or ip >= 1:
        risk_level = "HIGH"
        rule_matched = "email OR phone OR IP address present"
        confidence = 0.80

    elif (
        url >= 1
        or windows_path >= 1
        or (app_context in _BROWSER_CONTEXTS and sensitive_total >= 1)
    ):
        risk_level = "MEDIUM"
        rule_matched = "URL or Windows-path present, or browser context with entity"
        confidence = 0.65

    else:
        risk_level = "LOW"
        rule_matched = "no high-risk entity detected"
        confidence = 0.70

    return {
        "risk_level": risk_level,
        "confidence": confidence,
        "model_type": "rule_based_baseline",
        "rule_matched": rule_matched,
    }
