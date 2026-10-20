"""
Contract tests: any PrivacyFilter implementation (the rule-based baseline, the dev
pass-through, and eventually Wijesingha's real model) must pass these. Run with pytest;
parametrized so the same checks apply to every implementation, not duplicated per-filter.

    pip install pytest
    pytest shared/contract_tests.py -v
"""

from __future__ import annotations

import pytest

from .passthrough_filter import PassthroughPrivacyFilter
from .rule_based_filter import RuleBasedPrivacyFilter
from .types import PrivacyContext, Source

SAMPLE_TEXT = "Contact john.doe@example.com or call 077-123-4567 about the invoice."

IMPLEMENTATIONS = [
    RuleBasedPrivacyFilter(),
    PassthroughPrivacyFilter(acknowledge_dev_mode_on_own_data=True),
]


def _ctx(**overrides) -> PrivacyContext:
    base = dict(app="chrome", domain="mail.example.com", url=None, title="Inbox", source=Source.UIA)
    base.update(overrides)
    return PrivacyContext(**base)


@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda i: type(i).__name__)
def test_filter_version_is_recorded(impl):
    result = impl.filter(SAMPLE_TEXT, _ctx())
    assert result.filter_version and result.filter_version != "unknown"


@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda i: type(i).__name__)
def test_blocked_text_is_never_stored(impl):
    result = impl.filter(SAMPLE_TEXT, _ctx())
    if result.blocked:
        assert result.text == ""


@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda i: type(i).__name__)
def test_output_length_bounded(impl):
    """Tokenisation can grow text a little (e.g. "x@y.com" -> "[EMAIL_1]") but not without
    limit — a filter that explodes output size on adversarial input is itself a bug."""
    result = impl.filter(SAMPLE_TEXT, _ctx())
    assert len(result.text) <= len(SAMPLE_TEXT) + 200


@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda i: type(i).__name__)
def test_audit_never_contains_raw_sensitive_text(impl):
    """The audit trail is category + action only — never the actual flagged content."""
    result = impl.filter(SAMPLE_TEXT, _ctx())
    for record in result.audit:
        assert "@" not in record.category
        assert "john.doe" not in record.category


def test_passthrough_refuses_without_explicit_acknowledgement():
    with pytest.raises(Exception):
        PassthroughPrivacyFilter(acknowledge_dev_mode_on_own_data=False)


def test_rule_based_redacts_known_entity_categories():
    """Sanity check that the baseline actually does *something* — not a claim about
    detection quality, which is measured properly once real evaluation data exists."""
    result = RuleBasedPrivacyFilter().filter(SAMPLE_TEXT, _ctx())
    assert "john.doe@example.com" not in result.text
    assert result.redactions >= 1
