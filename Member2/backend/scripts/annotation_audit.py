"""
CAPRLM annotation audit tool — Phase 6.

Read-only review tool for the 16 flagged annotation rows from
consolidated_annotation_review_DRAFT.md.

For each flagged row:
  - Loads annotation flags from CAPRLM_annotation_50.csv (read-only).
  - Loads pipeline flags from processing_output/candidates.csv (read-only).
  - Compares annotation entity flags vs pipeline entity flags.
  - Checks whether the screenshot file exists on disk.
  - Classifies consistency of the label against annotation flags.
  - Produces a structured JSON report.

SAFETY:
  - Never modifies CAPRLM_annotation_50.csv.
  - Never modifies any candidates.csv.
  - Read-only throughout.

Usage (from Member2/ directory):
    .venv\\Scripts\\python -m backend.scripts.annotation_audit

Output:
    backend/data/annotation_audit_results.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from backend.services.train_model import DATASET_PATH

# Paths (read-only)
CANDIDATES_CSV = DATASET_PATH.parent / "processing_output" / "candidates.csv"
SCREENSHOTS_DIR = Path(r"C:\Users\ASUS\AppData\Local\DesktopResearchCollector\screenshots")

# Output
AUDIT_RESULTS_PATH = DATASET_PATH.parent / "annotation_audit_results.json"

# The 16 flagged screenshot_ids from consolidated_annotation_review_DRAFT.md
# with their annotated group and status
FLAGGED_ROWS: list[dict[str, str]] = [
    # G1: HIGH label with all-zero annotation entity flags
    {"screenshot_id": "02f39c8f-ce99-49ba-a2a3-aeea8504ae3a.jpg", "group": "G1",
     "issue": "HIGH label with all-zero annotation entity flags",
     "status": "NEEDS_VISUAL_REVIEW"},
    # G2: MEDIUM label with EMAIL=1 (should be HIGH per §3.1)
    {"screenshot_id": "02e86b37-426a-46c0-8adf-00fe16ff2f2d.jpg", "group": "G2",
     "issue": "MEDIUM label with EMAIL=1 (should be HIGH per guidelines §3.1)",
     "status": "NEEDS_VISUAL_REVIEW"},
    # G3: HIGH label where annotation flags support only MEDIUM
    {"screenshot_id": "00c019f8-0be1-426d-ace4-b8e3c6fa8d35.jpg", "group": "G3",
     "issue": "HIGH label where annotation flags support only MEDIUM (URL=1 only)",
     "status": "NEEDS_VISUAL_REVIEW"},
    # G4: LOW label with URL=1 (should be >= MEDIUM), plus flag conflict
    {"screenshot_id": "01bf28e2-c70b-4c32-82b4-31d786589ec3.jpg", "group": "G4",
     "issue": "LOW label with URL=1 (should be >= MEDIUM); pipeline also detects PHONE+IP",
     "status": "NEEDS_VISUAL_REVIEW"},
    # G5: MEDIUM with all-zero annotation entity flags (6 rows)
    {"screenshot_id": "00fa95dd-5a9e-458b-a071-fc2f8913e888.jpg", "group": "G5",
     "issue": "MEDIUM label with all-zero annotation entity flags (no notes column)",
     "status": "NEEDS_VISUAL_REVIEW"},
    {"screenshot_id": "01583a1d-7ccf-49d9-99c8-7ebf3b94cf01.jpg", "group": "G5",
     "issue": "MEDIUM label with all-zero annotation entity flags (no notes column)",
     "status": "NEEDS_VISUAL_REVIEW"},
    {"screenshot_id": "01a758f4-84ee-400c-a583-b7b70573c076.jpg", "group": "G5",
     "issue": "MEDIUM label with all-zero annotation entity flags (no notes column)",
     "status": "NEEDS_VISUAL_REVIEW"},
    {"screenshot_id": "024fd28f-aeb8-42dc-8484-9a8e2cd3ecf5.jpg", "group": "G5",
     "issue": "MEDIUM label with all-zero annotation entity flags (no notes column)",
     "status": "NEEDS_VISUAL_REVIEW"},
    {"screenshot_id": "033abfbe-b7f6-4c58-a769-8ffe082b38e4.jpg", "group": "G5",
     "issue": "MEDIUM label with all-zero annotation entity flags (no notes column)",
     "status": "NEEDS_VISUAL_REVIEW"},
    {"screenshot_id": "034e7035-d661-417e-8cab-6e3131d7a671.jpg", "group": "G5",
     "issue": "MEDIUM label with all-zero annotation entity flags (no notes column)",
     "status": "NEEDS_VISUAL_REVIEW"},
    # G6: MEDIUM with DATE=1 only, non-browser context
    {"screenshot_id": "00a3b7cb-0490-4a19-8f11-e37dad461566.jpg", "group": "G6",
     "issue": "MEDIUM label with DATE=1 only in non-browser context (Policy 2 dependency)",
     "status": "NEEDS_POLICY_DECISION"},
    # G7: LOW where pipeline detects PHONE (annotation PHONE=0)
    {"screenshot_id": "0024e4cb-aacb-44f7-8fe2-5c633f1ec67f.jpg", "group": "G7",
     "issue": "LOW label; annotation PHONE=0 but pipeline detects PHONE",
     "status": "NEEDS_VISUAL_REVIEW"},
    {"screenshot_id": "00c34dc6-612f-43cb-9b9f-297d92b6868a.jpg", "group": "G7",
     "issue": "LOW label; annotation PHONE=0 but pipeline detects PHONE",
     "status": "NEEDS_VISUAL_REVIEW"},
    {"screenshot_id": "00e7560f-f51a-4d8a-ad26-f6461fe5d6de.jpg", "group": "G7",
     "issue": "LOW label; annotation PHONE=0 but pipeline detects PHONE",
     "status": "NEEDS_VISUAL_REVIEW"},
    # G8: LOW where pipeline detects PHONE+IP (annotation all-zero)
    {"screenshot_id": "014635cf-027c-4bec-a6c2-304828e4e9f9.jpg", "group": "G8",
     "issue": "LOW label; annotation all-zero but pipeline detects PHONE+IP",
     "status": "NEEDS_VISUAL_REVIEW"},
    {"screenshot_id": "02488e7f-132f-4e80-b164-d34cf38424ff.jpg", "group": "G8",
     "issue": "LOW label; annotation all-zero but pipeline detects PHONE+IP",
     "status": "NEEDS_VISUAL_REVIEW"},
]

# Entity columns to compare
_ENTITY_COLS = ["EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"]


# ---------------------------------------------------------------------------
# Label consistency check (simplified — full check requires visual review)
# ---------------------------------------------------------------------------

def _annotation_flags_to_auto_label(flags: dict[str, int]) -> str:
    """
    Derive the expected label from annotation entity flags using
    the rule-based heuristic from annotation_guidelines_DRAFT.md §3.

    Note: 'MEDIUM due to visual context' (empty flags with visual reason)
    is a valid case per §6 but cannot be verified without screenshot access.
    """
    if flags.get("EMAIL", 0) >= 1 and flags.get("PHONE", 0) >= 1:
        return "CRITICAL"
    if flags.get("EMAIL", 0) >= 1 or flags.get("PHONE", 0) >= 1 or flags.get("IP_ADDRESS", 0) >= 1:
        return "HIGH"
    if flags.get("URL", 0) >= 1 or flags.get("WINDOWS_PATH", 0) >= 1:
        return "MEDIUM"
    if flags.get("DATE", 0) >= 1:
        return "LOW_OR_MEDIUM"  # policy-dependent
    return "LOW"


def _label_consistent_with_flags(label: str, flags: dict[str, int]) -> str:
    """
    Return 'YES', 'NO', or 'CONDITIONAL' based on whether the annotation
    label is consistent with the annotation entity flags.
    """
    expected = _annotation_flags_to_auto_label(flags)
    all_zero = all(v == 0 for v in flags.values())

    if all_zero and label in ("MEDIUM", "HIGH", "CRITICAL"):
        return "CONDITIONAL"  # requires visual annotation_notes (column absent)
    if expected == "LOW_OR_MEDIUM":
        return "CONDITIONAL"  # Policy 2 unresolved
    if label == expected:
        return "YES"
    # Check promotions: annotation flags might support lower risk, visual context higher
    flag_level_order = {"LOW": 0, "LOW_OR_MEDIUM": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    label_level_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    if label_level_order.get(label, 0) > flag_level_order.get(expected, 0):
        return "NO"  # label is higher than flags support
    return "NO"


# ---------------------------------------------------------------------------
# Audit a single row
# ---------------------------------------------------------------------------

def _audit_row(
    screenshot_id: str,
    group: str,
    issue: str,
    status: str,
    annotation_row: pd.Series | None,
    pipeline_row: pd.Series | None,
) -> dict[str, Any]:
    """Produce a structured audit record for one flagged screenshot."""

    record: dict[str, Any] = {
        "screenshot_id": screenshot_id,
        "group": group,
        "issue": issue,
        "reviewer_status": status,
    }

    # Check whether screenshot file exists.
    # Use a direct path lookup (O(1)) rather than rglob so the check never
    # silently fails when SCREENSHOTS_DIR itself is missing or inaccessible.
    _candidate = SCREENSHOTS_DIR / screenshot_id
    if not SCREENSHOTS_DIR.exists():
        # Directory absent — record the path problem explicitly rather than
        # propagating a FileNotFoundError that would abort the whole audit.
        record["screenshot_file_exists"] = False
        record["screenshot_path"] = None
        record["screenshot_dir_missing"] = str(SCREENSHOTS_DIR)
    else:
        record["screenshot_file_exists"] = _candidate.exists()
        record["screenshot_path"] = str(_candidate) if _candidate.exists() else None

    # --- Annotation data ---
    if annotation_row is None:
        record["annotation"] = {"found": False}
    else:
        ann_flags = {col: int(annotation_row.get(col, 0)) for col in _ENTITY_COLS}
        ann_label = str(annotation_row.get("privacy_risk_label", "UNKNOWN")).upper()
        record["annotation"] = {
            "found": True,
            "application_context": str(annotation_row.get("application_context", "")),
            "label": ann_label,
            "entity_flags": ann_flags,
            "sensitive_entity_present": int(annotation_row.get("sensitive_entity_present", 0)),
        }

    # --- Pipeline data ---
    if pipeline_row is None:
        record["pipeline"] = {"found": False}
    else:
        # Pipeline candidates.csv has per-category binary flags
        pipe_flags: dict[str, int] = {}
        for col in _ENTITY_COLS:
            val = pipeline_row.get(col, 0)
            if pd.isna(val):
                val = 0
            pipe_flags[col] = 1 if int(val) > 0 else 0

        auto_label_from_pipeline = _annotation_flags_to_auto_label(pipe_flags)
        record["pipeline"] = {
            "found": True,
            "application_context": str(pipeline_row.get("application_context", "Unknown")),
            "entity_flags": pipe_flags,
            "auto_label_from_flags": auto_label_from_pipeline,
            "ocr_text_length": int(pipeline_row.get("ocr_text_length", 0)) if not pd.isna(pipeline_row.get("ocr_text_length", 0)) else None,
        }

    # --- Comparison ---
    if annotation_row is not None and pipeline_row is not None:
        ann_flags = record["annotation"]["entity_flags"]
        pipe_flags = record["pipeline"]["entity_flags"]

        flag_agreement = ann_flags == pipe_flags
        flags_differ = {
            col: {"annotation": ann_flags[col], "pipeline": pipe_flags[col]}
            for col in _ENTITY_COLS
            if ann_flags[col] != pipe_flags[col]
        }
        ann_label = record["annotation"]["label"]
        label_consistent = _label_consistent_with_flags(ann_label, ann_flags)

        record["comparison"] = {
            "flag_agreement": flag_agreement,
            "flags_differ": flags_differ,
            "label_consistent_with_annotation_flags": label_consistent,
            "recommendation": (
                "Human visual review required"
                if status == "NEEDS_VISUAL_REVIEW"
                else "Policy decision required"
            ),
        }
    elif annotation_row is not None:
        ann_flags = record["annotation"]["entity_flags"]
        ann_label = record["annotation"]["label"]
        label_consistent = _label_consistent_with_flags(ann_label, ann_flags)
        record["comparison"] = {
            "flag_agreement": None,
            "flags_differ": None,
            "label_consistent_with_annotation_flags": label_consistent,
            "recommendation": "Pipeline row missing — cannot compare flags",
        }
    else:
        record["comparison"] = {
            "flag_agreement": None,
            "flags_differ": None,
            "label_consistent_with_annotation_flags": None,
            "recommendation": "Annotation row missing — screenshot not in CSV",
        }

    return record


# ---------------------------------------------------------------------------
# Main audit function
# ---------------------------------------------------------------------------

def run_annotation_audit() -> dict[str, Any]:
    """Load data and audit all 16 flagged rows."""
    print("=" * 72)
    print("CAPRLM Annotation Audit Tool (read-only)")
    print(f"  Annotation CSV:  {DATASET_PATH}")
    print(f"  Candidates CSV:  {CANDIDATES_CSV}")
    print("=" * 72)

    # --- Load annotation CSV ---
    if not DATASET_PATH.exists():
        print(f"ERROR: Annotation CSV not found: {DATASET_PATH}")
        return {"error": "Annotation CSV not found"}
    df_ann = pd.read_csv(DATASET_PATH)
    df_ann["screenshot_id"] = df_ann["screenshot_id"].astype(str).str.strip()
    print(f"\nAnnotation CSV loaded: {len(df_ann)} rows")

    # --- Load candidates CSV ---
    candidates_loaded = False
    df_pipe: pd.DataFrame | None = None
    if CANDIDATES_CSV.exists():
        try:
            df_pipe = pd.read_csv(CANDIDATES_CSV)
            df_pipe["screenshot_id"] = df_pipe["screenshot_id"].astype(str).str.strip()
            # Deduplicate: keep latest processed_at per screenshot_id
            if "processed_at" in df_pipe.columns:
                df_pipe = df_pipe.sort_values("processed_at", ascending=True)
                df_pipe = df_pipe.drop_duplicates(subset=["screenshot_id"], keep="last")
            candidates_loaded = True
            print(f"Candidates CSV loaded: {len(df_pipe)} rows (after dedup)")
        except Exception as exc:
            print(f"WARNING: Could not load candidates CSV: {exc}")
    else:
        print(f"WARNING: Candidates CSV not found at {CANDIDATES_CSV}")

    # --- Audit each flagged row ---
    print(f"\nAuditing {len(FLAGGED_ROWS)} flagged rows …\n")
    audit_records: list[dict[str, Any]] = []
    summary_by_group: dict[str, dict[str, int]] = {}

    for flagged in FLAGGED_ROWS:
        sid = flagged["screenshot_id"]
        group = flagged["group"]
        issue = flagged["issue"]
        status = flagged["status"]

        # Look up annotation row
        ann_match = df_ann[df_ann["screenshot_id"] == sid]
        ann_row: pd.Series | None = ann_match.iloc[0] if len(ann_match) == 1 else None

        # Look up pipeline row
        pipe_row: pd.Series | None = None
        if df_pipe is not None:
            pipe_match = df_pipe[df_pipe["screenshot_id"] == sid]
            pipe_row = pipe_match.iloc[0] if len(pipe_match) >= 1 else None

        record = _audit_row(sid, group, issue, status, ann_row, pipe_row)
        audit_records.append(record)

        # Print summary
        ann_label = record["annotation"].get("label", "NOT FOUND")
        pipe_auto = record["pipeline"].get("auto_label_from_flags", "NOT FOUND") if record["pipeline"]["found"] else "N/A"
        flag_agree = record.get("comparison", {}).get("flag_agreement")
        consistent = record.get("comparison", {}).get("label_consistent_with_annotation_flags")
        file_exists = record["screenshot_file_exists"]
        print(f"  {group:4s} {sid[:24]}…  ann={ann_label:<8}  pipe_auto={pipe_auto:<10}  "
              f"flags_agree={str(flag_agree):<6}  label_ok={str(consistent):<12}  "
              f"file={str(file_exists):<6}")

        # Group summary
        if group not in summary_by_group:
            summary_by_group[group] = {"total": 0, "screenshot_exists": 0, "flags_agree": 0}
        summary_by_group[group]["total"] += 1
        if file_exists:
            summary_by_group[group]["screenshot_exists"] += 1
        if flag_agree is True:
            summary_by_group[group]["flags_agree"] += 1

    # --- Overall summary ---
    total = len(audit_records)
    files_exist = sum(1 for r in audit_records if r["screenshot_file_exists"])
    pipeline_found = sum(1 for r in audit_records if r["pipeline"]["found"])
    flags_agree = sum(1 for r in audit_records
                      if r.get("comparison", {}).get("flag_agreement") is True)
    labels_ok_yes = sum(1 for r in audit_records
                        if r.get("comparison", {}).get("label_consistent_with_annotation_flags") == "YES")
    labels_ok_no = sum(1 for r in audit_records
                       if r.get("comparison", {}).get("label_consistent_with_annotation_flags") == "NO")
    labels_conditional = sum(1 for r in audit_records
                              if r.get("comparison", {}).get("label_consistent_with_annotation_flags") == "CONDITIONAL")

    print(f"\nSummary:")
    print(f"  Total flagged rows:        {total}")
    print(f"  Screenshot files found:    {files_exist}/{total}")
    print(f"  Pipeline rows found:       {pipeline_found}/{total}")
    print(f"  Entity flags agree:        {flags_agree}/{total}")
    print(f"  Label consistent (YES):    {labels_ok_yes}/{total}")
    print(f"  Label consistent (NO):     {labels_ok_no}/{total}")
    print(f"  Label conditional:         {labels_conditional}/{total}")

    result: dict[str, Any] = {
        "audited_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "annotation_csv": str(DATASET_PATH),
        "candidates_csv": str(CANDIDATES_CSV),
        "candidates_csv_found": candidates_loaded,
        "screenshots_dir": str(SCREENSHOTS_DIR),
        "screenshots_dir_exists": SCREENSHOTS_DIR.exists(),
        "total_flagged": total,
        "summary": {
            "screenshot_files_found": files_exist,
            "pipeline_rows_found": pipeline_found,
            "entity_flags_agree": flags_agree,
            "label_consistent_yes": labels_ok_yes,
            "label_consistent_no": labels_ok_no,
            "label_conditional": labels_conditional,
        },
        "group_summary": summary_by_group,
        "audit_records": audit_records,
        "notes": [
            "All 16 rows require human visual review of the screenshot file.",
            "This tool only compares structured metadata; it cannot assess visual content.",
            "Label consistency is checked against annotation entity flags only (§3 of guidelines).",
            "CONDITIONAL = all-zero flags with non-LOW label, valid per §6 only if annotation_notes column exists.",
            "NEEDS_POLICY_DECISION rows (Group G6) depend on Policy 2 resolution by supervisor.",
        ],
    }
    return result


if __name__ == "__main__":
    result = run_annotation_audit()
    AUDIT_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_RESULTS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nAudit results written -> {AUDIT_RESULTS_PATH}")
