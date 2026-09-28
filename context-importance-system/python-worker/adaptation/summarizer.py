"""Stage 7: one plain sentence for each important memory, written by a small language model that runs on this PC.

What it reads: the memory entries the screen-memory stage flagged ``needs_summary`` (high-importance episodes whose page text
was kept). That text was already put through the privacy filter before it was stored (``adaptation.privacy``), so the model
only ever sees filtered text. It is decrypted here, in memory, for the length of one prompt, and is not written anywhere.

What it writes: ``memory_entries.summary`` and ``summary_model``, nothing else. The entry's own facts (app, site, title,
times) stay as they were, so a wrong or odd sentence can always be checked against them, and the dashboard falls back to a
sentence built from those facts when there is no summary.

Fail-safe: a model that cannot load, or a call that raises, leaves the entries waiting for the next run. Output that is empty,
too short, or a refusal is discarded and that entry keeps its plain sentence rather than being retried forever.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from storage.memory_store import MemoryStore

from .page_text import readable, site_line_counts

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You write a short memory note about what a person was reading or working on, so they can recognise it later. "
    "Describe the content of the page text in one or two plain sentences, in the past tense, starting with \"You\". "
    "Name the main topic and one or two concrete points that are in the text. "
    "Do not mention the app, the time of day or how long it took: that is shown elsewhere. "
    "Do not invent anything that is not in the text. "
    "Words like [PERSON_1], [EMAIL_1] or [AMOUNT_1] stand for private details that were hidden: never guess them. "
    "If the text is only a sign-in, error or almost empty page, say that in a few words. "
    "No lists, no quotes, no advice."
)

_REFUSAL = re.compile(r"\b(as an ai|i cannot|i can't|i'm sorry|i am sorry|unable to)\b", re.IGNORECASE)


class LanguageModel(Protocol):
    name: str

    def complete(self, system: str, user: str, *, max_tokens: int) -> str: ...


@dataclass
class SummarizeConfig:
    max_entries: int = 25            # per run, so one run cannot hog the machine
    max_text_chars: int = 6000       # page text given to the model; longer pages keep their start and their end
    max_summary_chars: int = 400
    max_tokens: int = 120
    clean_pages: bool = True         # show the model the page without menus, labels and a site's repeated furniture


@dataclass
class SummarizeJob:
    memory: MemoryStore
    model_factory: Callable[[], LanguageModel]    # called only if something is waiting: loading a model takes seconds and RAM
    config: SummarizeConfig = field(default_factory=SummarizeConfig)
    progress: Callable[[int, int], None] | None = None     # called as (done, total) after each entry, for a progress display


def select_text(text: str, limit: int) -> str:
    """Short pages go in whole. A long page keeps about three quarters from its start and a quarter from its end."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.75)
    return text[:head].rstrip() + "\n[...]\n" + text[-(limit - head):].lstrip()


def build_prompt(entry: dict[str, Any], page_text: str, *, tz=None) -> tuple[str, str]:
    """(system, user). The user message names the page, then gives its text. App, time and duration are left out on
    purpose: the model repeated them instead of describing the content, and the page already shows them."""
    head = []
    if entry.get("title"):
        head.append(f"Page title: {entry['title']}")
    if entry.get("domain"):
        head.append(f"Site: {entry['domain']}")
    if int(entry.get("visit_number") or 1) > 1:
        head.append(f"This was visit number {entry['visit_number']} to this page")
    return SYSTEM_PROMPT, "\n".join(head) + ("\n\n" if head else "") + "Page text:\n" + page_text


def clean_summary(raw: str, *, max_chars: int = 400) -> str | None:
    """Tidy the model's answer. None means unusable (empty, too short, or a refusal)."""
    # Markdown marks go; underscores stay, because placeholders such as [PERSON_1] contain them.
    text = re.sub(r"[`*#]+|^>\s*", "", raw or "", flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip().strip("\"'“”")
    if len(text) < 15 or _REFUSAL.search(text):
        return None
    sentences = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(sentences[:2]).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text


def run_summaries(job: SummarizeJob) -> dict[str, Any]:
    """Summarise what is waiting. Returns a JSON-able report for the run summary."""
    cfg = job.config
    report: dict[str, Any] = {"waiting": 0, "summarized": 0, "discarded": 0, "failed": 0, "model": None}
    pending = job.memory.pending_summaries(limit=cfg.max_entries)
    report["waiting"] = len(pending)
    if not pending:
        return report

    llm = job.model_factory()
    report["model"] = llm.name
    sites: dict[tuple[str, str], Any] = {}        # per site, the lines its pages repeat; computed once per run
    for done, item in enumerate(pending):
        if job.progress is not None:
            job.progress(done, len(pending))
        stored = job.memory.load_document(item["doc_id"])
        page_text = stored.document.text() if stored is not None else ""
        if not page_text.strip():
            job.memory.set_summary(item["id"], None, None)       # nothing to read: keep the plain sentence
            report["discarded"] += 1
            continue
        if cfg.clean_pages:
            e = item["entry"]
            key = (e.get("app") or "", e.get("domain") or "")
            if key not in sites:
                sites[key] = site_line_counts(job.memory.site_pages(key[0], key[1]))
            page_text = readable(page_text, site=sites[key])
        system, user = build_prompt(item["entry"], select_text(page_text, cfg.max_text_chars))
        try:
            raw = llm.complete(system, user, max_tokens=cfg.max_tokens)
        except Exception as exc:     # the model broke: stop here and leave the rest waiting for the next run
            logger.warning("summary model failed (%s); %d entries stay waiting", type(exc).__name__, len(pending))
            report["failed"] += 1
            report["error"] = type(exc).__name__
            break
        summary = clean_summary(raw, max_chars=cfg.max_summary_chars)
        job.memory.set_summary(item["id"], summary, llm.name)
        report["summarized" if summary else "discarded"] += 1
    if job.progress is not None:
        job.progress(len(pending), len(pending))
    return report


class LlamaCppModel:
    """A GGUF model run with llama.cpp on the CPU (``pip install llama-cpp-python``; a ready-made Windows wheel exists)."""

    def __init__(self, path: str | Path, *, n_ctx: int = 4096, n_threads: int | None = None) -> None:
        from llama_cpp import Llama

        self.path = Path(path)
        self.name = self.path.stem
        self._llm = Llama(model_path=str(self.path), n_ctx=n_ctx, n_threads=n_threads, verbose=False)

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        out = self._llm.create_chat_completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=0.2,
        )
        return out["choices"][0]["message"]["content"] or ""
