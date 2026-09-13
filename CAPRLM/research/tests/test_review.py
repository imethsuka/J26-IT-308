from datetime import date

import pytest
from fastapi.testclient import TestClient

from research.api import service
from research.profile_store import ProfileStore
from research.review import level_taus

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


def _entities(client, **extra):
    r = client.post("/review", json={**BODY, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_review_masks_and_never_returns_masked_values(client):
    res = _entities(client)
    assert res["entities"]
    for e in res["entities"]:
        assert 0.0 <= e["risk"] <= 1.0
        if e["masked"]:
            assert e["value"] is None
    assert "sarah.johnson@example.com" not in res["redacted_text"]
    assert "hunter22xyz" not in res["redacted_text"]          # credentials policy: never
    assert "".join(s["text"] for s in res["segments"]) == res["redacted_text"]


def test_override_changes_one_entity_and_the_text(client):
    res = _entities(client)
    email = next(e for e in res["entities"] if e["category"] == "EMAIL")
    shown = _entities(client, overrides={email["id"]: "visible"})
    assert "sarah.johnson@example.com" in shown["redacted_text"]
    again = next(e for e in shown["entities"] if e["id"] == email["id"])
    assert again["decided_by"] == "override" and not again["masked"]
    assert shown["residual_risk"] >= res["residual_risk"]


def test_strict_masks_at_least_as_much_as_relaxed(client):
    relaxed = _entities(client, level="relaxed")
    strict = _entities(client, level="strict")
    assert strict["masked"] >= relaxed["masked"]
    taus = strict["taus"]
    assert taus["strict"] <= taus["balanced"] <= taus["relaxed"]


def test_level_taus_follow_the_models_none_threshold(model):
    taus = level_taus(model)
    t = model.config.none_threshold
    assert taus["balanced"] == (round(1 - t, 2) if t is not None else 0.5)


def test_policy_group_can_be_paused_and_changed(client):
    r = client.put("/policy", json={"groups": {"identifiers": {"enabled": False}}})
    assert r.status_code == 200
    group = next(g for g in r.json()["groups"] if g["id"] == "identifiers")
    assert group["enabled"] is False
    email = next(e for e in _entities(client)["entities"] if e["category"] == "EMAIL")
    assert email["decided_by"] != "policy"
    assert client.put("/policy", json={"groups": {"nope": {"enabled": True}}}).status_code == 422


def test_corrections_become_a_learned_rule_only_after_enough_evidence(client):
    client.put("/policy", json={"groups": {"identifiers": {"enabled": False}}})
    assert client.post("/corrections", json={"category": "EMAIL", "decision": "visible"}).json()["rules"] == []
    rules = client.post("/corrections", json={"category": "EMAIL", "decision": "visible"}).json()["rules"]
    assert rules == [{"category": "EMAIL", "decision": "visible", "evidence": 2}]
    email = next(e for e in _entities(client)["entities"] if e["category"] == "EMAIL")
    assert email["decided_by"] == "learned" and not email["masked"]
    assert client.delete("/profile").json()["rules"] == []


def test_unknown_correction_category_rejected(client):
    assert client.post("/corrections", json={"category": "SHOE_SIZE", "decision": "masked"}).status_code == 422


def test_outbound_payload_follows_mask_before_send(client):
    on = _entities(client)["outbound"]
    assert on["mask_before_send"] and "sarah.johnson@example.com" not in on["text"]
    client.put("/policy", json={"llm": {"mask_before_send": False}})
    off = _entities(client)["outbound"]
    assert not off["mask_before_send"] and off["masked"] == 0 and "sarah.johnson@example.com" in off["text"]


def test_stats_count_only_recorded_reviews(client):
    _entities(client)                                   # not recorded
    assert client.get("/stats").json()["reviews_7d"] == 0
    res = _entities(client, record_activity=True)
    s = client.get("/stats").json()
    assert s["reviews_7d"] == 1 and s["redacted_7d"] == res["masked"] and s["flagged_7d"] == res["flagged"]
    assert len(s["weekly"]) == 7 and s["weekly"][-1]["date"] == date.today().isoformat()
    assert 0.0 <= s["utility_kept"] <= 1.0


def test_review_fails_closed(client):
    class Boom:
        def risk_scores(self, pairs):
            raise RuntimeError("x")

    service.state.model = Boom()
    r = client.post("/review", json=BODY)
    assert r.status_code in (500, 503)
