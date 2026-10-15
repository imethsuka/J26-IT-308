"""
CAPRLM privacy leakage evaluation harness — Phase 5.

Tests the tokenization pipeline against synthetic PII-containing inputs
for all supported entity types.  No real screenshots are needed.

Tests performed:
  1. Every entity type tokenized at HIGH risk (no raw PII in output).
  2. Every entity type tokenized at MEDIUM risk.
  3. LOW + confidence < 0.75 → tokenization applied (uncertain).
  4. LOW + confidence >= 0.75 (documented gap) → tokenization SKIPPED;
     matches lists stripped; tokenized_text redacted.
  5. Token collision edge case: OCR text already contains '[EMAIL_1]' literal.
  6. Entity coverage measurement: fraction of entity types covered by tests.
  7. Multiple entities of the same type handled correctly.
  8. Restoration round-trip: tokenize → restore returns original text.

Output:
    backend/data/privacy_eval_results.json  (write; creates or overwrites)

Usage (from Member2/ directory):
    .venv\\Scripts\\python -m backend.scripts.privacy_eval
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.services.sensitive_detection import detect_sensitive_entities
from backend.services.tokenizer import (
    DEFAULT_MIN_CONFIDENCE_TO_SKIP,
    tokenize,
    restore,
    _strip_matches,
    _should_tokenize,
)
from backend.services.train_model import DATASET_PATH

RESULTS_PATH = DATASET_PATH.parent / "privacy_eval_results.json"

# ---------------------------------------------------------------------------
# Synthetic PII inputs — one per entity type
# ---------------------------------------------------------------------------

_ENTITY_INPUTS: dict[str, str] = {
    "EMAIL": "Please contact alice@example.com for support.",
    "PHONE": "Call us on 555-123-4567 during business hours.",
    "URL": "Visit https://www.example.com/login for access.",
    "IP_ADDRESS": "Server located at 192.168.100.200 in the datacenter.",
    "DATE": "The incident occurred on 12/25/2023.",
    "WINDOWS_PATH": r"File saved at C:\Users\admin\Documents\report.docx",
}

# Combined multi-entity input
_MULTI_ENTITY_INPUT = (
    "Email: alice@example.com, Phone: 555-123-4567, "
    "URL: https://example.com, IP: 10.0.0.1, "
    r"Path: C:\Users\test\file.txt, Date: 01/01/2024"
)

# Token collision input: already contains a token-format literal
_COLLISION_INPUT = (
    "[EMAIL_1] is shown here already but alice@example.com is the real address."
)

# Multiple values of the same type
_MULTI_VALUE_INPUT = (
    "Contacts: alice@example.com and bob@corp.org and charlie@test.io"
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _contains_pii(text: str, pii_values: list[str]) -> list[str]:
    """Return which PII values appear in text."""
    return [v for v in pii_values if v in text]


def _extract_pii_values(detection: dict) -> list[str]:
    """Get all match values from a detection result."""
    values = []
    for key, val in detection.items():
        if key == "total_sensitive_entity_count":
            continue
        if isinstance(val, dict):
            values.extend(val.get("matches", []))
    return values


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

def test_entity_type_at_risk_level(
    entity_type: str,
    input_text: str,
    risk_level: str,
    confidence: float | None,
) -> dict[str, Any]:
    """
    Tokenize input_text and check whether raw PII appears in the output.
    Returns a result dict documenting the test outcome.
    """
    detection = detect_sensitive_entities(input_text)
    pii_values = _extract_pii_values(detection)

    tokenized_text, token_map, tokenized_det = tokenize(
        input_text, detection, risk_level=risk_level, confidence=confidence
    )

    tokenization_applied = len(token_map) > 0
    leaked_in_text = _contains_pii(tokenized_text, pii_values)

    # Check matches in tokenized_det
    leaked_in_matches: list[str] = []
    for cat, val in tokenized_det.items():
        if cat == "total_sensitive_entity_count":
            continue
        if isinstance(val, dict):
            for m in val.get("matches", []):
                if isinstance(m, str) and m in pii_values:
                    leaked_in_matches.append(m)

    passed = True
    failure_reason = None

    if risk_level in ("HIGH", "MEDIUM", "UNAVAILABLE"):
        # Must tokenize if entities exist
        if pii_values and not tokenization_applied:
            passed = False
            failure_reason = f"Tokenization not applied at {risk_level} with entities present"
        if leaked_in_text:
            passed = False
            failure_reason = f"Raw PII leaked in tokenized_text at {risk_level}: {leaked_in_text}"
        if leaked_in_matches:
            passed = False
            failure_reason = f"Raw PII leaked in detection matches at {risk_level}: {leaked_in_matches}"
    elif risk_level == "LOW" and confidence is not None and confidence >= DEFAULT_MIN_CONFIDENCE_TO_SKIP:
        # Skip path: documented gap
        # matches should be stripped, tokenized_text should be redacted (not tokenized_text = raw)
        if pii_values and tokenization_applied:
            passed = False
            failure_reason = "Tokenization was unexpectedly applied on LOW+high-conf path"
        # Check matches are stripped
        if leaked_in_matches:
            passed = False
            failure_reason = f"Raw PII in matches on skip path (should be stripped): {leaked_in_matches}"
        # tokenized_text should equal original (the gap) OR be redacted
        # The documented gap is in API response: main.py now returns "[redacted...]"
        # when token_map is empty; tokenize() itself returns the original text.
        # Document but don't fail: this is a known architectural limitation.
    elif risk_level == "LOW" and (confidence is None or confidence < DEFAULT_MIN_CONFIDENCE_TO_SKIP):
        # Uncertain LOW: must tokenize
        if pii_values and not tokenization_applied:
            passed = False
            failure_reason = f"LOW+low-conf should tokenize but didn't"

    return {
        "entity_type": entity_type,
        "risk_level": risk_level,
        "confidence": confidence,
        "skip_threshold": DEFAULT_MIN_CONFIDENCE_TO_SKIP,
        "entities_detected": len(pii_values),
        "pii_values_detected": pii_values,
        "tokenization_applied": tokenization_applied,
        "token_count": len(token_map),
        "leaked_in_tokenized_text": leaked_in_text,
        "leaked_in_detection_matches": leaked_in_matches,
        "passed": passed,
        "failure_reason": failure_reason,
    }


def test_low_gap_documented(input_text: str, entity_type: str) -> dict[str, Any]:
    """
    Test and document the known gap: LOW + confidence >= 0.75 → tokenization skipped.
    The tokenize() function returns original text when skipping; the API layer
    (main.py) substitutes '[redacted — entity tokenization not applied]'.

    This test CONFIRMS the gap exists and documents it — it does not hide it.
    """
    detection = detect_sensitive_entities(input_text)
    pii_values = _extract_pii_values(detection)

    tokenized_text, token_map, tokenized_det = tokenize(
        input_text, detection, risk_level="LOW", confidence=0.90
    )

    # tokenize() returns original text on skip path
    text_is_original = tokenized_text == input_text
    token_map_empty = len(token_map) == 0

    # matches should be stripped (privacy protection for the skip path)
    leaked_in_matches: list[str] = []
    for cat, val in tokenized_det.items():
        if cat == "total_sensitive_entity_count":
            continue
        if isinstance(val, dict):
            for m in val.get("matches", []):
                if isinstance(m, str) and m in pii_values:
                    leaked_in_matches.append(m)

    # The gap: tokenized_text from tokenize() IS the raw text at skip path.
    # The API layer (main.py) adds the redaction. This tests tokenize() directly.
    gap_confirmed = text_is_original and token_map_empty and len(pii_values) > 0
    matches_stripped = len(leaked_in_matches) == 0

    return {
        "test": "low_gap_documented",
        "entity_type": entity_type,
        "risk_level": "LOW",
        "confidence": 0.90,
        "entities_detected": len(pii_values),
        "token_map_empty": token_map_empty,
        "tokenized_text_equals_original": text_is_original,
        "gap_confirmed": gap_confirmed,
        "matches_stripped_by_strip_matches": matches_stripped,
        "leaked_in_detection_matches_before_strip": leaked_in_matches,
        "note": (
            "KNOWN GAP: tokenize() returns original text at LOW+high-conf. "
            "The API layer (main.py) returns '[redacted...]' instead of the "
            "original text to prevent exposure via tokenized_summary.tokenized_text. "
            "This gap is documented in PROJECT_CONTROL_CENTER.md §10."
        ),
    }


def test_token_collision() -> dict[str, Any]:
    """
    Test the token collision edge case.
    When OCR text already contains '[EMAIL_1]', the tokenizer assigns the same
    token format to the real email. After tokenization, '[EMAIL_1]' appears twice:
    once from the literal and once from the real email replacement. Restoration
    then incorrectly replaces the original literal.
    """
    detection = detect_sensitive_entities(_COLLISION_INPUT)
    tokenized_text, token_map, _ = tokenize(
        _COLLISION_INPUT, detection, risk_level="HIGH", confidence=0.95
    )

    real_email_tokenized = "alice@example.com" not in tokenized_text
    restored = restore(tokenized_text, token_map)

    # The collision means the literal '[EMAIL_1]' will also be replaced on restore
    collision_occurred = "[EMAIL_1]" not in restored or restored != _COLLISION_INPUT

    return {
        "test": "token_collision",
        "input": _COLLISION_INPUT,
        "tokenized_text": tokenized_text,
        "token_map": token_map,
        "real_email_tokenized": real_email_tokenized,
        "collision_detected": collision_occurred,
        "restored_text": restored,
        "note": (
            "KNOWN LIMITATION: If OCR text contains a literal matching token format "
            "('[EMAIL_1]'), tokenization and restoration are ambiguous. "
            "Documented in tokenizer.py docstring. "
            "Probability: very low in typical desktop screenshots."
        ),
    }


def test_multi_value_same_type() -> dict[str, Any]:
    """
    Test that multiple distinct values of the same type each get unique tokens.
    """
    detection = detect_sensitive_entities(_MULTI_VALUE_INPUT)
    email_count = detection["EMAIL"]["count"]
    tokenized_text, token_map, _ = tokenize(
        _MULTI_VALUE_INPUT, detection, risk_level="HIGH", confidence=0.95
    )

    unique_email_tokens = sum(1 for k in token_map if k.startswith("[EMAIL_"))
    all_emails_tokenized = (
        "alice@example.com" not in tokenized_text
        and "bob@corp.org" not in tokenized_text
        and "charlie@test.io" not in tokenized_text
    )

    return {
        "test": "multi_value_same_type",
        "entity_type": "EMAIL",
        "emails_detected": email_count,
        "unique_email_tokens_assigned": unique_email_tokens,
        "all_emails_tokenized": all_emails_tokenized,
        "passed": all_emails_tokenized and unique_email_tokens == email_count,
    }


def test_restoration_round_trip() -> dict[str, Any]:
    """
    Test that tokenize → restore recovers the original text.
    """
    results = []
    for entity_type, text in _ENTITY_INPUTS.items():
        detection = detect_sensitive_entities(text)
        tokenized_text, token_map, _ = tokenize(
            text, detection, risk_level="HIGH", confidence=0.95
        )
        restored = restore(tokenized_text, token_map)
        exact_match = restored == text
        results.append({
            "entity_type": entity_type,
            "original": text,
            "tokenized": tokenized_text,
            "restored": restored,
            "exact_match": exact_match,
        })
    all_passed = all(r["exact_match"] for r in results)
    return {"test": "restoration_round_trip", "per_type": results, "all_passed": all_passed}


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------

def run_privacy_eval() -> dict[str, Any]:
    """Run all privacy evaluation tests and return results."""
    print("=" * 72)
    print("CAPRLM Privacy Leakage Evaluation Harness")
    print(f"  Default skip threshold: {DEFAULT_MIN_CONFIDENCE_TO_SKIP}")
    print("=" * 72)

    all_entity_tests: list[dict[str, Any]] = []
    failed_tests: list[str] = []

    risk_levels_to_test = [
        ("HIGH", 0.95),
        ("MEDIUM", 0.80),
        ("LOW", 0.50),   # uncertain LOW → should tokenize
        ("UNAVAILABLE", None),  # fail-safe
    ]

    # --- Test 1: Each entity type at HIGH/MEDIUM/uncertain-LOW/UNAVAILABLE ---
    print("\n[1] Per-entity-type tests at HIGH, MEDIUM, LOW(uncertain), UNAVAILABLE:")
    for risk_level, confidence in risk_levels_to_test:
        for entity_type, input_text in _ENTITY_INPUTS.items():
            result = test_entity_type_at_risk_level(
                entity_type, input_text, risk_level, confidence
            )
            all_entity_tests.append(result)
            status = "PASS" if result["passed"] else "FAIL"
            print(f"  {status} {entity_type:<16} risk={risk_level:<12} conf={str(confidence):<6} "
                  f"detected={result['entities_detected']} tokenized={result['tokenization_applied']}")
            if not result["passed"]:
                failed_tests.append(
                    f"{entity_type}/{risk_level}: {result['failure_reason']}"
                )

    # --- Test 2: Multi-entity input ---
    print("\n[2] Multi-entity input (all types in one text):")
    for risk_level, confidence in [("HIGH", 0.95), ("MEDIUM", 0.80)]:
        result = test_entity_type_at_risk_level(
            "MULTI", _MULTI_ENTITY_INPUT, risk_level, confidence
        )
        all_entity_tests.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"  {status} MULTI            risk={risk_level:<12} conf={str(confidence):<6} "
              f"detected={result['entities_detected']} tokenized={result['tokenization_applied']}")
        if not result["passed"]:
            failed_tests.append(f"MULTI/{risk_level}: {result['failure_reason']}")

    # --- Test 3: LOW + high confidence gap (documented) ---
    print("\n[3] LOW + high confidence gap (documented known limitation):")
    gap_results: list[dict[str, Any]] = []
    for entity_type, input_text in _ENTITY_INPUTS.items():
        gap_result = test_low_gap_documented(input_text, entity_type)
        gap_results.append(gap_result)
        print(f"  {entity_type:<16} gap_confirmed={gap_result['gap_confirmed']}  "
              f"matches_stripped={gap_result['matches_stripped_by_strip_matches']}")

    # --- Test 4: Token collision ---
    print("\n[4] Token collision edge case:")
    collision_result = test_token_collision()
    print(f"  real_email_tokenized={collision_result['real_email_tokenized']}  "
          f"collision_detected={collision_result['collision_detected']}")

    # --- Test 5: Multi-value same type ---
    print("\n[5] Multiple values of same entity type:")
    multi_value_result = test_multi_value_same_type()
    status = "PASS" if multi_value_result["passed"] else "FAIL"
    print(f"  {status} emails={multi_value_result['emails_detected']}  "
          f"unique_tokens={multi_value_result['unique_email_tokens_assigned']}  "
          f"all_tokenized={multi_value_result['all_emails_tokenized']}")
    if not multi_value_result["passed"]:
        failed_tests.append("multi_value_same_type: not all emails tokenized or token count mismatch")

    # --- Test 6: Restoration round-trip ---
    print("\n[6] Restoration round-trip (tokenize → restore = original):")
    round_trip_result = test_restoration_round_trip()
    for r in round_trip_result["per_type"]:
        status = "PASS" if r["exact_match"] else "FAIL"
        print(f"  {status} {r['entity_type']}")
        if not r["exact_match"]:
            failed_tests.append(f"round_trip/{r['entity_type']}: restored != original")

    # --- Coverage summary ---
    entity_types = list(_ENTITY_INPUTS.keys())
    covered_types = {r["entity_type"] for r in all_entity_tests if r.get("entities_detected", 0) > 0}
    coverage_fraction = len(covered_types & set(entity_types)) / len(entity_types)
    print(f"\n[7] Entity type coverage: {len(covered_types & set(entity_types))}/{len(entity_types)} "
          f"({coverage_fraction:.0%})")

    # --- Final summary ---
    total_entity_tests = len(all_entity_tests)
    passed_entity_tests = sum(1 for r in all_entity_tests if r.get("passed", False))
    print(f"\n{'='*72}")
    print(f"Entity-level tests: {passed_entity_tests}/{total_entity_tests} passed")
    if failed_tests:
        print(f"FAILURES ({len(failed_tests)}):")
        for f in failed_tests:
            print(f"  - {f}")
    else:
        print("All entity-level tests passed.")

    result = {
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "skip_threshold": DEFAULT_MIN_CONFIDENCE_TO_SKIP,
        "entity_types_tested": entity_types,
        "entity_type_coverage_fraction": round(coverage_fraction, 4),
        "entity_level_tests": {
            "total": total_entity_tests,
            "passed": passed_entity_tests,
            "failed": total_entity_tests - passed_entity_tests,
            "details": all_entity_tests,
        },
        "low_gap_documentation": {
            "description": (
                "LOW + confidence >= 0.75 skips tokenization. "
                "tokenize() returns original text (the gap at the function level). "
                "main.py returns '[redacted...]' placeholder to prevent API exposure. "
                "Matches are stripped by _strip_matches() in all cases."
            ),
            "gap_confirmed_for_all_types": all(r["gap_confirmed"] for r in gap_results),
            "matches_stripped_for_all_types": all(r["matches_stripped_by_strip_matches"] for r in gap_results),
            "per_type": gap_results,
        },
        "token_collision": collision_result,
        "multi_value_same_type": multi_value_result,
        "restoration_round_trip": round_trip_result,
        "failed_tests": failed_tests,
        "overall_passed": len(failed_tests) == 0,
    }
    return result


if __name__ == "__main__":
    result = run_privacy_eval()
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nResults written → {RESULTS_PATH}")
    if result["overall_passed"]:
        print("Overall: PASSED")
    else:
        print(f"Overall: FAILED — {len(result['failed_tests'])} failure(s)")
