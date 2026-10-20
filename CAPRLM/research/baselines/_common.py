"""Shared scaffolding for the three detection-sharing baselines.

static_masking, anonymization and pseudonymization all run the same detection stage as
CAPRLM (regex + NER), so against them the only difference is the protection/risk strategy.
`rule_based` (shared/) has its own regex-only detection and is not comparable on detection
alone — see BUILD_LOG.md.

Fail-closed like CAPRLMFilter: any error returns blocked=True, never the input text.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from shared.types import FilterResult, PrivacyContext, RedactionRecord

from research.detection import Candidate, Detector
from research.protection import apply_replacements

log = logging.getLogger(__name__)


class DetectingBaseline(ABC):
    version: str
    action: str   # the single action every span gets (no risk tiers in these baselines)

    def __init__(self, detector: Detector):
        self.detector = detector

    @abstractmethod
    def replacement(self, cand: Candidate, text: str) -> str: ...

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        try:
            cands = self.detector.detect(text)
            repl = [(c.start, c.end, self.replacement(c, text)) for c in cands]
            audit = [RedactionRecord(category=c.category, risk_tier="medium", action=self.action)
                     for c in cands]
            return FilterResult(text=apply_replacements(text, repl), blocked=False,
                                redactions=len(repl), filter_version=self.version, audit=audit)
        except Exception as exc:
            log.error("%s failed (%s); failing closed", self.version, type(exc).__name__)
            return FilterResult(text="", blocked=True, redactions=0, filter_version=self.version)
