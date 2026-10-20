"""anonymization: category generalisation — the span becomes its class label. Not
reversible: nothing is stored, and two different values of a category look identical."""

from __future__ import annotations

from research.detection import Candidate

from ._common import DetectingBaseline


class AnonymizationFilter(DetectingBaseline):
    version = "anonymization/0.1"
    action = "mask"

    def replacement(self, cand: Candidate, text: str) -> str:
        return f"[{cand.category}]"
