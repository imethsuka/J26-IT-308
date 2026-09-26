"""Why the risk model says what it says.

CAPRLM's model is a logistic regression over named features (research/model/features.py), so its reasoning can be read straight from
its learned weights: nothing here approximates or re-fits the model. For a protected tier ``t`` the weight of a feature is
``coef[t] - coef[none]``: how much having that feature moves the model from "harmless" toward ``t``.

* ``global_effects``: for each tier, the features with the largest positive weights (what makes something *that* risky).
* ``explain_pairs``: for one detected span in its context, the features present on it with the largest weight times value, i.e. why
  *this* span got its score. Positive pushes toward protecting, negative toward leaving it visible.

Only logistic regression is explainable this way; for other model kinds these return None and callers show no reasons.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression

from research.detection.candidate import Candidate
from shared.types import PrivacyContext

from .features import featurize
from .risk_model import RiskModel

_NICE = {"FINANCIAL_FIGURE": "a money amount", "NATIONAL_ID": "a national ID", "CARD_LIKE_NUMBER": "a card-like number",
         "BANK_ACCOUNT": "a bank account", "IP_ADDRESS": "an IP address", "URL_TOKEN": "a token in a URL",
         "VEHICLE_PLATE": "a vehicle plate"}


def _cat(c: str) -> str:
    return _NICE.get(c, "an " + c.replace("_", " ").lower() if c[:1] in "AEIOU" else "a " + c.replace("_", " ").lower())


_LEN = ("is very short (under 4 characters)", "is short (4 to 7 characters)", "is medium length (8 to 15)", "is long (16 to 31)",
        "is very long (32 or more)")
_DIG = ("has no digits", "has a few digits", "is mostly digits", "is all digits")
_UP = ("has no capitals", "has some capitals", "is mostly capitals", "is all capitals")
_FLAGS = {"at": "contains an @", "dash": "contains a dash", "space": "contains a space", "dot": "contains a dot",
          "slash": "contains a slash", "cap": "starts with a capital", "sym": "contains symbols"}
_DOCLEN = ("a very short text", "a short text", "a medium-length text", "a long text", "a very long text")


def _shape(value: str) -> str:
    m = re.fullmatch(r"(len|dig|up)(\d)", value)
    if m:
        table = {"len": _LEN, "dig": _DIG, "up": _UP}[m.group(1)]
        i = int(m.group(2))
        return table[i] if i < len(table) else value
    return _FLAGS.get(value, value)


def _doc(value: str) -> str:
    if value.startswith("kw:"):
        return f"the page mentions {value[3:]}"
    m = re.fullmatch(r"len(\d)", value)
    if m and int(m.group(1)) < len(_DOCLEN):
        return f"it sits in {_DOCLEN[int(m.group(1))]}"
    m = re.fullmatch(r"n(\d)", value)
    if m:
        return ("only one sensitive-looking item on the page", "a couple of items on the page", "several items on the page", "many items on the page")[min(int(m.group(1)), 3)]
    return f"page signal: {value}"


_FAMILIES = {"cat": "What it is", "det": "What it is", "confidence": "What it is", "src": "How it was read",
             "app": "The app", "appb": "The app", "dom": "Where it appears", "title": "Where it appears", "ctx": "Where it appears",
             "w": "Words around it", "shape": "How the text looks", "doc": "The page"}


def family(name: str) -> str:
    """Which kind of evidence a feature is: the crossed features (category|something) belong to the "something"."""
    head = name.split("|", 1)[1] if "|" in name else name
    return _FAMILIES.get(head.partition("=")[0], "Other")


def humanize(name: str) -> str:
    """A model feature name as a short phrase: ``cat=EMAIL|ctx=finance`` -> "an email in a finance context"."""
    if "|" in name:
        left, right = name.split("|", 1)
        if left.startswith("cat="):
            what = _cat(left[4:])
            kind, _, value = right.partition("=")
            if kind == "ctx":
                return f"{what} in {_a(value)} context"
            if kind == "app":
                return f"{what} in {value}"
            if kind == "src":
                return f"{what} read by {'OCR' if value == 'ocr' else 'UI Automation'}"
            if kind == "w":
                return f"{what} with {_word(value)}"
            if kind == "shape":
                return f"{what} that {_shape(value)}"
            if kind == "doc":
                return f"{what}, and {_doc(value)}"
    kind, _, value = name.partition("=")
    if kind == "cat":
        return f"it is {_cat(value)}"
    return {
        "confidence": lambda: "how sure the detector is",
        "det": lambda: f"found by {'a pattern rule' if value == 'regex' else 'the language model (NER)'}",
        "src": lambda: f"read by {'OCR' if value == 'ocr' else 'UI Automation'}",
        "app": lambda: f"the app is {value}",
        "appb": lambda: f"the app looks like {_a(value)} app",
        "dom": lambda: "the site is not a known kind" if value == "none" else f"the site looks like {_a(value)} site",
        "title": lambda: f"the window title suggests {value}",
        "w": lambda: _word(value),
        "shape": lambda: f"the text {_shape(value)}",
        "doc": lambda: _doc(value),
    }.get(kind, lambda: name)()


def _a(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _word(value: str) -> str:
    side, _, word = value.partition(":")
    return f'the word "{word}" {"just before" if side == "L" else "just after"} it' if word else value


def _linear(model: RiskModel):
    """(vectorizer, feature names, classes, coef matrix) for a fitted logistic regression, else None."""
    if not model.fitted:
        return None
    vec, clf = model.pipeline.steps[0][1], model.pipeline.steps[-1][1]
    if not isinstance(clf, LogisticRegression):
        return None
    classes = [str(c) for c in clf.classes_]
    coef = np.asarray(clf.coef_)
    if coef.shape[0] == 1 and len(classes) == 2:           # binary: one row, for classes_[1]
        coef = np.vstack([-coef[0] / 2, coef[0] / 2])
    return vec, np.asarray(vec.get_feature_names_out()), classes, coef


def _weights(classes: list[str], coef: np.ndarray, tier: str) -> np.ndarray | None:
    """How much each feature moves the model from 'none' toward ``tier``."""
    if tier not in classes:
        return None
    base = coef[classes.index("none")] if "none" in classes else 0.0
    return coef[classes.index(tier)] - base


def global_effects(model: RiskModel, *, top: int = 8) -> dict[str, list[dict[str, Any]]] | None:
    """tier -> the features that most raise the model's belief in that tier (label, raw feature name, weight)."""
    lin = _linear(model)
    if lin is None:
        return None
    _, names, classes, coef = lin
    out: dict[str, list[dict[str, Any]]] = {}
    for tier in ("low", "medium", "high"):
        w = _weights(classes, coef, tier)
        if w is None:
            continue
        # skip the near-constant confidence feature: its weight is not a reason a person can read
        order = [i for i in np.argsort(-w) if names[i] != "confidence"][:top]
        out[tier] = [{"feature": str(names[i]), "label": humanize(str(names[i])), "weight": round(float(w[i]), 3)} for i in order]
    return out


