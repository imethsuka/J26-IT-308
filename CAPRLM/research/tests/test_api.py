import base64
import json

import pytest
from fastapi.testclient import TestClient

from research.api import service
from research.detection import Detector, OcrUnavailableError
from research.filters import FILTER_NAMES

BODY = {
    "text": "Mail sarah.johnson@example.com or call 077-123-4567.",
    "context": {"app": "chrome", "domain": "mail.google.com", "title": "Inbox", "source": "uia"},
}


@pytest.fixture()
def client(filters, detector, model, monkeypatch):
    # Bypass the lifespan's disk-backed model load; install the fixtures' state directly.
    monkeypatch.setattr(service, "load_or_train_default", lambda d: model)
    with TestClient(service.app) as c:
        service.state.filters = filters
        service.state.detector = detector
        yield c


def test_compare_returns_all_five_filters_keyed_by_name(client):
    r = client.post("/compare", json=BODY)
    assert r.status_code == 200
    results = r.json()["results"]
    assert tuple(results) == FILTER_NAMES
    for res in results.values():
        assert {"text", "blocked", "redactions", "filter_version", "audit"} <= res.keys()
    assert "sarah.johnson@example.com" not in results["caprlm"]["text"]
    assert results["caprlm"]["filter_version"].startswith("caprlm/")


def test_compare_filter_error_comes_back_blocked_not_passed_through(client):
    class Boom:
        version = "boom/0"

        def filter(self, text, ctx):
            raise RuntimeError("x")

    service.state.filters = {**service.state.filters, "caprlm": Boom()}
    res = client.post("/compare", json=BODY).json()["results"]["caprlm"]
    assert res["blocked"] is True and res["text"] == "" and res["error"]


def test_compare_rejects_bad_source(client):
    bad = {**BODY, "context": {**BODY["context"], "source": "telepathy"}}
    assert client.post("/compare", json=bad).status_code == 422


def test_compare_image_503_when_ocr_unavailable(client):
    class NoOcr:
        def image_to_text(self, image):
            raise OcrUnavailableError("no tesseract")

    service.state.detector = Detector(service.state.detector.ner, ocr=NoOcr())
    r = client.post("/compare-image", json={"image_base64": base64.b64encode(b"x").decode(), "context": BODY["context"]})
    assert r.status_code == 503 and "blocked" in r.json()["detail"]


def test_compare_image_runs_ocr_text_through_all_filters(client):
    class FakeOcr:
        def image_to_text(self, image):
            return "call 077-123-4567"

    service.state.detector = Detector(service.state.detector.ner, ocr=FakeOcr())
    r = client.post("/compare-image", json={"image_base64": base64.b64encode(b"x").decode(), "context": BODY["context"]})
    assert r.status_code == 200
    assert r.json()["ocr_text"] == "call 077-123-4567"
    assert "077-123-4567" not in r.json()["results"]["static_masking"]["text"]


def test_compare_image_rejects_invalid_base64(client):
    r = client.post("/compare-image", json={"image_base64": "***", "context": BODY["context"]})
    assert r.status_code == 400


def test_eval_results_404_names_the_command_then_serves_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "RESULTS_PATH", tmp_path / "results.json")
    r = client.get("/eval-results")
    assert r.status_code == 404 and "run_comparison" in r.json()["detail"]
    (tmp_path / "results.json").write_text(json.dumps({"dataset": "x"}), encoding="utf-8")
    assert client.get("/eval-results").json() == {"dataset": "x"}
