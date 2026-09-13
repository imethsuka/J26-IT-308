"""Unit tests for the rule-based importance baseline.

Each rule branch (dwell threshold, revisit flag, app category) is covered in isolation, plus the
combined extremes and the shared-interface contract.
"""

from __future__ import annotations

import math

import pytest

from models.base import ImportanceScorer
from models.rule_based import RuleBasedImportanceScorer


def features(dwell=0.0, seen_before=0, app=None, **extra) -> dict:
    return {"tmp_dwell_seconds": dwell, "ctx_seen_before": seen_before, "ctx_app": app, **extra}


@pytest.fixture
def scorer() -> RuleBasedImportanceScorer:
    return RuleBasedImportanceScorer()


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# --------------------------------------------------------------------------- interface

def test_implements_shared_interface(scorer) -> None:
    assert isinstance(scorer, ImportanceScorer)
    p = scorer.predict_proba(features())
    assert isinstance(p, float) and 0.0 <= p <= 1.0


def test_predict_thresholds_the_probability(scorer) -> None:
    strong = features(dwell=120, seen_before=1, app="code")
    weak = features(dwell=0, seen_before=0, app="vlc")
    assert scorer.predict(strong) == 1
    assert scorer.predict(weak) == 0


def test_neutral_features_give_the_prior(scorer) -> None:
    # No dwell, never seen, unknown app -> logit == bias.
    p = scorer.predict_proba(features(app="some_unknown_app"))
    assert p == pytest.approx(sigmoid(scorer.bias), abs=1e-9)


# --------------------------------------------------------------------------- rule 1: dwell threshold

def test_dwell_below_min_contributes_nothing(scorer) -> None:
    assert scorer.explain(features(dwell=scorer.dwell_min))["dwell_component"] == 0.0
    assert scorer.explain(features(dwell=0))["dwell_component"] == 0.0


def test_dwell_at_saturation_contributes_full(scorer) -> None:
    assert scorer.explain(features(dwell=scorer.dwell_sat))["dwell_component"] == 1.0
    assert scorer.explain(features(dwell=999))["dwell_component"] == 1.0


def test_dwell_ramps_linearly_between(scorer) -> None:
    mid = (scorer.dwell_min + scorer.dwell_sat) / 2
    assert scorer.explain(features(dwell=mid))["dwell_component"] == pytest.approx(0.5, abs=1e-6)


def test_longer_dwell_never_lowers_probability(scorer) -> None:
    probs = [scorer.predict_proba(features(dwell=d)) for d in (0, 10, 30, 60, 120)]
    assert probs == sorted(probs)


# --------------------------------------------------------------------------- rule 2: revisit flag

def test_revisit_flag_raises_probability(scorer) -> None:
    base = scorer.predict_proba(features(seen_before=0))
    revisited = scorer.predict_proba(features(seen_before=1))
    assert revisited > base
    assert scorer.explain(features(seen_before=1))["revisit_component"] == 1.0
    assert scorer.explain(features(seen_before=0))["revisit_component"] == 0.0


def test_revisit_contribution_matches_weight(scorer) -> None:
    delta_logit = scorer.explain(features(seen_before=1))["logit"] - scorer.explain(features(seen_before=0))["logit"]
    assert delta_logit == pytest.approx(scorer.w_revisit, abs=1e-9)


# --------------------------------------------------------------------------- rule 3: app category

@pytest.mark.parametrize(
    "app,category,weight",
    [
        ("code", "productivity", 1.0),
        ("Code.exe", "productivity", 1.0),
        ("outlook", "communication", 0.5),
        ("chrome", "browser", 0.0),
        ("vlc", "media", -1.0),
        ("some_random_tool", "unknown", 0.0),
        (None, "unknown", 0.0),
    ],
)
def test_app_category_mapping(scorer, app, category, weight) -> None:
    info = scorer.explain(features(app=app))
    assert info["app_category"] == category
    assert info["app_component"] == weight


def test_productivity_app_beats_media_app(scorer) -> None:
    assert scorer.predict_proba(features(app="code")) > scorer.predict_proba(features(app="vlc"))


def test_browser_is_neutral_between_productivity_and_media(scorer) -> None:
    prod = scorer.predict_proba(features(app="code"))
    browser = scorer.predict_proba(features(app="chrome"))
    media = scorer.predict_proba(features(app="vlc"))
    assert media < browser < prod


# --------------------------------------------------------------------------- combination + explain

def test_all_signals_high_is_near_certain(scorer) -> None:
    p = scorer.predict_proba(features(dwell=120, seen_before=1, app="code"))
    expected = sigmoid(scorer.bias + scorer.w_dwell + scorer.w_revisit + scorer.w_app)
    assert p == pytest.approx(expected, abs=1e-9)
    assert p > 0.95


def test_all_signals_low_is_unlikely(scorer) -> None:
    p = scorer.predict_proba(features(dwell=0, seen_before=0, app="vlc"))
    expected = sigmoid(scorer.bias - scorer.w_app)
    assert p == pytest.approx(expected, abs=1e-9)
    assert p < 0.1


def test_explain_contributions_sum_to_logit(scorer) -> None:
    info = scorer.explain(features(dwell=30, seen_before=1, app="outlook"))
    total = sum(info["contributions"].values())
    assert total == pytest.approx(info["logit"], abs=1e-6)
    assert info["probability"] == pytest.approx(sigmoid(info["logit"]), abs=1e-6)


def test_missing_feature_keys_are_treated_as_neutral(scorer) -> None:
    # An empty dict must not raise; it should behave like all-neutral.
    assert scorer.predict_proba({}) == pytest.approx(sigmoid(scorer.bias), abs=1e-9)


# --------------------------------------------------------------------------- configurability

def test_weights_are_overridable() -> None:
    custom = RuleBasedImportanceScorer(w_revisit=0.0)
    assert custom.predict_proba(features(seen_before=1)) == custom.predict_proba(features(seen_before=0))


def test_invalid_dwell_bounds_rejected() -> None:
    with pytest.raises(ValueError):
        RuleBasedImportanceScorer(dwell_min_seconds=60, dwell_sat_seconds=60)


def test_custom_app_category_weight() -> None:
    scorer = RuleBasedImportanceScorer(
        app_categories={"myapp": "productivity"}, category_weights={"productivity": 2.0}
    )
    assert scorer.explain(features(app="myapp"))["app_component"] == 2.0
