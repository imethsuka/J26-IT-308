"""Evaluation metrics — only the ones the project's evaluation plan names:
detection Precision/Recall/F1, leakage rate / re-identification success (privacy), and
semantic similarity before/after (utility). Personalization before/after is NOT computed:
no personalization mechanism exists yet.

All of them are judged on what a filter *outputs* (the text), by looking for each gold
entity's surface string, so every filter — including rule_based, which has its own
detection — is measured the same way.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from shared.privacy_filter import PrivacyFilter
from shared.types import FilterResult

from research.data.placeholder_scenarios import Scenario

log = logging.getLogger(__name__)

SIMILARITY_METHOD = "lexical_tfidf_cosine"   # a lexical proxy, NOT an embedding-based semantic score
_TOKEN = re.compile(r"[A-Za-z0-9]{4,}")


def identifying_tokens(surface: str, category: str) -> set[str]:
    """Tokens that would let someone recover the entity. For emails only the local part
    counts — the domain ("example.com") is shared by many people. Crude by design: this is
    a default for research decision #5, not a model-based re-identification attack."""
    if category == "EMAIL":
        surface = surface.split("@", 1)[0]
    return {t.lower() for t in _TOKEN.findall(surface)}


def _tokens_present(tokens: set[str], text: str) -> bool:
    out = {t.lower() for t in _TOKEN.findall(text)}
    return bool(tokens & out)


@dataclass
class _Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    sensitive: int = 0
    leaked: int = 0
    reidentified: int = 0
    similarities: list[float] = field(default_factory=list)
    blocked: int = 0
    errors: int = 0
    docs: int = 0


def _safe_filter(filt: PrivacyFilter, sc: Scenario, counts: _Counts) -> FilterResult:
    """Fail-closed, as a caller would: an exception counts as blocked content."""
    try:
        return filt.filter(sc.text, sc.ctx)
    except Exception as exc:
        counts.errors += 1
        log.error("%s raised %s; scoring as blocked", type(filt).__name__, type(exc).__name__)
        return FilterResult(text="", blocked=True)


def evaluate_filter(filt: PrivacyFilter, scenarios: list[Scenario]) -> dict:
    counts = _Counts()
    vectorizer = TfidfVectorizer(token_pattern=r"[A-Za-z0-9_]+").fit([s.text for s in scenarios])

    for sc in scenarios:
        res = _safe_filter(filt, sc, counts)
        counts.docs += 1
        counts.blocked += res.blocked
        out = "" if res.blocked else res.text

        for g in sc.gold:
            protected = g.surface not in out
            sensitive = g.tier != "none"
            if sensitive:
                counts.sensitive += 1
                if protected:
                    counts.tp += 1
                else:
                    counts.fn += 1
                    counts.leaked += 1
                if _tokens_present(identifying_tokens(g.surface, g.category), out):
                    counts.reidentified += 1
            elif protected:
                counts.fp += 1   # over-redaction: protected something the gold says needs no protection

        sim = 0.0
        if out.strip():
            a, b = vectorizer.transform([sc.text]), vectorizer.transform([out])
            sim = float(cosine_similarity(a, b)[0, 0])
        counts.similarities.append(sim)

    precision = counts.tp / (counts.tp + counts.fp) if counts.tp + counts.fp else 0.0
    recall = counts.tp / (counts.tp + counts.fn) if counts.tp + counts.fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "leakage_rate": counts.leaked / counts.sensitive if counts.sensitive else 0.0,
        "reidentification_rate": counts.reidentified / counts.sensitive if counts.sensitive else 0.0,
        "semantic_similarity": sum(counts.similarities) / len(counts.similarities) if counts.similarities else 0.0,
        "blocked_rate": counts.blocked / counts.docs if counts.docs else 0.0,
        "errors": counts.errors,
    }
