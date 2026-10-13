"""Unit tests for protection.select_protection."""

import pytest
from backend.services.protection import select_protection


def _empty_detection():
    return {
        "EMAIL": {"matches": [], "count": 0},
        "PHONE": {"matches": [], "count": 0},
        "URL": {"matches": [], "count": 0},
        "IP_ADDRESS": {"matches": [], "count": 0},
        "DATE": {"matches": [], "count": 0},
        "WINDOWS_PATH": {"matches": [], "count": 0},
        "total_sensitive_entity_count": 0,
    }


def _detection_with_email():
    d = _empty_detection()
    d["EMAIL"] = {"matches": ["a@b.com"], "count": 1}
    d["total_sensitive_entity_count"] = 1
    return d


# ---------------------------------------------------------------------------
# Risk level → action mapping
# ---------------------------------------------------------------------------

def test_low_maps_to_monitor():
    result = select_protection("LOW", 0.8, _empty_detection())
    assert result["protection_action"] == "MONITOR"
    assert result["reversible"] is True


def test_medium_maps_to_blur_sensitive():
    result = select_protection("MEDIUM", 0.7, _empty_detection())
    assert result["protection_action"] == "BLUR_SENSITIVE"
    assert result["reversible"] is True


def test_high_maps_to_blur_and_alert():
    result = select_protection("HIGH", 0.6, _empty_detection())
    assert result["protection_action"] == "BLUR_AND_ALERT"
    assert result["reversible"] is True


def test_critical_maps_to_block():
    result = select_protection("CRITICAL", 0.9, _empty_detection())
    assert result["protection_action"] == "BLOCK"
    assert result["reversible"] is False


def test_unknown_risk_falls_back_to_monitor():
    result = select_protection("UNKNOWN_LEVEL", None, _empty_detection())
    assert result["protection_action"] == "MONITOR"


# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------

def test_severity_ordering():
    levels = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    severities = [select_protection(lvl, 0.5, _empty_detection())["severity"] for lvl in levels]
    assert severities == sorted(severities)


# ---------------------------------------------------------------------------
# Affected categories
# ---------------------------------------------------------------------------

def test_affected_categories_populated():
    result = select_protection("HIGH", 0.8, _detection_with_email())
    assert "EMAIL" in result["affected_categories"]


def test_affected_categories_empty_when_no_entities():
    result = select_protection("LOW", 0.9, _empty_detection())
    assert result["affected_categories"] == []


def test_total_sensitive_entity_count_not_in_affected():
    result = select_protection("MEDIUM", 0.5, _detection_with_email())
    assert "total_sensitive_entity_count" not in result["affected_categories"]


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------

def test_return_has_required_keys():
    result = select_protection("LOW", 0.8, _empty_detection())
    required = {
        "protection_action", "description", "reversible",
        "severity", "risk_level_received", "confidence_received", "affected_categories",
    }
    assert required.issubset(result.keys())
