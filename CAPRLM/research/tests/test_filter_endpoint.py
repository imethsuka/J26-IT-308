import pytest
from fastapi.testclient import TestClient

from research.api import service
from research.profile_store import ProfileStore

BODY = {
    "text": "Mail sarah.johnson@example.com about the invoice, password: hunter22xyz.",
    "context": {"app": "chrome", "domain": "mail.google.com", "title": "Inbox", "source": "uia"},
}


@pytest.fixture()
def client(filters, detector, model, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "load_or_train_default", lambda d: model)
    with TestClient(service.app) as c:
        service.state.detector = detector
        service.state.model = model
        service.state.filters = filters
        service.state.store = ProfileStore(tmp_path / "profile.json")
        yield c


def test_filter_returns_redacted_text_and_nothing_else(client):
    r = client.post("/filter", json=BODY)
    assert r.status_code == 200
    out = r.json()
    assert set(out) == {"text", "blocked", "redactions", "filter_version"}
    assert out["blocked"] is False and out["redactions"] >= 1
    assert "sarah.johnson@example.com" not in out["text"]
    assert out["filter_version"].startswith("caprlm/")


def test_filter_blocked_when_the_filter_fails_closed(client):
    class Boom:
        version = "boom/0"

        def filter(self, text, ctx):
            raise RuntimeError("x")

    service.state.filters = {**service.state.filters, "caprlm": Boom()}
    r = client.post("/filter", json=BODY)
    assert r.status_code == 503 and "blocked" in r.json()["detail"]


def test_filter_rejects_a_bad_source(client):
    bad = {**BODY, "context": {**BODY["context"], "source": "telepathy"}}
    assert client.post("/filter", json=bad).status_code == 422
