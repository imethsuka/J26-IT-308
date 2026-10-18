"""Stage 7: one plain sentence for each important memory, written by a small language model that runs on this PC.

What it reads: the memory entries the screen-memory stage flagged ``needs_summary`` (high-importance episodes whose page text
was kept). That text was already put through the privacy filter before it was stored (``adaptation.privacy``), so the model
only ever sees filtered text. It is decrypted here, in memory, for the length of one prompt, and is not written anywhere.

What it writes: ``memory_entries.summary`` and ``summary_model``, nothing else. The entry's own facts (app, site, title,
times) stay as they were, so a wrong or odd sentence can always be checked against them, and the dashboard falls back to a
sentence built from those facts when there is no summary.

What it skips, writing no sentence (the entry keeps the one built from its facts): pages with less than ``min_words`` real
words after cleaning. Glances never get here: they are not entries, or are hidden (``adaptation/memory_tidy.py``). Measured on the author's memories
(2026-10-24): with almost no text the model invented content (an empty picture-in-picture window became "a book about the
history of photography"), and on 2-second glances it mostly repeated the visit count.

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

from features.titles import display_title

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


class ModelPaused(Exception):
    """The model stopped on purpose (the user came back, the PC went on battery): what is left waits for the next run."""


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
    min_words: int = 40              # fewer real words than this (after cleaning) is too little to describe: no model call


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
    """(system, user). The user message names the page, then gives its text. App, time, duration and the visit number are left
    out on purpose: the model repeated them instead of describing the content ("You visited page 32", from "visit number 32"),
    and the page already shows them."""
    head = []
    title = display_title(entry.get("title"))
    if title:
        head.append(f"Page title: {title}")
    if entry.get("domain"):
        head.append(f"Site: {entry['domain']}")
    return SYSTEM_PROMPT, "\n".join(head) + ("\n\n" if head else "") + "Page text:\n" + page_text


_MARKDOWN = re.compile(r"`+|\*+|^\s*#{1,6}\s+|^>\s*", re.MULTILINE)
_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)
_STARTS_RIGHT = re.compile(r"^You\s+[a-z]+\b(?!:)")  # "You read...", not "You: ..." or "You said: <copied text>"
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|'[^']{12,}'")


def strip_markdown(raw: str) -> str:
    """Markdown emphasis, code marks, headings and quotes go. A ``#`` inside a word stays ("C#", "F#", "#3"), and so do
    underscores, which placeholders such as [PERSON_1] contain."""
    return _MARKDOWN.sub("", _THINKING.sub("", raw or ""))


def clean_summary(raw: str, *, max_chars: int = 400, page_text: str | None = None) -> str | None:
    """Tidy the model's answer. None means unusable: empty, too short, a refusal, not a sentence about "You", or mostly copied
    from the page (the small model sometimes pasted the page's first line back)."""
    text = re.sub(r"\s+", " ", strip_markdown(raw)).strip().strip("\"'“”")
    if len(text) < 15 or _REFUSAL.search(text) or not _STARTS_RIGHT.match(text):
        return None
    if page_text and copied_from(text, page_text):
        return None
    sentences = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(sentences[:2]).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text


def copied_from(summary: str, page_text: str, *, run: int = 10) -> bool:
    """True when ``run`` words in a row of the summary appear verbatim in the page: a copy, not a description. Quoted text
    (a video or document title) does not count: naming the page is fine."""
    words = re.findall(r"\w+", _QUOTED.sub(" ", summary).lower())
    if len(words) < run:
        return False
    page = " " + " ".join(re.findall(r"\w+", page_text.lower())) + " "
    return any(" " + " ".join(words[i:i + run]) + " " in page for i in range(1, len(words) - run + 1))


def real_words(text: str) -> int:
    return len(re.findall(r"[^\W\d_]{2,}", text))


def run_summaries(job: SummarizeJob) -> dict[str, Any]:
    """Summarise what is waiting. Returns a JSON-able report for the run summary."""
    cfg = job.config
    report: dict[str, Any] = {"waiting": 0, "summarized": 0, "discarded": 0, "failed": 0, "too_little_text": 0,
                              "model": None}
    pending = job.memory.pending_summaries(limit=cfg.max_entries)
    report["waiting"] = len(pending)
    if not pending:
        return report

    llm: LanguageModel | None = None   # started only when an entry actually needs it
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
        if real_words(page_text) < cfg.min_words:
            job.memory.set_summary(item["id"], None, None)       # too little to describe: a guess would be invented
            report["too_little_text"] += 1
            continue
        system, user = build_prompt(item["entry"], select_text(page_text, cfg.max_text_chars))
        if llm is None:
            llm = job.model_factory()
            report["model"] = llm.name
        try:
            raw = llm.complete(system, user, max_tokens=cfg.max_tokens)
        except ModelPaused as why:
            logger.info("summaries paused (%s); %d entries stay waiting", why, len(pending) - done)
            report["paused"] = str(why)
            break
        except Exception as exc:     # the model broke: stop here and leave the rest waiting for the next run
            logger.warning("summary model failed (%s); %d entries stay waiting", type(exc).__name__, len(pending))
            report["failed"] += 1
            report["error"] = type(exc).__name__
            break
        summary = clean_summary(raw, max_chars=cfg.max_summary_chars, page_text=page_text)
        job.memory.set_summary(item["id"], summary, llm.name)
        report["summarized" if summary else "discarded"] += 1
    if job.progress is not None:
        job.progress(len(pending), len(pending))
    if llm is not None and getattr(llm, "where", None):     # set once the model has started (adaptation/llm_runtime.LocalModel)
        report["device"] = llm.where
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
