"""Loader for the LLM-generated JSONL files in `CAPRLM/CAPRLM Data/` (see the dataset prompt).

The files are messy, so loading is tolerant but every row is validated, and what was
dropped is reported — never silently. Handles: several JSON objects on one line, blank
lines, invalid backslash escapes (`\\@`), and rows whose `surface` is not in `text`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from shared.types import PrivacyContext, Source

from .placeholder_scenarios import GoldSpan, Scenario

TIERS = {"none", "low", "medium", "high"}
DATA_DIR = Path(__file__).resolve().parents[2] / "CAPRLM Data"

_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')
_DECODER = json.JSONDecoder()


@dataclass
class LoadReport:
    files: int = 0
    rows_ok: int = 0
    dropped: Counter = field(default_factory=Counter)

    def summary(self) -> dict:
        return {"files": self.files, "rows_kept": self.rows_ok, "dropped": dict(self.dropped)}


def _objects(raw: str, report: LoadReport):
    """Yield JSON objects from text that may hold several per line or have bad escapes."""
    raw = _BAD_ESCAPE.sub("", raw)
    i, n = 0, len(raw)
    while i < n:
        while i < n and raw[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        try:
            obj, i = _DECODER.raw_decode(raw, i)
        except json.JSONDecodeError:
            report.dropped["unparseable_json"] += 1
            nxt = raw.find('{"id"', i + 1)     # resync at the next row
            if nxt == -1:
                break
            i = nxt
            continue
        yield obj


def _to_scenario(d: dict, report: LoadReport) -> Scenario | None:
    text = d.get("text")
    if not isinstance(text, str) or not text.strip() or not d.get("app"):
        report.dropped["missing_text_or_app"] += 1
        return None
    spans = d.get("spans")
    if not isinstance(spans, list):
        report.dropped["bad_spans"] += 1
        return None
    gold: list[GoldSpan] = []
    for s in spans:
        surface, cat, tier = s.get("surface"), s.get("category"), s.get("tier")
        if tier not in TIERS or not cat or not isinstance(surface, str):
            report.dropped["bad_span_fields"] += 1
            return None
        if surface not in text:
            report.dropped["surface_not_in_text"] += 1
            return None
        gold.append(GoldSpan(surface, cat, tier))
    source = Source.OCR if d.get("source") == "ocr" else Source.UIA
    ctx = PrivacyContext(app=str(d["app"]), domain=d.get("domain"), url=None,
                         title=d.get("title"), source=source)
    return Scenario(text, ctx, str(d.get("context_kind", "unknown")), tuple(gold))


def load_dir(path: Path = DATA_DIR) -> tuple[list[Scenario], LoadReport]:
    report = LoadReport()
    seen: set[str] = set()
    out: list[Scenario] = []
    for f in sorted(p for p in path.iterdir() if p.is_file()):
        report.files += 1
        for obj in _objects(f.read_text(encoding="utf-8"), report):
            sc = _to_scenario(obj, report) if isinstance(obj, dict) else None
            if sc is None:
                continue
            if sc.text in seen:
                report.dropped["duplicate_text"] += 1
                continue
            seen.add(sc.text)
            out.append(sc)
    report.rows_ok = len(out)
    return out, report


def split(scenarios: list[Scenario], test_fraction: float = 0.2, seed: int = 7):
    """Deterministic row split. NOTE: rows from one generator batch share style, so this
    is optimistic versus a split by generator model (BUILD_LOG.md)."""
    import random
    idx = list(range(len(scenarios)))
    random.Random(seed).shuffle(idx)
    k = int(len(idx) * test_fraction)
    test = [scenarios[i] for i in idx[:k]]
    train = [scenarios[i] for i in idx[k:]]
    return train, test
