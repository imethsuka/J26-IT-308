"""A detected span, before any risk decision. This is the detection stage's output and the
risk model's input — never CAPRLM's output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    """Offsets into the text that was scanned; the span's content is not stored here so a
    Candidate can be logged without carrying the sensitive string itself."""
    start: int
    end: int
    category: str       # "EMAIL", "PERSON", "CREDENTIAL", ...
    confidence: float   # 0..1; see each detector for how it is derived (NER's is a constant)
    detector: str       # "regex" | "ner"
    # Lower-cased words just left/right of the span (digits normalised), set by Detector.
    # Surrounding text is context too: "Password: x" vs "Order: x". Never the span itself.
    window: tuple[str, ...] = ()
    # Coarse shape of the span (length / digit / case buckets) and document-level signals
    # (length, number of candidates, keyword presence). Buckets and flags only — no content.
    shape: tuple[str, ...] = ()
    doc: tuple[str, ...] = ()

    def surface(self, text: str) -> str:
        return text[self.start:self.end]

    def overlaps(self, other: "Candidate") -> bool:
        return self.start < other.end and other.start < self.end
