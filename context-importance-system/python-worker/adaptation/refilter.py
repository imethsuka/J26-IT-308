"""One-time clean-up: put the page text that was stored without a real privacy filter through the CAPRLM filter.

Before the filter was wired in, screen text could be kept by the unredacted developer option (``filter_version`` starting with
``passthrough``). Those pages must not be read by the summary model, and they should not sit in ``memory.db`` unfiltered.
For each such page this re-runs every stored fragment through the filter and replaces the page with the filtered result.

Fail-safe, per page: if the filter raises for any fragment the page is left exactly as it was (never half-converted, never
dropped by accident); if the filter blocks a fragment the whole page's text is removed and its entries stop claiming to hold text.
A backup copy is the caller's job (``refilter_memory.py`` makes one before ``--apply``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from features.text_merge import TextDocument
from storage.memory_store import MemoryStore

from .privacy import PrivacyContext, PrivacyFilter

logger = logging.getLogger(__name__)


@dataclass
class RefilterReport:
    pages: int = 0
    converted: int = 0
    dropped: int = 0          # the filter blocked something: the page's text was removed
    failed: int = 0           # the filter raised: the page was left untouched
    words_before: int = 0
    words_after: int = 0
    versions: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["versions"] = sorted(self.versions)
        return d


def refilter_memory(memory: MemoryStore, privacy_filter: PrivacyFilter, *, apply: bool, limit: int | None = None) -> RefilterReport:
    report = RefilterReport()
    pages = memory.documents_to_refilter()
    if limit is not None:
        pages = pages[:limit]
    for page in pages:
        report.pages += 1
        stored = memory.load_document(page["doc_id"])
        if stored is None or not stored.document.fragments:
            continue
        ctx = PrivacyContext(app=page["app"], domain=page["domain"], title=page["title"], source="uia")
        report.words_before += stored.document.word_count
        new_doc = TextDocument()
        blocked = False
        version = ""
        try:
            for fragment in stored.document.fragments:
                result = privacy_filter.filter(fragment.text(), ctx)
                if not isinstance(result.text, str):
                    raise TypeError("filter returned non-text")
                if result.blocked:
                    blocked = True
                    break
                version = result.filter_version or privacy_filter.version
                if result.text.strip():
                    new_doc.merge(result.text)
        except Exception as exc:        # filter or service failed: leave this page exactly as it was
            logger.warning("refilter: page left unchanged (%s)", type(exc).__name__)
            report.failed += 1
            report.words_before -= stored.document.word_count
            continue

        if blocked or new_doc.word_count == 0:
            report.dropped += 1
            if apply:
                memory.replace_document_text(page["doc_id"], None, None)
            continue
        report.converted += 1
        report.words_after += new_doc.word_count
        report.versions.add(version)
        if apply:
            memory.replace_document_text(page["doc_id"], new_doc, version)
    return report
