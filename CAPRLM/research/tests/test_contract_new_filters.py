"""Runs shared/contract_tests.py's checks, unchanged, against every new filter.

contract_tests.py hard-codes its own IMPLEMENTATIONS list and must not be edited, so the
per-implementation test functions are imported and applied to ours here. Any new check added
there that takes a single `impl` argument is picked up automatically.
"""

import inspect

import pytest

from shared import contract_tests

CONTRACT_CHECKS = [
    fn for name, fn in inspect.getmembers(contract_tests, inspect.isfunction)
    if name.startswith("test_") and list(inspect.signature(fn).parameters) == ["impl"]
]
NEW_FILTERS = ["caprlm", "static_masking", "anonymization", "pseudonymization"]


def test_contract_checks_were_found():
    assert len(CONTRACT_CHECKS) >= 4


@pytest.mark.parametrize("check", CONTRACT_CHECKS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("name", NEW_FILTERS)
def test_new_filter_passes_contract(name, check, filters):
    check(filters[name])
