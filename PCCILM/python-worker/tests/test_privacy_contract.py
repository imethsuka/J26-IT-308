"""The contract any ``PrivacyFilter`` must meet (adaptation/privacy.py). Member 2: add your filter to FILTERS and run this file.

The filters here are Member 1's: the dev pass-through, a block-all, and a toy digit redactor that only exists to
exercise the contract. None of them is a real privacy implementation.
"""

from __future__ import annotations

import re

import pytest

from adaptation.privacy import BlockAllFilter, FilterResult, PassThroughFilter, PrivacyContext, PrivacyFilter


class DigitRedactor:
    """Toy filter for tests: replaces digit runs. Not a privacy implementation."""

    version = "toy-digits/1"

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        redacted, n = re.subn(r"\d+", "[NUM]", text)
        return FilterResult(text=redacted, redactions=n, filter_version=self.version)


FILTERS = [PassThroughFilter, BlockAllFilter, DigitRedactor]

SAMPLES = [
    "Order 4417 shipped to 12 Main Street, card ending 4242",
    "",
    "plain words with no digits at all",
    "mixed 日本語 text and emoji \U0001f600 and tabs\tand\nnewlines",
    "x" * 50_000,
]
CTX = PrivacyContext(app="chrome", domain="shop.example.com", title="Checkout")


def assert_meets_contract(f: PrivacyFilter) -> None:
    assert isinstance(f, PrivacyFilter)
    assert isinstance(f.version, str) and f.version
    for sample in SAMPLES:
        first = f.filter(sample, CTX)
        assert isinstance(first, FilterResult)
        assert isinstance(first.text, str) and isinstance(first.blocked, bool)
        assert first.redactions >= 0
        assert first.filter_version  # stored with every entry, for audit
        assert f.filter(sample, CTX) == first  # deterministic: a later visit must filter the same text the same way
        if first.blocked:
            continue
        assert len(first.text) <= len(sample) + 1_000  # may mark things, must not blow the text up


@pytest.mark.parametrize("filter_class", FILTERS)
def test_filter_meets_the_contract(filter_class) -> None:
    assert_meets_contract(filter_class())


def test_block_all_blocks_and_returns_no_text() -> None:
    result = BlockAllFilter().filter("secret", CTX)
    assert result.blocked and result.text == ""


def test_digit_redactor_counts_what_it_changed() -> None:
    result = DigitRedactor().filter("Order 4417 and 12", CTX)
    assert result.text == "Order [NUM] and [NUM]" and result.redactions == 2
