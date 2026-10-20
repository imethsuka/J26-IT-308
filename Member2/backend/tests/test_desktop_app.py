"""Integration checks for the desktop adapters against the trained artifact."""
import pytest

from desktop_app.services import model_inspector


def test_model_load_preserves_bundle_schema_and_classes():
    info = model_inspector.load_model_info()
    assert info["model"].n_features_in_ == len(info["feature_names"]) == 8
    assert info["classes"] == list(info["model"].classes_)
    assert info["classes"] == ["HIGH", "LOW", "MEDIUM"]
    assert info["feature_names"] == [
        "EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH",
        "sensitive_entity_present", "application_context_encoded",
    ]


def test_actual_model_prediction_has_only_trained_probability_classes():
    info = model_inspector.load_model_info()
    values = {name: 0 for name in info["feature_names"] if name != "application_context_encoded"}
    result = model_inspector.predict(values, "Unknown")
    assert result["risk_level"] in info["classes"]
    assert set(result["raw_probabilities"]) == set(info["classes"])


def test_invalid_features_and_empty_context_are_rejected():
    with pytest.raises(ValueError, match="seven supported binary"):
        model_inspector.predict({"EMAIL": 2}, "Unknown")
    valid = {name: 0 for name in ["EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH", "sensitive_entity_present"]}
    with pytest.raises(ValueError, match="context must not be empty"):
        model_inspector.predict(valid, " ")


def test_feature_importance_matches_actual_model():
    info = model_inspector.load_model_info()
    importance = model_inspector.feature_importances(info)
    assert len(importance) == len(info["feature_names"])
    assert all(value >= 0 for _, value in importance)


def test_evaluation_log_is_read_as_recorded():
    record = model_inspector.evaluation()
    assert record is not None
    assert record["dataset_rows"] == 50
    assert record["test_size"] == 10
    assert record["test_classification_report"]["accuracy"] == pytest.approx(0.8)


def test_missing_model_artifact_fails_instead_of_fabricating_status(monkeypatch, tmp_path):
    monkeypatch.setattr(model_inspector.risk_prediction, "_MODEL_PATH", tmp_path / "missing.joblib")
    monkeypatch.setattr(model_inspector.risk_prediction, "_bundle", None)
    with pytest.raises(RuntimeError, match="ML model not found"):
        model_inspector.load_model_info()


def test_application_window_starts_with_actual_model(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from desktop_app.main import CaprlmWindow

    app = QApplication.instance() or QApplication([])
    window = CaprlmWindow()
    assert window.info["model"].n_features_in_ == 8
    assert window.nav.count() == 6
    window.close()
    app.processEvents()
