"""Stage 8: a title, a tag and a short TLDR for each work session, and a recap for each day.

Input is only what is already in ``memory.db``: the entries' own facts (app, site, page title, time spent) and the sentences
stage 7 wrote (or, where there is none yet, a sentence built from the facts alone). No page text is read here, so this stage
never touches anything the privacy filter has not already cleaned.

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

from storage.memory_store import MemoryStore

from .summarizer import LanguageModel

logger = logging.getLogger(__name__)

GAP = timedelta(minutes=30)
MAX_SPAN = timedelta(minutes=60)      # a session is also cut when it has run this long since its first entry

SESSION_SYSTEM = (
    "You summarise one work session from a list of short notes about what a person did on their computer. "
    "Reply in exactly this format, with nothing else:\n"
    "TITLE: three to seven words naming the work\n"
    "TAG: one lower-case word for the topic\n"
    "TLDR: two plain sentences in the past tense, starting with \"You\" (always write the word TLDR and a colon first)\n"
    "Use only the notes. Do not invent names, numbers or details. "
    "Words like [PERSON_1] or [AMOUNT_1] stand for hidden private details: never guess them."
)

DAY_SYSTEM = (
    "You recap one day from a list of work sessions, each with a title and a short summary. Write about \"you\", never \"I\". "
    "Write three short plain sentences in the past tense, starting with \"You\", covering the main things done that day. "
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
    what = f"“{title.strip()}”" if title and title.strip() else "a page"
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
                          summary or plain_sentence(app, domain, title, seconds, visit)))
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


def _lines(items: list[Item], tz, limit: int = 14) -> str:
    """One line per page (app + title), most important first, then back in time order. Repeated visits are counted, not repeated:
    sixty near-identical lines gave the model nothing to summarise. A model sentence is used where the page has one."""
    pages: dict[tuple[str, str], list[Item]] = {}
    for i in items:
        pages.setdefault((i.app, i.title.strip()), []).append(i)
    ranked = sorted(pages.values(), key=lambda g: sum(i.importance or 0 for i in g), reverse=True)[:limit]
    ranked.sort(key=lambda g: g[0].start)
    lines = []
    for g in ranked:
        written = [i.sentence for i in g if i.sentence and not i.sentence.startswith("Spent ")]
        minutes = round(sum(i.seconds for i in g) / 60)
        if written:
            text = written[-1]
        else:
            what = f"“{g[0].title.strip()}”" if g[0].title.strip() else "a page"
            text = f"{what} in {g[0].app}"
        visits = f"{len(g)} visit{'' if len(g) == 1 else 's'}"
        spent = f"{minutes} min" if minutes >= 1 else "under a minute"
        extra = f" ({visits}, {spent})"
        lines.append(f"- {g[0].start.astimezone(tz):%H:%M} {text}{extra}")
    return "\n".join(lines)


def session_prompt(session: Session, tz=None) -> tuple[str, str]:
    first, last = session.first.astimezone(tz), session.last.astimezone(tz)
    return SESSION_SYSTEM, f"Session {first:%A %d %B}, {first:%H:%M} to {last:%H:%M}.\nNotes:\n{_lines(session.items, tz)}"


def day_prompt(day: str, notes: list[dict[str, str]]) -> tuple[str, str]:
    body = "\n".join(f"- {n['time']} {n['title']}: {n['tldr']}" for n in notes)
    return DAY_SYSTEM, f"Day {day}. Sessions:\n{body}"


_FIELD = re.compile(r"^\s*(TITLE|TAG|TLDR)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_session_note(raw: str) -> tuple[str, str, str] | None:
    """(title, tag, tldr), or None if there is no usable title and text. The model sometimes leaves out the ``TLDR:`` label
    and just writes the sentences after the tag line; that is accepted."""
    text = re.sub(r"[`*#]+", "", raw or "")
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
    text = re.sub(r"\s+", " ", re.sub(r"[`*#]+", "", raw or "")).strip().strip("\"'“”")
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
    todo = [s for s in closed if s.key not in have][-cfg.max_sessions:]

    days: dict[str, list[Session]] = {}
    for s in closed:
        days.setdefault(s.day(tz), []).append(s)
    day_jobs = {d: ss for d, ss in days.items()
                if memory.day_note_key(d) != session_key_of_sessions(ss) and all(x.key in have or x in todo for x in ss)}
    if not todo and not day_jobs:
        memory.prune_notes(before_us=int((now - timedelta(days=90)).timestamp() * 1_000_000))
        return report

    llm = job.model_factory()
    report["model"] = llm.name
    try:
        for s in todo:
            system, user = session_prompt(s, tz)
            parsed = parse_session_note(llm.complete(system, user, max_tokens=160))
            title, tag, tldr = parsed if parsed else (None, None, None)
            memory.save_session_note(
                key=s.key, day=s.day(tz), first_ts=int(s.first.timestamp() * 1_000_000), last_ts=int(s.last.timestamp() * 1_000_000),
                n_entries=len(s.items), title=title or None, tag=tag or None, tldr=tldr, model=llm.name if parsed else None)
            report["session_notes" if parsed else "unusable"] += 1
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
                       "tldr": notes[x.key]["tldr"]}
                      for x in ss if x.key in notes and notes[x.key]["tldr"]]
            if not usable:
                continue
            system, user = day_prompt(d, usable)
            tldr = clean_text(llm.complete(system, user, max_tokens=200), sentences=3, max_chars=600)
            memory.save_day_note(day=d, sessions_key=session_key_of_sessions(ss), tldr=tldr, model=llm.name if tldr else None)
            report["day_notes" if tldr else "unusable"] += 1
    except Exception as exc:
        logger.warning("day recap model failed (%s)", type(exc).__name__)
        report["failed"] += 1
        report["error"] = type(exc).__name__
    memory.prune_notes(before_us=int((now - timedelta(days=90)).timestamp() * 1_000_000))
    return report


def session_key_of_sessions(sessions: list[Session]) -> str:
    return hashlib.sha1(",".join(s.key for s in sessions).encode("ascii")).hexdigest()[:16]


def _best_title(session: Session) -> str:
    """The most important page's title: what the dashboard shows when the model gave the session no usable title."""
    best = max(session.items, key=lambda i: i.importance or 0)
    return best.title.strip() or f"{best.app} session"
