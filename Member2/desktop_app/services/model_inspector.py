"""Read-only desktop adapters for the existing CAPRLM services."""
from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

from backend.services import risk_prediction

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "backend" / "data" / "caprlm_model.joblib"
EVALUATION_PATH = ROOT / "backend" / "data" / "training_log.json"


def load_model_info() -> dict[str, Any]:
    bundle = risk_prediction.get_bundle()
    model = bundle["model"]
    names = list(bundle.get("feature_names", []))
    classes = list(bundle.get("classes", []))
    if not names or len(names) != getattr(model, "n_features_in_", len(names)):
        raise ValueError("Model feature metadata does not match the loaded estimator.")
    return {
        "model": model,
        "artifact": MODEL_PATH,
        "model_type": type(model).__name__,
        "feature_names": names,
        "classes": classes,
        "n_estimators": getattr(model, "n_estimators", None),
        "max_depth": getattr(model, "max_depth", None),
        "version": bundle.get("model_version"),
        "bundle": bundle,
    }


def feature_importances(info: dict[str, Any]) -> list[tuple[str, float]]:
    model = info["model"]
    if not hasattr(model, "feature_importances_"):
        return []
    values = list(model.feature_importances_)
    if len(values) != len(info["feature_names"]):
        return []
    return sorted(zip(info["feature_names"], map(float, values)), key=lambda p: p[1], reverse=True)


def predict(values: dict[str, Any], context: str) -> dict[str, Any]:
    # Map the displayed schema to the exact runtime input consumed by the
    # existing prediction service; that service preserves bundle feature order.
    expected = {"EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH", "sensitive_entity_present"}
    if set(values) != expected or any(type(value) is not int or value not in (0, 1) for value in values.values()):
        raise ValueError("Provide exactly the seven supported binary features, each set to 0 or 1.")
    if not isinstance(context, str) or not context.strip():
        raise ValueError("Application context must not be empty.")
    context = context.strip()
    features = {
        "email_count": values["EMAIL"], "phone_count": values["PHONE"],
        "url_count": values["URL"], "ip_count": values["IP_ADDRESS"],
        "date_count": values["DATE"], "windows_path_count": values["WINDOWS_PATH"],
        "sensitive_entity_count": values["sensitive_entity_present"],
        "application_context": context,
    }
    result = risk_prediction.predict_privacy_risk(features, context, "Medium", "Medium")
    if result.get("risk_level") == "UNAVAILABLE":
        raise RuntimeError(result.get("model_status", "Model is unavailable."))
    # The shared backend adds CRITICAL=0 for threshold compatibility, even
    # when CRITICAL was absent from model.classes_. Hide that synthetic entry.
    trained = list(risk_prediction.get_bundle().get("classes", []))
    if result.get("raw_probabilities") is not None:
        result["raw_probabilities"] = {
            key: value for key, value in result["raw_probabilities"].items() if key in trained
        }
    return result


def evaluation() -> dict[str, Any] | None:
    if not EVALUATION_PATH.is_file():
        return None
    entries = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
    return entries[-1] if isinstance(entries, list) and entries else None


def system_info() -> dict[str, str]:
    import joblib
    import sklearn
    return {"Python": platform.python_version(), "scikit-learn": sklearn.__version__,
            "joblib": joblib.__version__, "Model artifact": str(MODEL_PATH),
            "Artifact exists": str(MODEL_PATH.is_file())}
