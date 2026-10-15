r"""Compare local language models on the memory-writing job, on the user's own stored pages.

    python -m eval.memory_models --memory-db %LOCALAPPDATA%\context-importance\memory.db `
        --models <folder of .gguf files> [--pages 16] [--sessions 4] --out <scratch folder>\memory_models.json

It works on a *copy* of memory.db (made with SQLite's backup API), so the live file is never written. For each model it runs
the real stage-7 prompt on the same pages (privacy-filtered stored text, cleaned exactly as ``run_summaries`` cleans it) and
the real stage-8 prompt on the same sessions, through ``adaptation/llm_runtime.LocalModel`` (dedicated GPU when usable).

Measured, not judged: seconds per memory, the device it ran on, how many sentences pass ``clean_summary`` (not empty, about
"You", not copied from the page) and how many session notes parse. Whether a sentence is *true* to its page needs a person:
the outputs and a short excerpt of each page go to ``--out`` for that. That file holds page text: keep it on this PC.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _copy_db(src: Path, dest: Path) -> None:
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source, sqlite3.connect(dest) as target:
        source.backup(target)


def pick_pages(memory, db_path: Path, n: int, *, min_words: int = 150, per_site: int = 2) -> list[dict]:
    """Up to ``n`` stored pages with real text, newest first, at most ``per_site`` per app+site so the set is varied.
    Pages whose text was ever stored without a real filter are left out, as the summariser leaves them out."""
    from adaptation.page_text import readable, site_line_counts
    from adaptation.summarizer import real_words

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT d.doc_id, d.app, d.domain, d.title FROM documents d WHERE d.fragments_blob IS NOT NULL "
            "AND COALESCE(d.filter_version, '') NOT LIKE 'passthrough%' AND NOT EXISTS (SELECT 1 FROM memory_entries x "
            "WHERE x.doc_id = d.doc_id AND COALESCE(x.filter_version, '') LIKE 'passthrough%') ORDER BY d.last_seen DESC"
        ).fetchall()
    picked: list[dict] = []
    per: dict[tuple[str, str], int] = {}
    for doc_id, app, domain, title in rows:
        site = (app or "", domain or "")
        if per.get(site, 0) >= per_site:
            continue
        stored = memory.load_document(doc_id)
        if stored is None:
            continue
        text = readable(stored.document.text(), site=site_line_counts(memory.site_pages(app, domain)))
        if real_words(text) < min_words:
            continue
        per[site] = per.get(site, 0) + 1
        picked.append({"doc_id": doc_id, "app": app, "domain": domain, "title": title, "text": text})
        if len(picked) >= n:
            break
    return picked


def pick_sessions(memory, n: int, days: int = 30) -> list:
    from adaptation.rollups import add_excerpts, group_sessions, to_items

    since = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = group_sessions(to_items(memory.rollup_entries(since_us=int(since.timestamp() * 1_000_000))))
    chosen = sorted(sessions, key=lambda s: sum(i.seconds for i in s.items), reverse=True)[:n]
    add_excerpts(chosen, memory)
    return chosen


def run_model(path: Path, pages: list[dict], sessions: list) -> dict:
    from adaptation.llm_runtime import LocalModel
    from adaptation.rollups import parse_session_note, session_prompt
    from adaptation.summarizer import SummarizeConfig, build_prompt, clean_summary, select_text

    cfg = SummarizeConfig()
    model = LocalModel(path, background=False)
    started = time.perf_counter()
    model.complete("Reply with OK.", "OK?", max_tokens=4)            # load and warm up outside the timings
    load_s = time.perf_counter() - started
    out: dict = {"model": path.stem, "device": model.where, "load_s": round(load_s, 1), "summaries": [], "notes": []}
    try:
        for page in pages:
            system, user = build_prompt({"title": page["title"], "domain": page["domain"]}, select_text(page["text"], cfg.max_text_chars))
            t = time.perf_counter()
            raw = model.complete(system, user, max_tokens=cfg.max_tokens)
            out["summaries"].append({"doc_id": page["doc_id"], "seconds": round(time.perf_counter() - t, 2), "raw": raw,
                                     "kept": clean_summary(raw, max_chars=cfg.max_summary_chars, page_text=page["text"])})
        for s in sessions:
            system, user = session_prompt(s)
            t = time.perf_counter()
            raw = model.complete(system, user, max_tokens=160)
            parsed = parse_session_note(raw)
            out["notes"].append({"key": s.key, "seconds": round(time.perf_counter() - t, 2), "raw": raw,
                                 "parsed": None if parsed is None else {"title": parsed[0], "tag": parsed[1], "tldr": parsed[2]}})
    finally:
        model.close()
    secs = [x["seconds"] for x in out["summaries"]]
    out["per_memory_s"] = round(sum(secs) / len(secs), 2) if secs else None
    out["kept"] = sum(1 for x in out["summaries"] if x["kept"])
    out["notes_parsed"] = sum(1 for x in out["notes"] if x["parsed"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--memory-db", required=True)
    parser.add_argument("--models", required=True, nargs="+", help=".gguf files, or folders of them")
    parser.add_argument("--pages", type=int, default=16)
    parser.add_argument("--sessions", type=int, default=4)
    parser.add_argument("--out", required=True, help="JSON file for the outputs (contains page text: keep it local)")
    args = parser.parse_args(argv)

    from storage.memory_store import MemoryStore

    models = sorted({p for m in args.models for p in ([Path(m)] if Path(m).is_file() else Path(m).glob("*.gguf"))})
    work = Path(tempfile.mkdtemp(prefix="memeval-"))
    try:
        copy = work / "memory.db"
        _copy_db(Path(args.memory_db), copy)
        memory = MemoryStore(copy)
        pages = pick_pages(memory, copy, args.pages)
        sessions = pick_sessions(memory, args.sessions)
        print(f"{len(pages)} pages, {len(sessions)} sessions, {len(models)} models")
        results = []
        for path in models:
            r = run_model(path, pages, sessions)
            results.append(r)
            print(f"{r['model']:40} {r['device']:28} load {r['load_s']:5.1f}s  {r['per_memory_s']}s/memory  "
                  f"kept {r['kept']}/{len(pages)}  notes {r['notes_parsed']}/{len(sessions)}", flush=True)
        memory.close()
        Path(args.out).write_text(json.dumps({
            "pages": [{k: v for k, v in p.items() if k != "text"} | {"excerpt": p["text"][:600]} for p in pages],
            "sessions": [{"key": s.key, "prompt": __import__("adaptation.rollups", fromlist=["session_prompt"]).session_prompt(s)[1]}
                         for s in sessions],
            "results": results,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
