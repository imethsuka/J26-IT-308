import pytest
from fastapi.testclient import TestClient

from research.api import service
from research.data.placeholder_scenarios import generate
from research.eval.curve import build_curve
from research.model.explain import explain_pairs, family, global_effects, humanize
from research.profile_store import ProfileStore
from research.review import level_taus
from shared.types import PrivacyContext, Source

CTX = PrivacyContext(app="outlook", domain="mail.example.com", title="Inbox", source=Source.UIA)
TEXT = "Hi Chathura, your revised monthly salary is Rs. 185,000 and mail sarah.johnson@example.com about the plan."


@pytest.mark.parametrize("name, expected", [
    ("cat=EMAIL|ctx=finance", "an email in a finance context"),
    ("cat=EMAIL|ctx=email", "an email in an email context"),
    ("w=L:password", 'the word "password" just before it'),
    ("w=R:is", 'the word "is" just after it'),
    ("shape=dig2", "the text is mostly digits"),
    ("cat=EMAIL|shape=at", "an email that contains an @"),
    ("doc=kw:salary", "the page mentions salary"),
    ("det=ner", "found by the language model (NER)"),
    ("dom=news", "the site looks like a news site"),
    ("appb=email", "the app looks like an email app"),
    ("cat=NATIONAL_ID", "it is a national ID"),
    ("something=odd", "something=odd"),
])
def test_feature_names_become_plain_phrases(name, expected):
    assert humanize(name) == expected


def test_every_feature_belongs_to_a_kind_of_evidence():
    assert family("cat=EMAIL") == "What it is"
    assert family("cat=EMAIL|ctx=finance") == "Where it appears"      # a crossed feature belongs to the thing it is crossed with
    assert family("w=L:hi") == "Words around it" and family("cat=PERSON|w=L:hi") == "Words around it"
    assert family("doc=kw:salary") == "The page"


def test_global_effects_list_the_features_that_raise_each_tier(model):
    effects = global_effects(model, top=5)

    assert set(effects) == {"low", "medium", "high"}
    for rows in effects.values():
        assert 0 < len(rows) <= 5
        assert all(r["weight"] > 0 for r in rows)                            # strongest raisers first, all positive
        assert [r["weight"] for r in rows] == sorted((r["weight"] for r in rows), reverse=True)
        assert all("confidence" not in r["feature"] for r in rows)


def test_a_span_is_explained_by_kinds_of_evidence_that_add_up(detector, model):
    cands = detector.detect(TEXT)
    pairs = [(c, CTX) for c in cands]
    scores = model.risk_scores(pairs)

    explained = explain_pairs(model, pairs, [t for _, t in scores])

    assert len(explained) == len(cands)
    for items in explained:
        assert len(items) <= 3
        assert all({"family", "label", "effect"} <= item.keys() for item in items)
        assert [abs(i["effect"]) for i in items] == sorted((abs(i["effect"]) for i in items), reverse=True)
        assert len({i["family"] for i in items}) == len(items)               # one line per kind of evidence


def test_no_explanation_for_a_model_that_is_not_linear(detector):
    from research.model import ModelConfig, RiskModel
    from research.model.training import build_pairs

    pairs, tiers = build_pairs(generate(60, 3, test=False), detector)
    forest = RiskModel(ModelConfig(kind="randomforest")).fit(pairs, tiers)

    assert global_effects(forest) is None
    assert explain_pairs(forest, pairs[:2], ["high", "high"]) is None


def test_the_curve_trades_recall_for_precision_and_reports_categories(detector, model):
    curve = build_curve(model, detector, generate(40, 9, test=True), level_taus(model))

    pts = curve["points"]
    assert pts == sorted(pts, key=lambda p: p["tau"])
    recalls = [p["recall"] for p in pts]
    assert recalls == sorted(recalls, reverse=True)                          # a higher bar can only miss more
    assert all(0 <= p["precision"] <= 1 and 0 <= p["recall"] <= 1 for p in pts)
    assert {"relaxed", "balanced", "strict"} <= {t for t in curve["presets"]}
    assert any(abs(p["tau"] - curve["presets"]["balanced"]) < 1e-9 for p in pts)   # the presets are points on the curve
    assert curve["categories"] and all(c["sensitive"] > 0 for c in curve["categories"])
    assert curve["n_sensitive"] <= curve["n_spans"]


@pytest.fixture()
def client(filters, detector, model, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "load_or_train_default", lambda d: model)
    with TestClient(service.app) as c:
        service.state.detector = detector
        service.state.model = model
        service.state.filters = filters
        service.state.store = ProfileStore(tmp_path / "profile.json")
        service.state.curve = None
        yield c


def test_review_entities_carry_their_reasons(client):
    body = {"text": TEXT, "context": {"app": "outlook", "domain": "mail.example.com", "title": "Inbox", "source": "uia"}}
    entities = client.post("/review", json=body).json()["entities"]

    assert entities and all("why" in e for e in entities)
    assert any(e["why"] for e in entities)
    assert all(set(i) == {"family", "label", "effect"} for e in entities for i in e["why"])


def test_model_info_describes_the_model(client):
    info = client.get("/model-info").json()

    assert info["kind"] == "logreg" and info["explainable"] is True and info["n_features"] > 0
    assert set(info["classes"]) == {"none", "low", "medium", "high"}
    assert set(info["effects"]) == {"low", "medium", "high"}
    assert set(info["taus"]) == {"relaxed", "balanced", "strict"}


def test_model_curve_is_computed_once_and_cached(client, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "_curve_scenarios", lambda: calls.append(1) or generate(30, 5, test=True))

    first = client.get("/model-curve").json()
    second = client.get("/model-curve").json()

    assert first == second and len(calls) == 1 and first["points"]


def test_model_curve_says_so_when_the_scenarios_are_not_installed(client, monkeypatch):
    from fastapi import HTTPException

    def missing():
        raise HTTPException(status_code=404, detail="The labelled scenarios are not available on this install, so no curve can be drawn.")

    monkeypatch.setattr(service, "_curve_scenarios", missing)

    r = client.get("/model-curve")
    assert r.status_code == 404 and "not available" in r.json()["detail"]
