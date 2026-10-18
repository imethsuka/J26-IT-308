"""Stage 8: a title, a tag and a short TLDR for each work session, and a recap for each day.

Input is what is already in ``memory.db``: the entries' own facts (app, site, page title, time spent), the sentences stage 7
wrote, and, for the pages the most time went to that have no sentence, a short cleaned excerpt of the stored page. That page
text went through the privacy filter before it was stored, exactly like the text stage 7 reads.

What the notes are for: a person scanning their day wants *what they worked on*, not which windows were open. Measured on
the author's notes (2026-10-24), the earlier prompt produced lists of apps and minutes ("You visited Microsoft Edge for 1
minute, then...") and every day recap was a word-for-word copy of one session note. So pages are ranked by time spent, the
prompt asks for the subject, and a recap that copies a session note is replaced by one built from the session titles.

A *session* is a run of important (high or medium) entries with no gap longer than 30 minutes and no more than 60 minutes
from its first entry to the start of its last. The dashboard groups entries the
same way and finds a session's note by a hash of its entry ids (``session_key``), so the grouping rule and the key must stay
identical to ``UI/ContextDashboard/Views/Memory/SessionBuilder.cs``; ``tests/test_rollups.py`` and the C# tests share one example.

The model only writes words. Time per app, the most important memories and the coverage line are computed from the stored
numbers by the dashboard and are never taken from the model.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from features.titles import display_title
from storage.memory_store import MemoryStore

from .page_text import readable
from .summarizer import LanguageModel, ModelPaused, strip_markdown

logger = logging.getLogger(__name__)

GAP = timedelta(minutes=30)
# Bump when the note prompts or rules change: notes written under an older version are rewritten, a few per run.
# 2 (2026-10-24): pages ranked by time with excerpts, subject-not-apps prompt, recaps that copy a session are replaced.
NOTES_VERSION = 2
MAX_SPAN = timedelta(minutes=60)      # a session is also cut when it has run this long since its first entry

SESSION_SYSTEM = (
    "You summarise one stretch of computer work from notes about the pages a person spent time on, longest first. "
    "Say what they were working on, reading, learning or watching: the subject and the goal, not the apps, windows or "
    "minutes (those are shown elsewhere). If they did two or three separate things, name each one. "
    "Reply in exactly this format, with nothing else:\n"
    "TITLE: three to seven words naming the work\n"
    "TAG: one lower-case word for the topic\n"
    "TLDR: two plain sentences in the past tense, starting with \"You\" (always write the word TLDR and a colon first)\n"
    "Use only the notes. Do not invent names, numbers or details. "
    "Words like [PERSON_1] or [AMOUNT_1] stand for hidden private details: never guess them."
)

DAY_SYSTEM = (
    "You recap one day from a list of work sessions, each with a title, the time spent and a short summary. "
    "Write about \"you\", never \"I\". Write three short plain sentences in the past tense, starting with \"You\", that "
    "combine the sessions into the main things done that day, the ones with the most time first. Do not copy any one "
    "session's summary. "
    "Use only the sessions given. Do not invent anything. Words like [PERSON_1] stand for hidden details: never guess them. "
    "No lists, no headings."
)


@dataclass
class Item:
    id: int
    start: datetime
    end: datetime
    app: str
    domain: str
    title: str
    tier: str
    importance: float | None
    seconds: float
    visit: int
    sentence: str            # the model's summary when there is one, else built from the facts
    doc_id: str = ""
    excerpt: str = ""        # a short cleaned piece of the page, for a top page that has no model sentence


@dataclass
class Session:
    key: str
    items: list[Item] = field(default_factory=list)

    @property
    def first(self) -> datetime:
        return self.items[0].start

    @property
    def last(self) -> datetime:
        return max(i.end for i in self.items)

    def day(self, tz=None) -> str:
        return self.first.astimezone(tz).strftime("%Y-%m-%d")


def session_key(entry_ids: list[int]) -> str:
    """Same function in C#: SHA-1 of the ids sorted ascending and joined by commas, first 16 hex characters."""
    return hashlib.sha1(",".join(str(i) for i in sorted(entry_ids)).encode("ascii")).hexdigest()[:16]


