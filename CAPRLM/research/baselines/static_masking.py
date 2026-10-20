"""static_masking: a fixed category -> mask-string table. No risk tiers, no context."""

from __future__ import annotations

from research.detection import Candidate

from ._common import DetectingBaseline

MASKS = {
    "EMAIL": "****@****",
    "PHONE": "***-***-****",
    "CARD_LIKE_NUMBER": "**** **** **** ****",
    "CREDENTIAL": "********",
}
DEFAULT_MASK = "****"


class StaticMaskingFilter(DetectingBaseline):
    version = "static-masking/0.1"
    action = "mask"

    def replacement(self, cand: Candidate, text: str) -> str:
        return MASKS.get(cand.category, DEFAULT_MASK)
