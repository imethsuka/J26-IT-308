"""Protection stage helpers shared by CAPRLM and the baselines: apply per-span replacements."""

from __future__ import annotations


def apply_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    """Replace [start, end) spans, working right-to-left so earlier offsets stay valid.
    Spans must not overlap (the detector guarantees that)."""
    out = text
    for start, end, repl in sorted(replacements, key=lambda r: r[0], reverse=True):
        out = out[:start] + repl + out[end:]
    return out
