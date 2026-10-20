"""Off-the-shelf NER (spaCy) mapped onto CAPRLM's entity categories.

Model choice is a default: `en_core_web_sm` because it is small enough for fast iteration;
`en_core_web_trf` is more accurate (notably on PERSON) and is selected with
CAPRLM_SPACY_MODEL=en_core_web_trf. If the model isn't installed this raises
NerUnavailableError — it does NOT fall back to regex-only, because silently detecting less
is a privacy regression, not a graceful degradation (callers fail closed instead).
"""

from __future__ import annotations

import os

from .candidate import Candidate

DEFAULT_MODEL = "en_core_web_sm"

_LABEL_MAP = {
    "PERSON": "PERSON",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "FAC": "LOCATION",
    "ORG": "ORG",
    "DATE": "DATE",
    "MONEY": "FINANCIAL_FIGURE",
}

# spaCy's pipeline exposes no per-entity probability, so NER spans get one constant
# confidence. The model's `detector=ner` feature carries the real information (NER spans
# are less reliable than regex ones); this number is not a calibrated score.
NER_CONFIDENCE = 0.8


class NerUnavailableError(RuntimeError):
    pass


class SpacyNerDetector:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.environ.get("CAPRLM_SPACY_MODEL", DEFAULT_MODEL)
        self._nlp = None

    def _load(self):
        if self._nlp is None:
            try:
                import spacy
                self._nlp = spacy.load(self.model_name, disable=["lemmatizer"])
            except Exception as exc:  # ImportError, OSError (model missing), ...
                raise NerUnavailableError(
                    f"spaCy model '{self.model_name}' could not be loaded "
                    f"(python -m spacy download {self.model_name}): {type(exc).__name__}"
                ) from exc
        return self._nlp

    def detect(self, text: str) -> list[Candidate]:
        doc = self._load()(text)
        return [
            Candidate(e.start_char, e.end_char, _LABEL_MAP[e.label_], NER_CONFIDENCE, "ner")
            for e in doc.ents
            if e.label_ in _LABEL_MAP
        ]
