"""Hide memory entries that are task switching, not memories: shell surfaces and glances.

New episodes are already filtered when they are built (``adaptation/screen_memory.py``). This catches the entries written
before that existed, and anything that slips through. It only sets ``memory_entries.hidden`` to the reason: nothing is
deleted, and ``MemoryStore.unhide_all`` brings everything back. Hidden entries are skipped by the summaries, the session
and day notes, and the dashboard.

A glance is judged by the time until the next episode started (on any window), not by the episode's own capture times:
a page read without scrolling has one capture and would otherwise look like zero seconds.
"""

from __future__ import annotations

import bisect
import json
from typing import Any

from features.surfaces import is_non_context
from storage.memory_store import MemoryStore

GLANCE_SECONDS = 10.0


def tidy_memories(memory: MemoryStore, *, glance_seconds: float = GLANCE_SECONDS) -> dict[str, Any]:
    starts = memory.episode_starts()
    reasons: dict[int, str] = {}
    for row in memory.visible_episodes():
        entry = json.loads(row["entry_json"])
        if is_non_context(entry.get("app"), entry.get("title")):
            reasons[row["id"]] = "task switching"
            continue
        later = bisect.bisect_right(starts, row["start_ts"])
        if later < len(starts) and not row["key_presses"]:
            on_screen = (starts[later] - row["start_ts"]) / 1_000_000
            if on_screen < glance_seconds:
                reasons[row["id"]] = "glance"
    memory.hide_entries(reasons)
    values = list(reasons.values())
    return {"hidden": len(values), "task_switching": values.count("task switching"), "glances": values.count("glance")}