def explain_pairs(model: RiskModel, pairs: list[tuple[Candidate, PrivacyContext]], tiers: list[str], *, top: int = 3) -> list[list[dict[str, Any]]] | None:
    """For each span, the kinds of evidence that moved its score most, strongest first. Each item has the family ("Where it appears"),
    the single most influential feature in that family as a phrase, and the family's net effect (positive pushes toward protecting
    the span as its tier, negative toward leaving it visible)."""
    lin = _linear(model)
    if lin is None:
        return None
    vec, names, classes, coef = lin
    if not pairs:
        return []
    X = vec.transform([featurize(c, x, model.config.features) for c, x in pairs])
    out: list[list[dict[str, Any]]] = []
    for row, tier in enumerate(tiers):
        w = _weights(classes, coef, tier)
        if w is None:
            out.append([])
            continue
        r = X[row]
        groups: dict[str, list[tuple[float, str]]] = {}
        for j, v in zip(r.indices, r.data):
            if names[j] == "confidence":            # a raw detector number, not a reason a person can act on
                continue
            groups.setdefault(family(str(names[j])), []).append((float(w[j] * v), str(names[j])))
        items = []
        for fam, feats in groups.items():
            total = sum(c for c, _ in feats)
            lead = max(feats, key=lambda t: abs(t[0]))
            items.append({"family": fam, "label": humanize(lead[1]), "effect": round(total, 2)})
        items.sort(key=lambda d: -abs(d["effect"]))
        out.append([i for i in items if abs(i["effect"]) >= 0.15][:top])
    return out