def plain_sentence(app: str, domain: str, title: str, seconds: float, visit: int) -> str:
    """The sentence the dashboard shows for an entry with no summary. Mirrors ``MemoryRow.Headline`` in C#."""
    what = f"“{display_title(title)}”" if display_title(title) else "a page"
    where = app if not domain or domain == "-" else f"{app} ({domain})"
    spent = "under a minute" if seconds < 60 else f"{round(seconds / 60)} min"
    return f"Spent {spent} on {what} in {where}" + (f", visit {visit}" if visit > 1 else "") + "."


def _parse_time(text: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(text or "")
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def to_items(rows: list[dict[str, Any]]) -> list[Item]:
    items = []
    for r in rows:
        e = r["entry"]
        start, end = _parse_time(e.get("start")), _parse_time(e.get("end"))
        if start is None:
            continue
        end = end or start
        seconds = float(e.get("active_seconds") or 0)
        visit = int(e.get("visit_number") or 1)
        app, domain, title = e.get("app") or "", e.get("domain") or "", e.get("title") or ""
        summary = (r.get("summary") or "").strip()
        items.append(Item(r["id"], start, end, app, domain, title, r["tier"], r["importance"], seconds, visit,
                          summary or plain_sentence(app, domain, title, seconds, visit), doc_id=e.get("doc_id") or ""))
    return sorted(items, key=lambda i: (i.start, i.id))


def group_sessions(items: list[Item], gap: timedelta = GAP, max_span: timedelta = MAX_SPAN) -> list[Session]:
    """A new session starts when an entry begins more than ``gap`` after the latest end seen in the current one, or more than
    ``max_span`` after the current session's first entry (otherwise a busy day becomes one session of hundreds of entries)."""
    sessions: list[Session] = []
    current: list[Item] = []
    latest_end: datetime | None = None
    for item in items:
        if current and latest_end is not None and (item.start - latest_end > gap or item.start - current[0].start > max_span):
            sessions.append(Session(session_key([i.id for i in current]), current))
            current = []
        latest_end = item.end if not current or latest_end is None else max(latest_end, item.end)
        current.append(item)
    if current:
        sessions.append(Session(session_key([i.id for i in current]), current))
    return sessions


def pages_by_time(items: list[Item]) -> list[list[Item]]:
    """The session's pages (same app and title), the one with the most time first. Repeated visits are one page: sixty
    near-identical lines gave the model nothing to summarise."""
    pages: dict[tuple[str, str], list[Item]] = {}
    for i in items:
        pages.setdefault((i.app, display_title(i.title)), []).append(i)
    return sorted(pages.values(), key=lambda g: (sum(i.seconds for i in g), sum(i.importance or 0 for i in g)), reverse=True)


def _lines(items: list[Item], tz, limit: int = 12) -> str:
    """One line per page, longest first: its title, the model's sentence when it has one (else a short excerpt of the page),
    and the time spent."""
    lines = []
    for g in pages_by_time(items)[:limit]:
        written = [i.sentence for i in g if i.sentence and not i.sentence.startswith("Spent ")]
        excerpt = next((i.excerpt for i in g if i.excerpt), "")
        title = display_title(g[0].title) or "untitled"
        minutes = round(sum(i.seconds for i in g) / 60)
        spent = f"{minutes} min" if minutes >= 1 else "under a minute"
        detail = written[-1] if written else (f"page text: {excerpt}" if excerpt else "")
        lines.append(f"- “{title}” in {g[0].app} ({spent})" + (f": {detail}" if detail else ""))
    return "\n".join(lines)


EXCERPT_CHARS = 300


def add_excerpts(sessions: list[Session], memory: MemoryStore, *, per_session: int = 4) -> None:
    """Give the longest pages without a model sentence a short excerpt of their stored (filtered) text, so the note can say
    what they were about. At most ``per_session`` pages per session, each page read once."""
    cache: dict[str, str] = {}
    for session in sessions:
        for g in pages_by_time(session.items)[:per_session]:
            if any(i.sentence and not i.sentence.startswith("Spent ") for i in g):
                continue
            doc_id = next((i.doc_id for i in g if i.doc_id), "")
            if not doc_id:
                continue
            if doc_id not in cache:
                stored = memory.load_document(doc_id)
                text = readable(stored.document.text()) if stored is not None else ""
                cache[doc_id] = " ".join(text.split())[:EXCERPT_CHARS]
            for i in g:
                i.excerpt = cache[doc_id]


def session_prompt(session: Session, tz=None) -> tuple[str, str]:
    first, last = session.first.astimezone(tz), session.last.astimezone(tz)
    return SESSION_SYSTEM, f"Session {first:%A %d %B}, {first:%H:%M} to {last:%H:%M}.\nNotes:\n{_lines(session.items, tz)}"


def day_prompt(day: str, notes: list[dict[str, str]]) -> tuple[str, str]:
    ranked = sorted(notes, key=lambda n: float(n.get("minutes") or 0), reverse=True)
    body = "\n".join(f"- {n['title']} ({_minutes(n.get('minutes'))}): {n['tldr']}" for n in ranked)
    return DAY_SYSTEM, f"Day {day}. Sessions, most time first:\n{body}"


def _minutes(value: Any) -> str:
    m = round(float(value or 0))
    return "under a minute" if m < 1 else f"{m} min" if m < 90 else f"{m / 60:.1f} h"


def copies_a_note(recap: str, notes: list[dict[str, str]], *, run: int = 8) -> bool:
    """True when ``run`` words in a row of the recap come from one session's TLDR: the model copied instead of combining."""
    words = re.findall(r"\w+", recap.lower())
    for n in notes:
        tldr = " " + " ".join(re.findall(r"\w+", (n.get("tldr") or "").lower())) + " "
        if any(" " + " ".join(words[i:i + run]) + " " in tldr for i in range(0, max(0, len(words) - run + 1))):
            return True
    return False


def plain_day_recap(notes: list[dict[str, str]]) -> str | None:
    """A recap built from the facts: the sessions with the most time, by title. Used when the model's recap is unusable."""
    ranked = [n for n in sorted(notes, key=lambda n: float(n.get("minutes") or 0), reverse=True) if n.get("title")][:3]
    if not ranked:
        return None
    parts = [f"{n['title']} ({_minutes(n.get('minutes'))})" for n in ranked]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Most of your time went to {joined}."


_FIELD = re.compile(r"^\s*(TITLE|TAG|TLDR)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_session_note(raw: str) -> tuple[str, str, str] | None:
    """(title, tag, tldr), or None if there is no usable title and text. The model sometimes leaves out the ``TLDR:`` label
    and just writes the sentences after the tag line; that is accepted."""
    text = strip_markdown(raw)
    found = {m.group(1).upper(): m.group(2) for m in _FIELD.finditer(text)}
    title = (found.get("TITLE") or "").strip(" .\"'“”")
    tag = re.sub(r"[^a-z0-9-]", "", (found.get("TAG") or "").strip().lower().split(" ")[0]) if found.get("TAG") else ""
    body = found.get("TLDR")
    if body is None:                                    # no label: everything that is not a TITLE / TAG line
        body = " ".join(line.strip() for line in text.splitlines()
                        if line.strip() and not re.match(r"\s*(TITLE|TAG)\s*:", line, re.IGNORECASE))
    tldr = clean_text(body, sentences=3, max_chars=500)
    if tldr is None:
        return None
    # A title that is only a generic label, or the header line copied back, says nothing: leave it empty and the dashboard names
    # the session after its most important page instead.
    if len(title) < 3 or _GENERIC_TITLE.match(title) or re.search(r"\d{1,2}:\d{2}", title):
        title = ""
    return title[:80], tag[:24], tldr


_GENERIC_TITLE = re.compile(
    r"^\s*(session|work|working|research|code|coding|browsing|general|computer|desktop|activity)( session| work| activity| time)?\s*$"
    r"|^\s*session\b", re.IGNORECASE)
_FIRST_PERSON = re.compile(r"\b(I|me|my|myself|I'm|I've)\b")


def clean_text(raw: str, *, sentences: int, max_chars: int) -> str | None:
    text = re.sub(r"\s+", " ", strip_markdown(raw)).strip().strip("\"'“”")
    if len(text) < 15 or re.search(r"\b(as an ai|i cannot|i can't|i'm sorry|unable to)\b", text, re.IGNORECASE):
        return None
    if _FIRST_PERSON.search(text):          # the notes are about "you"; a model that writes "I spent..." has lost the thread
        return None
    text = " ".join(re.split(r"(?<=[.!?])\s+", text)[:sentences]).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text


@dataclass
class RollupConfig:
    days: int = 14                   # how far back sessions are looked at
    max_sessions: int = 12           # new session notes per run
    gap: timedelta = GAP


@dataclass
class RollupJob:
    memory: MemoryStore
    model_factory: Callable[[], LanguageModel]
    config: RollupConfig = field(default_factory=RollupConfig)


def run_rollups(job: RollupJob, *, now: datetime | None = None, tz=None) -> dict[str, Any]:
    cfg, memory = job.config, job.memory
    now = now or datetime.now(timezone.utc)
    report: dict[str, Any] = {"sessions_seen": 0, "session_notes": 0, "day_notes": 0, "unusable": 0, "failed": 0, "model": None}

    since = now - timedelta(days=cfg.days)
    sessions = group_sessions(to_items(memory.rollup_entries(since_us=int(since.timestamp() * 1_000_000))), cfg.gap)
    report["sessions_seen"] = len(sessions)
    closed = [s for s in sessions if now - s.last > cfg.gap]            # an open session is still growing: wait
    have = memory.session_note_keys()
    current = memory.session_note_keys(min_version=NOTES_VERSION)
    todo = [s for s in closed if s.key not in current]
    todo = ([s for s in todo if s.key not in have] + [s for s in todo if s.key in have])[-cfg.max_sessions:]  # new ones first

    days: dict[str, list[Session]] = {}
    for s in closed:
        days.setdefault(s.day(tz), []).append(s)
    day_jobs = {d: ss for d, ss in days.items()
                if memory.day_note_key(d, min_version=NOTES_VERSION) != session_key_of_sessions(ss)
                and all(x.key in current or x in todo for x in ss)}
    if not todo and not day_jobs:
        memory.prune_notes(before_us=int((now - timedelta(days=90)).timestamp() * 1_000_000))
        return report

    llm = job.model_factory()
    report["model"] = llm.name
    add_excerpts(todo, memory)
    try:
        for s in todo:
            system, user = session_prompt(s, tz)
            parsed = parse_session_note(llm.complete(system, user, max_tokens=160))
            title, tag, tldr = parsed if parsed else (None, None, None)
            memory.save_session_note(
                key=s.key, day=s.day(tz), first_ts=int(s.first.timestamp() * 1_000_000), last_ts=int(s.last.timestamp() * 1_000_000),
                n_entries=len(s.items), title=title or None, tag=tag or None, tldr=tldr, model=llm.name if parsed else None,
                version=NOTES_VERSION)
            report["session_notes" if parsed else "unusable"] += 1
    except ModelPaused as why:     # stopped on purpose: keep what was written, the rest waits for the next run
        report["paused"] = str(why)
        return report
    except Exception as exc:       # the model broke: keep what was written, retry the rest next run
        logger.warning("rollup model failed (%s)", type(exc).__name__)
        report["failed"] += 1
        report["error"] = type(exc).__name__
        return report

    # day recaps, from the notes the sessions now have
    try:
        for d, ss in sorted(day_jobs.items()):
            notes = memory.get_session_notes([x.key for x in ss])
            usable = [{"time": f"{x.first.astimezone(tz):%H:%M}", "title": notes[x.key]["title"] or _best_title(x),
                       "tldr": notes[x.key]["tldr"], "minutes": sum(i.seconds for i in x.items) / 60}
                      for x in ss if x.key in notes and notes[x.key]["tldr"]]
            if not usable:
                continue
            system, user = day_prompt(d, usable)
            tldr = clean_text(llm.complete(system, user, max_tokens=200), sentences=3, max_chars=600)
            model = llm.name
            if tldr is None or (len(usable) > 1 and copies_a_note(tldr, usable)):
                tldr, model = plain_day_recap(usable), None       # built from the facts, so not labelled as the model's
                report["day_notes_plain"] = report.get("day_notes_plain", 0) + 1
            memory.save_day_note(day=d, sessions_key=session_key_of_sessions(ss), tldr=tldr, model=model if tldr else None,
                                 version=NOTES_VERSION)
            report["day_notes" if tldr else "unusable"] += 1
    except ModelPaused as why:
        report["paused"] = str(why)
    except Exception as exc:
        logger.warning("day recap model failed (%s)", type(exc).__name__)
        report["failed"] += 1
        report["error"] = type(exc).__name__
    memory.prune_notes(before_us=int((now - timedelta(days=90)).timestamp() * 1_000_000))
    return report


def session_key_of_sessions(sessions: list[Session]) -> str:
    return hashlib.sha1(",".join(s.key for s in sessions).encode("ascii")).hexdigest()[:16]


def _best_title(session: Session) -> str:
    """The title of the page the most time went to: what stands in when the model gave the session no usable title."""
    best = pages_by_time(session.items)[0][0]
    return display_title(best.title) or f"{best.app} session"
