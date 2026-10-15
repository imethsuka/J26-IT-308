"""
Detection evaluation: false-positive analysis for LOW-labeled screenshots
with all-zero annotation entity flags.

Uses already-extracted candidates.csv (no OCR re-run required).

Output: data/detection_eval_results.json
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parent.parent.parent.parent  # J26-IT-308
_DATA = Path(__file__).parent.parent / "data"
_ANNOTATION_CSV = _DATA / "CAPRLM_annotation_50.csv"
_CANDIDATES_CSV = _DATA / "processing_output" / "candidates.csv"
_OUTPUT_JSON = _DATA / "detection_eval_results.json"

# ---------------------------------------------------------------------------
# Entity type descriptions for false-positive heuristics
# ---------------------------------------------------------------------------

_ENTITY_DESCRIPTIONS = {
    "DATE": (
        "Date patterns (YYYY-MM-DD, DD/MM/YYYY, etc.). "
        "Screenshots may display timestamps, version numbers (e.g. 2024.1.0), "
        "or numeric literals that match date regex."
    ),
    "EMAIL": (
        "Email patterns (user@domain). "
        "May match code examples, docstrings, or placeholder emails."
    ),
    "PHONE": (
        "Phone patterns (10-digit sequences, +1-xxx etc.). "
        "May match student IDs, product serials, numeric sequences in code."
    ),
    "URL": (
        "URL patterns (http://, https://, www.). "
        "Common in browser screenshots; may be benign documentation links."
    ),
    "IP_ADDRESS": (
        "IPv4 patterns. "
        "May match version numbers (1.2.3.4), CIDR notation, localhost refs."
    ),
    "WINDOWS_PATH": (
        "Windows path patterns (C:\\, D:\\, etc.). "
        "Present in terminal/IDE screenshots with file paths."
    ),
}

# Heuristic: whether a detection is likely a real sensitive entity or likely
# a false positive depends on the application context.
_LIKELY_BENIGN_CONTEXTS = {
    "DATE":       {"ChatGPT", "VS Code", "Unknown", "Google Chrome"},  # ubiquitous
    "URL":        {"Google Chrome", "YouTube", "Unknown"},              # browser content
    "IP_ADDRESS": {"VS Code", "Unknown"},                               # dev tools
}


def _load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _entity_flags(row: dict, source: str = "annotation") -> dict[str, int]:
    """Return {entity_type: 0|1} flags for a CSV row."""
    if source == "annotation":
        return {
            k: int(row.get(k, 0))
            for k in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH")
        }
    # candidates.csv column names are the same
    return {
        k: int(float(row.get(k, 0)))
        for k in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH")
    }


def _fp_likely(entity: str, app_context: str) -> str:
    """Return 'likely_fp', 'uncertain', or 'likely_tp' for a pipeline detection."""
    ctx = str(app_context).strip()
    benign_ctxs = _LIKELY_BENIGN_CONTEXTS.get(entity, set())
    if entity == "DATE":
        # Dates are almost always non-sensitive in desktop screenshots
        return "likely_fp"
    if entity == "URL" and any(x in ctx for x in ("Chrome", "YouTube", "Browser")):
        return "uncertain"  # Could be tracking URL — needs visual review
    if entity in ("EMAIL", "PHONE", "WINDOWS_PATH", "IP_ADDRESS"):
        return "uncertain"  # These are more likely to be genuinely sensitive
    return "uncertain"


def main():
    print(f"Loading annotation CSV: {_ANNOTATION_CSV}")
    annotations = _load_csv(_ANNOTATION_CSV)
    print(f"  {len(annotations)} rows")

    print(f"Loading candidates CSV: {_CANDIDATES_CSV}")
    candidates = _load_csv(_CANDIDATES_CSV)
    print(f"  {len(candidates)} rows")

    # Build candidates lookup: screenshot_id → row
    cand_by_id = {r["screenshot_id"]: r for r in candidates}

    # Find LOW-labeled rows with all-zero annotation entity flags
    low_zero = [
        r for r in annotations
        if r["privacy_risk_label"] == "LOW"
        and all(int(r.get(k, 0)) == 0
                for k in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"))
    ]
    print(f"\nLOW-labeled rows with all-zero annotation flags: {len(low_zero)}")

    # For each, check what pipeline detected
    records = []
    fp_summary = {
        "total_evaluated":         len(low_zero),
        "zero_pipeline_detections": 0,
        "any_pipeline_detection":   0,
        "by_entity_type":           {k: 0 for k in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH")},
    }

    for ann in low_zero:
        sid = ann["screenshot_id"]
        app_ctx = ann.get("application_context", "Unknown")
        cand = cand_by_id.get(sid)

        ann_flags = _entity_flags(ann, "annotation")
        pip_flags = _entity_flags(cand, "candidates") if cand else {}
        pip_counts = {}
        if cand:
            for k in ("email_count", "phone_count", "url_count", "ip_count",
                      "date_count", "windows_path_count"):
                entity_key = k.replace("_count", "").upper().replace("IP", "IP_ADDRESS").replace("URL", "URL").replace("WINDOWS_PATH", "WINDOWS_PATH")
                pip_counts[k] = int(float(cand.get(k, 0) or 0))

        # Map count keys to entity type keys
        count_key_map = {
            "email_count":        "EMAIL",
            "phone_count":        "PHONE",
            "url_count":          "URL",
            "ip_count":           "IP_ADDRESS",
            "date_count":         "DATE",
            "windows_path_count": "WINDOWS_PATH",
        }

        false_positive_detections = []
        any_detection = False
        for ck, etype in count_key_map.items():
            cnt = int(float(cand.get(ck, 0) or 0)) if cand else 0
            if cnt > 0:
                any_detection = True
                fp_likely_class = _fp_likely(etype, app_ctx)
                false_positive_detections.append({
                    "entity_type":   etype,
                    "pipeline_count": cnt,
                    "annotation_flag": ann_flags.get(etype, 0),
                    "fp_assessment": fp_likely_class,
                    "description":   _ENTITY_DESCRIPTIONS[etype],
                })
                fp_summary["by_entity_type"][etype] += 1

        if any_detection:
            fp_summary["any_pipeline_detection"] += 1
        else:
            fp_summary["zero_pipeline_detections"] += 1

        records.append({
            "screenshot_id":          sid,
            "annotation_label":       ann["privacy_risk_label"],
            "application_context":    app_ctx,
            "pipeline_found":         cand is not None,
            "ocr_text_length":        int(float(cand.get("ocr_text_length", 0) or 0)) if cand else None,
            "annotation_flags":       ann_flags,
            "pipeline_flags":         pip_flags,
            "any_pipeline_detection": any_detection,
            "detections":             false_positive_detections,
            "assessment": (
                "POTENTIAL_FALSE_POSITIVES" if any_detection else "CONSISTENT_WITH_ANNOTATION"
            ),
        })

    # Sort: detections first, then consistent
    records.sort(key=lambda r: (0 if r["any_pipeline_detection"] else 1, r["screenshot_id"]))

    # Compute overall false positive rate for each entity
    fp_rate = {}
    for etype, count in fp_summary["by_entity_type"].items():
        fp_rate[etype] = {
            "screenshots_with_fp_detection": count,
            "total_evaluated":               len(low_zero),
            "rate":                          round(count / len(low_zero), 4) if low_zero else 0,
        }

    # Build summary
    date_fps = sum(
        1 for r in records
        if any(d["entity_type"] == "DATE" for d in r["detections"])
    )
    non_date_fps = sum(
        1 for r in records
        if any(d["entity_type"] != "DATE" for d in r["detections"])
    )

    result = {
        "evaluated_at":           time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method":                 "candidates_csv_entity_flags_only_no_ocr_rerun",
        "annotation_csv":         str(_ANNOTATION_CSV),
        "candidates_csv":         str(_CANDIDATES_CSV),
        "evaluation_scope":       "LOW_labeled_all_zero_annotation_flags",
        "summary": {
            **fp_summary,
            "date_only_fp_count":     date_fps,
            "non_date_fp_count":      non_date_fps,
            "false_positive_rates":   fp_rate,
        },
        "findings": [
            {
                "finding": "DATE detections dominate false positives",
                "detail":  (
                    f"{fp_summary['by_entity_type']['DATE']} of {len(low_zero)} "
                    "LOW-labeled all-zero-annotation screenshots have pipeline DATE detections. "
                    "These are overwhelmingly likely false positives: desktop screenshots routinely "
                    "contain timestamps, file modification dates, version numbers, and message times "
                    "that match date regex patterns but are not annotator-defined sensitive entities."
                ),
                "recommendation": (
                    "DATE-only detections on screenshots annotated as LOW with no DATE flag "
                    "should be reviewed against the annotation guidelines. "
                    "If DATE entities are routinely benign in certain contexts (VS Code, ChatGPT), "
                    "a context-aware suppression rule could reduce false positive rate."
                ),
            },
            {
                "finding": "Non-DATE entity detections require visual review",
                "detail":  (
                    f"{non_date_fps} screenshots have non-DATE pipeline detections "
                    "against a zero-annotation-flag LOW label. "
                    "These include PHONE, URL, IP_ADDRESS patterns that may be "
                    "student IDs, documentation links, or CIDR notation."
                ),
                "recommendation": (
                    "Each case should be visually inspected by the annotator "
                    "to determine whether the annotation is incorrect or the detection is a false positive."
                ),
            },
        ],
        "records": records,
    }

    with open(_OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print("DETECTION EVALUATION SUMMARY")
    print(f"{'='*60}")
    print(f"LOW-labeled all-zero-annotation screenshots evaluated: {len(low_zero)}")
    print(f"With any pipeline detection:     {fp_summary['any_pipeline_detection']}")
    print(f"With zero pipeline detections:   {fp_summary['zero_pipeline_detections']}")
    print(f"\nPipeline detections by entity type (on annotated-clean LOW rows):")
    for etype, cnt in sorted(fp_summary["by_entity_type"].items(), key=lambda x: -x[1]):
        pct = 100 * cnt / len(low_zero) if low_zero else 0
        print(f"  {etype:14s}: {cnt:3d} screenshots ({pct:.1f}%)")
    print(f"\nOutput: {_OUTPUT_JSON}")


if __name__ == "__main__":
    main()
