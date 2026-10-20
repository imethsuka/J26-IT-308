"""The detection stage: regex + NER (+ OCR for screenshots) -> candidate spans."""

from __future__ import annotations

import dataclasses
import re

from .candidate import Candidate
from .ner_detector import SpacyNerDetector
from .ocr import OcrEngine, TesseractOcr
from .regex_detector import RegexDetector


class Detector:
    def __init__(self, ner: SpacyNerDetector | None, regex: RegexDetector | None = None,
                 ocr: OcrEngine | None = None):
        # ner=None means regex-only, and must be asked for explicitly (tests, ablations).
        self.ner = ner
        self.regex = regex or RegexDetector()
        self.ocr = ocr or TesseractOcr()

    @classmethod
    def default(cls) -> "Detector":
        return cls(SpacyNerDetector())

    def detect(self, text: str) -> list[Candidate]:
        regex_cands = self.regex.detect(text)
        if self.ner is None:
            cands = regex_cands
        else:
            # Regex wins any overlap: its structured matches are the precise ones.
            ner_cands = [c for c in self.ner.detect(text) if not any(c.overlaps(r) for r in regex_cands)]
            cands = sorted(regex_cands + ner_cands, key=lambda c: c.start)
        doc = _doc_signals(text, len(cands))
        return [dataclasses.replace(c, window=_window(text, c), shape=_shape(c.surface(text)), doc=doc)
                for c in cands]

    def ocr_text(self, image: bytes) -> str:
        return self.ocr.image_to_text(image)

    def info(self) -> dict:
        return {"ner_model": self.ner.model_name if self.ner else None}


_WORD = re.compile(r"[^\W\d_]+|\d+")


def _norm(w: str) -> str:
    return "0" if w.isdigit() else w.lower()


def _window(text: str, c: Candidate, left: int = 3, right: int = 2) -> tuple[str, ...]:
    """Words around (not inside) the span, tagged by side: ("L:password", "R:is")."""
    lw = [_norm(w) for w in _WORD.findall(text[max(0, c.start - 60):c.start])][-left:]
    rw = [_norm(w) for w in _WORD.findall(text[c.end:c.end + 40])][:right]
    return tuple(f"L:{w}" for w in lw) + tuple(f"R:{w}" for w in rw)


def _bucket(v: float, edges: tuple[float, ...]) -> int:
    return sum(v >= e for e in edges)


def _shape(span: str) -> tuple[str, ...]:
    """Length / digit-ratio / case buckets plus a few character-class flags."""
    n = max(len(span), 1)
    digits = sum(ch.isdigit() for ch in span) / n
    letters = [ch for ch in span if ch.isalpha()]
    upper = (sum(ch.isupper() for ch in letters) / len(letters)) if letters else 0.0
    flags = [f"len{_bucket(len(span), (4, 8, 16, 32))}",
             f"dig{_bucket(digits, (0.01, 0.5, 0.99))}",
             f"up{_bucket(upper, (0.01, 0.5, 0.99))}"]
    flags += [name for name, ch in (("at", "@"), ("dash", "-"), ("space", " "), ("dot", "."), ("slash", "/"))
              if ch in span]
    if span[:1].isupper():
        flags.append("cap")
    if any(not (ch.isalnum() or ch.isspace()) for ch in span):
        flags.append("sym")
    return tuple(flags)


# A general lexicon of words that make a document likelier to be sensitive. Written from
# general knowledge, not from the dataset; a default to review (BUILD_LOG.md).
_DOC_KEYWORDS = (
    "password", "passcode", "otp", "pin", "token", "secret", "login", "credential",
    "account", "balance", "iban", "swift", "salary", "payroll", "invoice", "transfer", "statement",
    "card", "cvv", "expiry", "diagnosis", "patient", "prescription", "medical", "lab", "doctor",
    "nic", "passport", "ssn", "social security", "tax", "dob", "birth", "address", "confidential",
    "private", "ticket", "order", "tracking", "tutorial", "example", "sample", "placeholder", "test",
)


def _doc_signals(text: str, n_candidates: int) -> tuple[str, ...]:
    low = text.lower()
    sig = [f"len{_bucket(len(text), (60, 150, 400, 1000))}", f"n{_bucket(n_candidates, (2, 4, 7))}"]
    sig += [f"kw:{k}" for k in _DOC_KEYWORDS if k in low]
    return tuple(sig)
