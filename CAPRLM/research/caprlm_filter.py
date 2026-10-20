"""CAPRLMFilter: detection -> risk prediction -> protection, behind the PrivacyFilter contract.

Fail-closed (PRIVACY_HANDOFF.md): any failure inside filter()/filter_image() — model not
fitted, NER model missing, OCR unavailable — returns blocked=True with empty text instead of
the unfiltered input. It returns rather than raises so a caller that forgets its own
try/except still fails closed.
"""

from __future__ import annotations

import dataclasses
import logging

from shared.types import FilterResult, PrivacyContext, RedactionRecord, Source

from research.detection import Detector
from research.model import RiskModel
from research.protection import apply_replacements

log = logging.getLogger(__name__)

# tier -> protection action. A default (BUILD_LOG.md), not a settled design: the research
# question is the tier prediction; how each tier is then protected is configurable here.
DEFAULT_TIER_ACTIONS = {
    "none": "pass_through",
    "low": "tokenize",     # [EMAIL_1] — keeps "same entity" identity, hides the value
    "medium": "mask",      # [REDACTED] — no identity kept
    "high": "drop",        # removed outright
}


class CAPRLMFilter:
    def __init__(self, model: RiskModel, detector: Detector, tier_actions: dict[str, str] | None = None):
        self.model = model
        self.detector = detector
        self.tier_actions = {**DEFAULT_TIER_ACTIONS, **(tier_actions or {})}

    @property
    def version(self) -> str:
        return self.model.version

    def _blocked(self) -> FilterResult:
        return FilterResult(text="", blocked=True, redactions=0, filter_version=self.version)

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        try:
            return self._filter(text, ctx)
        except Exception as exc:
            log.error("CAPRLM filter failed (%s); failing closed", type(exc).__name__)
            return self._blocked()

    def filter_image(self, image: bytes, ctx: PrivacyContext) -> FilterResult:
        try:
            text = self.detector.ocr_text(image)
            return self._filter(text, dataclasses.replace(ctx, source=Source.OCR))
        except Exception as exc:
            log.error("CAPRLM filter_image failed (%s); failing closed", type(exc).__name__)
            return self._blocked()

    def _filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        cands = self.detector.detect(text)
        tiers = self.model.predict([(c, ctx) for c in cands])

        audit: list[RedactionRecord] = []
        replacements: list[tuple[int, int, str]] = []
        token_ids: dict[tuple[str, str], int] = {}
        for c, tier in zip(cands, tiers):
            action = self.tier_actions[tier]
            audit.append(RedactionRecord(category=c.category, risk_tier=tier, action=action))
            if action == "pass_through":
                continue
            if action == "tokenize":
                # Same value -> same token within the document, numbered per category.
                key = (c.category, c.surface(text))
                if key not in token_ids:
                    token_ids[key] = 1 + sum(1 for k in token_ids if k[0] == c.category)
                repl = f"[{c.category}_{token_ids[key]}]"
            elif action == "mask":
                repl = "[REDACTED]"
            else:  # drop
                repl = ""
            replacements.append((c.start, c.end, repl))

        return FilterResult(
            text=apply_replacements(text, replacements),
            blocked=False,
            redactions=len(replacements),
            filter_version=self.version,
            audit=audit,
        )
