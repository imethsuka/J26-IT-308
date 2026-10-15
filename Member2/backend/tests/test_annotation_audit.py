"""
Regression tests for annotation_audit.py — summary counter consistency.

Covers:
  AUDIT-01  Summary screenshot_files_found matches individual record count
            when screenshots directory exists (all files present).
  AUDIT-02  Summary screenshot_files_found matches individual record count
            when screenshots directory is MISSING (root cause of prior bug).
  AUDIT-03  Summary screenshot_files_found matches when only some files exist.
  AUDIT-04  Group summary screenshot_exists values sum to overall total.
  AUDIT-05  screenshots_dir_exists field reflects actual directory presence.
  AUDIT-06  Missing SCREENSHOTS_DIR does not raise; audit completes with 0 found.
  AUDIT-07  All audit_records have screenshot_file_exists field (not absent/null).
  AUDIT-08  summary entity_flags_agree count matches individual records.
  AUDIT-09  summary label_consistent counts match individual records.
  AUDIT-10  Direct path check used (not rglob): _candidate existence matches record.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

import backend.scripts.annotation_audit as audit_mod
from backend.scripts.annotation_audit import (
    _audit_row,
    _annotation_flags_to_auto_label,
    _label_consistent_with_flags,
    FLAGGED_ROWS,
)


# ---------------------------------------------------------------------------
# Minimal synthetic fixtures
# ---------------------------------------------------------------------------

def _make_ann_row(**kwargs) -> pd.Series:
    defaults = {
        "screenshot_id": "test.jpg",
        "privacy_risk_label": "LOW",
        "application_context": "Test",
        "sensitive_entity_present": 0,
        "EMAIL": 0, "PHONE": 0, "URL": 0,
        "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


def _make_pipe_row(**kwargs) -> pd.Series:
    defaults = {
        "screenshot_id": "test.jpg",
        "application_context": "Unknown",
        "ocr_text_length": 100,
        "EMAIL": 0, "PHONE": 0, "URL": 0,
        "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


# ---------------------------------------------------------------------------
# Helpers for full-run mocking
# ---------------------------------------------------------------------------

def _patched_run(screenshots_dir_exists: bool, files_present: set[str] | None = None):
    """
    Run run_annotation_audit() with the DATASET_PATH, CANDIDATES_CSV,
    and SCREENSHOTS_DIR all mocked.

    screenshots_dir_exists: whether SCREENSHOTS_DIR.exists() returns True.
    files_present: set of screenshot_id filenames that exist.
                   If None and dir exists, all FLAGGED_ROWS files exist.
    """
    if files_present is None and screenshots_dir_exists:
        files_present = {r["screenshot_id"] for r in FLAGGED_ROWS}
    elif files_present is None:
        files_present = set()

    # Build minimal annotation DataFrame
    rows = []
    for flagged in FLAGGED_ROWS:
        rows.append({
            "screenshot_id": flagged["screenshot_id"],
            "privacy_risk_label": "LOW",
            "application_context": "Test",
            "sensitive_entity_present": 0,
            "EMAIL": 0, "PHONE": 0, "URL": 0,
            "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0,
        })
    df_ann = pd.DataFrame(rows)

    # Build minimal candidates DataFrame (same IDs, no entity flags)
    df_pipe = df_ann.copy()
    df_pipe["ocr_text_length"] = 100

    def _fake_dir_exists(self):
        # Called on SCREENSHOTS_DIR
        return screenshots_dir_exists

    def _fake_file_exists(self):
        # Called on SCREENSHOTS_DIR / screenshot_id
        return self.name in files_present

    with (
        patch.object(audit_mod, "DATASET_PATH", autospec=False,
                     new=MagicMock(exists=lambda: True,
                                  __str__=lambda s: "fake_ann.csv")),
        patch("pandas.read_csv", side_effect=[df_ann, df_pipe]),
        patch.object(audit_mod, "SCREENSHOTS_DIR", autospec=False,
                     new=_make_mock_dir(screenshots_dir_exists, files_present)),
    ):
        result = audit_mod.run_annotation_audit()
    return result


def _make_mock_dir(exists: bool, files_present: set[str]) -> MagicMock:
    """
    Return a Path-like MagicMock for SCREENSHOTS_DIR that:
    - .exists() → exists
    - truediv (/) returns a child mock whose .exists() checks files_present
    """
    mock_dir = MagicMock(spec=Path)
    mock_dir.exists.return_value = exists
    mock_dir.__str__ = lambda s: r"C:\fake\screenshots"

    def _child(name):
        child = MagicMock(spec=Path)
        child.name = str(name)
        child.exists.return_value = str(name) in files_present
        child.__str__ = lambda s: rf"C:\fake\screenshots\{name}"
        return child

    mock_dir.__truediv__ = lambda self, name: _child(name)
    return mock_dir


# ---------------------------------------------------------------------------
# AUDIT-01  All files present: summary matches record count
# ---------------------------------------------------------------------------

def test_audit01_summary_matches_records_all_present():
    """
    AUDIT-01 — When all screenshot files are present, summary
    screenshot_files_found equals the count of individual records
    with screenshot_file_exists=True.
    """
    result = _patched_run(screenshots_dir_exists=True)

    expected_count = sum(1 for r in result["audit_records"] if r["screenshot_file_exists"])
    assert result["summary"]["screenshot_files_found"] == expected_count, (
        f"summary={result['summary']['screenshot_files_found']} but "
        f"records say {expected_count}"
    )
    assert result["summary"]["screenshot_files_found"] == 16


# ---------------------------------------------------------------------------
# AUDIT-02  Screenshots directory missing: summary = 0, no crash
# ---------------------------------------------------------------------------

def test_audit02_missing_dir_summary_zero_no_crash():
    """
    AUDIT-02 — When SCREENSHOTS_DIR does not exist, audit completes without
    raising; screenshot_files_found = 0; all records show screenshot_file_exists=False.
    (This is the root cause of the original stale-counter bug.)
    """
    result = _patched_run(screenshots_dir_exists=False)

    # No crash — result is a dict
    assert isinstance(result, dict)
    assert result["summary"]["screenshot_files_found"] == 0
    for record in result["audit_records"]:
        assert record["screenshot_file_exists"] is False, (
            f"Expected False for {record['screenshot_id']} when dir missing"
        )


# ---------------------------------------------------------------------------
# AUDIT-03  Only some files exist: summary reflects partial count
# ---------------------------------------------------------------------------

def test_audit03_partial_files_summary_matches():
    """
    AUDIT-03 — When only a subset of files exist, summary count matches
    individual records.
    """
    present = {FLAGGED_ROWS[0]["screenshot_id"], FLAGGED_ROWS[1]["screenshot_id"]}
    result = _patched_run(screenshots_dir_exists=True, files_present=present)

    expected = sum(1 for r in result["audit_records"] if r["screenshot_file_exists"])
    assert result["summary"]["screenshot_files_found"] == expected
    assert result["summary"]["screenshot_files_found"] == 2


# ---------------------------------------------------------------------------
# AUDIT-04  Group summary screenshot_exists values sum to overall total
# ---------------------------------------------------------------------------

def test_audit04_group_summary_sums_to_total():
    """
    AUDIT-04 — Group screenshot_exists values sum to the overall
    screenshot_files_found.
    """
    result = _patched_run(screenshots_dir_exists=True)

    group_sum = sum(
        v["screenshot_exists"]
        for v in result["group_summary"].values()
    )
    assert group_sum == result["summary"]["screenshot_files_found"]


# ---------------------------------------------------------------------------
# AUDIT-05  screenshots_dir_exists field reflects truth
# ---------------------------------------------------------------------------

def test_audit05_screenshots_dir_exists_field_present():
    result_exists = _patched_run(screenshots_dir_exists=True)
    result_missing = _patched_run(screenshots_dir_exists=False)

    assert result_exists["screenshots_dir_exists"] is True
    assert result_missing["screenshots_dir_exists"] is False


# ---------------------------------------------------------------------------
# AUDIT-06  Missing dir: all records have screenshot_file_exists=False
# ---------------------------------------------------------------------------

def test_audit06_missing_dir_all_records_false():
    result = _patched_run(screenshots_dir_exists=False)
    for rec in result["audit_records"]:
        assert rec["screenshot_file_exists"] is False


# ---------------------------------------------------------------------------
# AUDIT-07  All records have screenshot_file_exists key
# ---------------------------------------------------------------------------

def test_audit07_all_records_have_exists_key():
    result = _patched_run(screenshots_dir_exists=True)
    for rec in result["audit_records"]:
        assert "screenshot_file_exists" in rec, (
            f"screenshot_file_exists missing for {rec['screenshot_id']}"
        )
        assert isinstance(rec["screenshot_file_exists"], bool)


# ---------------------------------------------------------------------------
# AUDIT-08  entity_flags_agree count matches individual records
# ---------------------------------------------------------------------------

def test_audit08_flags_agree_summary_matches_records():
    result = _patched_run(screenshots_dir_exists=True)
    expected = sum(
        1 for r in result["audit_records"]
        if r.get("comparison", {}).get("flag_agreement") is True
    )
    assert result["summary"]["entity_flags_agree"] == expected


# ---------------------------------------------------------------------------
# AUDIT-09  label_consistent counts match individual records
# ---------------------------------------------------------------------------

def test_audit09_label_consistent_counts_match():
    result = _patched_run(screenshots_dir_exists=True)
    records = result["audit_records"]
    yes = sum(
        1 for r in records
        if r.get("comparison", {}).get("label_consistent_with_annotation_flags") == "YES"
    )
    no = sum(
        1 for r in records
        if r.get("comparison", {}).get("label_consistent_with_annotation_flags") == "NO"
    )
    cond = sum(
        1 for r in records
        if r.get("comparison", {}).get("label_consistent_with_annotation_flags") == "CONDITIONAL"
    )
    assert result["summary"]["label_consistent_yes"] == yes
    assert result["summary"]["label_consistent_no"] == no
    assert result["summary"]["label_conditional"] == cond


# ---------------------------------------------------------------------------
# AUDIT-10  Direct path check: _candidate.exists() matches record value
# ---------------------------------------------------------------------------

def test_audit10_direct_path_check_matches_record(tmp_path):
    """
    AUDIT-10 — Verify that _audit_row uses direct path existence check
    (not rglob). Create a real temp directory with one file and confirm
    the record reflects the correct exists/not-exists values.
    """
    sid_present = "present-screenshot.jpg"
    sid_absent = "absent-screenshot.jpg"
    (tmp_path / sid_present).touch()

    original_dir = audit_mod.SCREENSHOTS_DIR
    try:
        audit_mod.SCREENSHOTS_DIR = tmp_path

        ann = _make_ann_row(screenshot_id=sid_present)
        pipe = _make_pipe_row(screenshot_id=sid_present)
        rec_present = _audit_row(sid_present, "G1", "test", "NEEDS_VISUAL_REVIEW", ann, pipe)
        rec_absent = _audit_row(sid_absent, "G1", "test", "NEEDS_VISUAL_REVIEW", ann, pipe)
    finally:
        audit_mod.SCREENSHOTS_DIR = original_dir

    assert rec_present["screenshot_file_exists"] is True
    assert rec_present["screenshot_path"] is not None
    assert rec_absent["screenshot_file_exists"] is False
    assert rec_absent["screenshot_path"] is None


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

def test_annotation_flags_to_auto_label_email():
    assert _annotation_flags_to_auto_label({"EMAIL": 1, "PHONE": 0, "URL": 0,
                                             "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0}) == "HIGH"


def test_annotation_flags_to_auto_label_email_and_phone():
    assert _annotation_flags_to_auto_label({"EMAIL": 1, "PHONE": 1, "URL": 0,
                                             "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0}) == "CRITICAL"


def test_annotation_flags_to_auto_label_all_zero():
    assert _annotation_flags_to_auto_label({"EMAIL": 0, "PHONE": 0, "URL": 0,
                                             "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0}) == "LOW"


def test_label_consistent_with_flags_yes():
    flags = {"EMAIL": 0, "PHONE": 0, "URL": 0, "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0}
    assert _label_consistent_with_flags("LOW", flags) == "YES"


def test_label_consistent_with_flags_no_email_medium():
    # EMAIL=1 should be HIGH, but label is MEDIUM → inconsistent
    flags = {"EMAIL": 1, "PHONE": 0, "URL": 0, "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0}
    assert _label_consistent_with_flags("MEDIUM", flags) == "NO"


def test_label_consistent_all_zero_medium_is_conditional():
    # All zero flags with MEDIUM label → CONDITIONAL (§6 visual context needed)
    flags = {"EMAIL": 0, "PHONE": 0, "URL": 0, "IP_ADDRESS": 0, "DATE": 0, "WINDOWS_PATH": 0}
    assert _label_consistent_with_flags("MEDIUM", flags) == "CONDITIONAL"
