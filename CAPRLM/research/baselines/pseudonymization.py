"""pseudonymization: consistent per-entity surrogates within a session (same email -> same
surrogate), reversible only through the SessionKeyStore held by this filter instance."""

from __future__ import annotations

from research.detection import Candidate, Detector

from ._common import DetectingBaseline
from .key_store import SessionKeyStore


class PseudonymizationFilter(DetectingBaseline):
    version = "pseudonymization/0.1"
    action = "tokenize"

    def __init__(self, detector: Detector, key_store: SessionKeyStore | None = None):
        super().__init__(detector)
        # `is None`, not `or`: an empty SessionKeyStore is falsy (it defines __len__).
        self.key_store = SessionKeyStore() if key_store is None else key_store

    def replacement(self, cand: Candidate, text: str) -> str:
        return self.key_store.surrogate(cand.category, cand.surface(text))
