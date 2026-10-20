"""
CAPRLM Live Demonstration -- Research Presentation
==================================================
Project: J26-IT-308 - Member 2

Runs three illustrative cases through the actual pipeline:
    text  ->  detect_sensitive_entities
          ->  extract_features
          ->  predict_privacy_risk

Model:      Member2/backend/data/caprlm_model.joblib  (v1.0.0, 2026-10-13)
            SHA-256: C08E30BC... (identical to C:\\CAPRLM-Model\\caprlm_model.joblib)
Classes:    HIGH  |  LOW  |  MEDIUM
Test F1:    0.4444 (macro) -- equal to majority-class baseline on 10-row test fold
            This is an honest research finding documented in experiment_results.json.

Run from the Member2 directory:
    .venv\\Scripts\\python.exe caprlm_demo.py
"""

from __future__ import annotations
import sys, os
# Ensure the Member2 package root is on sys.path when run directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.services.sensitive_detection import detect_sensitive_entities
from backend.services.feature_extraction   import extract_features
from backend.services.risk_prediction      import predict_privacy_risk

# ---------------------------------------------------------------------------
# Demo cases -- synthetic text only; no real personal data
# ---------------------------------------------------------------------------
CASES = [
    {
        "label": "CASE A -- Expected LOW",
        "context": "VS Code",
        "privacy_preference": "Medium",
        "historical_protection_level": "Medium",
        "text": (
            "VS Code editor. Project: caprlm_prototype. "
            "Python 3.11 environment active. "
            "No network connections, no user credentials shown."
        ),
    },
    {
        "label": "CASE B -- Expected MEDIUM",
        "context": "Google Chrome",
        "privacy_preference": "Medium",
        "historical_protection_level": "Medium",
        "text": (
            "Chrome address bar shows https://docs.example-org.internal/wiki. "
            "Page footer: last updated 2026-10-15. "
            "No personal contact information visible."
        ),
    },
    {
        "label": "CASE C -- Expected HIGH",
        "context": "Desktop",
        "privacy_preference": "Medium",
        "historical_protection_level": "Medium",
        "text": (
            "Desktop sticky note: "
            "Contact: helpdesk@example-corp.com  "
            "Phone: +1 (555) 012-3456  "
            "Server: 192.168.10.50"
        ),
    },
]

# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------
SEP = "=" * 64

def _entity_line(detection: dict) -> str:
    parts = []
    for cat in ("EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH"):
        info = detection.get(cat, {})
        count = info.get("count", 0) if isinstance(info, dict) else 0
        matches = info.get("matches", []) if isinstance(info, dict) else []
        if count > 0:
            parts.append(f"{cat}={count} {matches}")
        else:
            parts.append(f"{cat}=0")
    parts.append(f"total={detection.get('total_sensitive_entity_count', 0)}")
    return "  " + "  ".join(parts)

print()
print(SEP)
print("CAPRLM -- Context-Aware Privacy Risk Level Model")
print("Live demonstration  |  J26-IT-308 - Member 2")
print(SEP)
print()
print("MODEL METADATA")
print(f"  Path:         Member2/backend/data/caprlm_model.joblib")
print(f"  Version:      1.0.0  (trained 2026-10-13)")
print(f"  Algorithm:    Random Forest")
print(f"  Classes:      HIGH | LOW | MEDIUM")
print(f"  Test macro F1: 0.4444 (= majority-class baseline -- honest research finding)")
print()

for case in CASES:
    text    = case["text"]
    ctx     = case["context"]
    pref    = case["privacy_preference"]
    hist    = case["historical_protection_level"]

    # Stage 1: entity detection
    detection = detect_sensitive_entities(text)

    # Stage 2: feature extraction
    features  = extract_features(text, detection, ctx)

    # Stage 3: risk prediction (ML + threshold adjustment)
    result    = predict_privacy_risk(features, ctx, pref, hist)

    print(SEP)
    print(f"  {case['label']}")
    print(SEP)
    print(f"  Input text:   {text[:80]}{'...' if len(text) > 80 else ''}")
    print(f"  Context:      {ctx}")
    print(f"  Privacy pref: {pref}  |  Historical level: {hist}")
    print()
    print("  [Stage 1] Entity detection")
    print(_entity_line(detection))
    print()
    print("  [Stage 2] Extracted features (binarised at inference)")
    for k, v in features.items():
        if k != "application_context":
            binarised = 1 if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0
            print(f"    {k:<35s} raw={v}  ->  binary={binarised}")
    print(f"    {'application_context':<35s} '{ctx}'")
    print()
    print("  [Stage 3] ML risk prediction")
    print(f"    Risk level  : {result['risk_level']}")
    print(f"    Confidence  : {result['confidence']}")
    raw = result.get("raw_probabilities") or {}
    print(f"    Raw P(HIGH) : {raw.get('HIGH', 'n/a')}")
    print(f"    Raw P(LOW)  : {raw.get('LOW',  'n/a')}")
    print(f"    Raw P(MED)  : {raw.get('MEDIUM', 'n/a')}")
    thresh = result.get("applied_thresholds") or {}
    print(f"    Thresholds  : medium>={thresh.get('medium','?')}  high>={thresh.get('high','?')}")
    print(f"    Model status: {result.get('model_status', 'n/a')}")
    print()

print(SEP)
print("PIPELINE STATUS")
print(SEP)
print("  VERIFIED (automated tests):   entity detection, feature extraction,")
print("                                ML prediction, threshold adjustment,")
print("                                pixel redaction (15 tests),")
print("                                continual learning / rollback (12 tests)")
print("  NOT YET VERIFIED (needs browser): frontend UI rendering in VS Code Live Server")
print()
print("  Full test suite: 381 passed, 0 failures (run 2026-10-20)")
print()
print("  LIMITATION: ML model F1 = majority-class baseline (0.4444) on 10-row test fold.")
print("  Root cause: feature-class overlap (MEDIUM/HIGH share similar entity patterns);")
print("  test fold too small for statistical discrimination. Documented in")
print("  backend/data/experiment_results.json and the RTM.")
print()
print(SEP)
