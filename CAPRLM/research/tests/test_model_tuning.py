import numpy as np

from research.detection import Detector
from research.model.risk_model import ModelConfig, RiskModel, decide, tune_threshold

CLASSES = ["high", "low", "medium", "none"]


def test_threshold_protects_unless_confidently_none():
    p = np.array([0.05, 0.10, 0.25, 0.60])          # none is the argmax at 0.60
    assert decide(p, CLASSES, None) == "none"        # plain argmax
    assert decide(p, CLASSES, 0.5) == "none"         # 0.60 >= 0.5: confident enough
    assert decide(p, CLASSES, 0.9) == "medium"       # not confident: best non-none tier


def test_threshold_never_returns_none_below_it_even_if_none_is_top():
    p = np.array([0.0, 0.0, 0.0, 1.0])
    assert decide(p, CLASSES, 1.0) == "none"
    p = np.array([0.30, 0.0, 0.0, 0.70])
    assert decide(p, CLASSES, 0.95) == "high"


def test_tune_threshold_raises_recall_when_it_helps():
    # Sensitive spans (true "high") that plain argmax misses: the model gives "none" 55%.
    proba = np.array([[0.45, 0.0, 0.0, 0.55]] * 20 + [[0.0, 0.0, 0.0, 1.0]] * 20)
    y = ["high"] * 20 + ["none"] * 20
    tau, report = tune_threshold(proba, CLASSES, y)
    assert tau is not None and 0.45 < tau <= 1.0
    assert report["tuned"]["recall"] > report["argmax"]["recall"]


def test_shape_and_doc_features_carry_no_span_content():
    d = Detector(ner=None)
    text = "Your OTP is 849201 and password: Hunter22!"
    cands = d.detect(text)
    assert cands and all(c.shape and c.doc for c in cands)
    blob = " ".join(w for c in cands for w in c.shape + c.doc + c.window).lower()
    assert "849201" not in blob and "hunter22" not in blob
    assert "kw:password" in cands[0].doc and "kw:otp" in cands[0].doc


def test_model_with_threshold_predicts_only_known_tiers(model):
    cfg = ModelConfig("logreg", C=3.0, class_weight="balanced", none_threshold=0.9)
    assert cfg.label() == "logreg C=3 cw=balanced"
    assert isinstance(model, RiskModel) and model.version.startswith("caprlm/")
