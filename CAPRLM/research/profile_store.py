"""Local, content-free state behind the Privacy Review / Policy & Risk screens: the user's
policy, their per-category corrections, and per-day activity counts.

Nothing here ever holds a sensitive value — corrections are (category, decision), activity is
counts. Stored as one JSON file, written atomically.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STATE_PATH = Path(os.environ.get(
    "CAPRLM_STATE_PATH", Path(__file__).resolve().parent / "artifacts" / "profile.json"))

LEVELS = ("relaxed", "balanced", "strict")
ACTIONS = ("never", "tokenize", "mask")
EVIDENCE_NEEDED = 2        # corrections of one kind, net of the opposite kind, before a rule is learned
KEEP_DAYS = 60

# id -> (label, detector categories, default action)
GROUPS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "credentials": ("Credentials & keys", ("CREDENTIAL", "URL_TOKEN"), "never"),
    "financial": ("Financial figures", ("FINANCIAL_FIGURE", "BANK_ACCOUNT", "CARD_LIKE_NUMBER"), "tokenize"),
    "identifiers": ("Personal identifiers",
                    ("NATIONAL_ID", "EMAIL", "PHONE", "PERSON", "VEHICLE_PLATE", "IP_ADDRESS"), "tokenize"),
    "health": ("Health & medical", ("MEDICAL",), "tokenize"),
}
LEARNABLE_CATEGORIES = ("CREDENTIAL", "URL_TOKEN", "FINANCIAL_FIGURE", "BANK_ACCOUNT", "CARD_LIKE_NUMBER",
                        "NATIONAL_ID", "EMAIL", "PHONE", "PERSON", "VEHICLE_PLATE", "IP_ADDRESS", "MEDICAL",
                        "LOCATION", "ORG", "DATE")


def group_of(category: str) -> str | None:
    return next((g for g, (_, cats, _) in GROUPS.items() if category in cats), None)


def _default_state() -> dict:
    return {
        "policy": {
            "level": "balanced",
            "groups": {g: {"action": a, "enabled": True} for g, (_, _, a) in GROUPS.items()},
            "llm": {"mask_before_send": True, "provider": "", "model": ""},
        },
        "corrections": [],     # {category, decision: "masked"|"visible", at}
        "activity": {},        # "YYYY-MM-DD" -> {flagged, redacted, kept_sum, reviews}
    }


class ProfileStore:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> dict:
        state = _default_state()
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return state
        # Merge over defaults so a file from an older version still has every key.
        pol = saved.get("policy", {})
        state["policy"]["level"] = pol.get("level", state["policy"]["level"])
        state["policy"]["llm"].update(pol.get("llm", {}))
        for g, cfg in pol.get("groups", {}).items():
            if g in state["policy"]["groups"]:
                state["policy"]["groups"][g].update(cfg)
        state["corrections"] = saved.get("corrections", [])
        state["activity"] = saved.get("activity", {})
        return state

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- policy ----------------------------------------------------------------------------
    def policy(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._state["policy"]))

    def update_policy(self, *, level: str | None = None, groups: dict | None = None,
                      llm: dict | None = None) -> dict:
        with self._lock:
            pol = self._state["policy"]
            if level is not None:
                if level not in LEVELS:
                    raise ValueError(f"level must be one of {LEVELS}")
                pol["level"] = level
            for g, cfg in (groups or {}).items():
                if g not in GROUPS:
                    raise ValueError(f"unknown group: {g}")
                if "action" in cfg:
                    if cfg["action"] not in ACTIONS:
                        raise ValueError(f"action must be one of {ACTIONS}")
                    pol["groups"][g]["action"] = cfg["action"]
                if "enabled" in cfg:
                    pol["groups"][g]["enabled"] = bool(cfg["enabled"])
            for k, v in (llm or {}).items():
                if k not in pol["llm"]:
                    raise ValueError(f"unknown llm setting: {k}")
                pol["llm"][k] = bool(v) if k == "mask_before_send" else str(v)
            self._save()
            return json.loads(json.dumps(pol))

    # -- corrections -> learned rules ------------------------------------------------------------
    def add_correction(self, category: str, decision: str) -> None:
        if decision not in ("masked", "visible"):
            raise ValueError("decision must be 'masked' or 'visible'")
        with self._lock:
            self._state["corrections"].append(
                {"category": category, "decision": decision, "at": datetime.now(timezone.utc).isoformat()})
            self._save()

    def learned_rules(self) -> dict[str, dict]:
        """category -> {decision, evidence}. A rule exists once one decision leads the other
        by EVIDENCE_NEEDED corrections — so a single misclick teaches nothing, and later
        opposite corrections can undo a rule."""
        with self._lock:
            tally: dict[str, int] = {}
            for c in self._state["corrections"]:
                tally[c["category"]] = tally.get(c["category"], 0) + (1 if c["decision"] == "masked" else -1)
        return {cat: {"decision": "masked" if n > 0 else "visible", "evidence": abs(n)}
                for cat, n in tally.items() if abs(n) >= EVIDENCE_NEEDED}

    def n_corrections(self) -> int:
        with self._lock:
            return len(self._state["corrections"])

    def reset_profile(self) -> None:
        with self._lock:
            self._state["corrections"] = []
            self._save()

    # -- activity --------------------------------------------------------------------------------
    def record_review(self, flagged: int, redacted: int, utility_kept: float,
                      today: date | None = None) -> None:
        today = today or date.today()
        with self._lock:
            a = self._state["activity"].setdefault(
                today.isoformat(), {"flagged": 0, "redacted": 0, "kept_sum": 0.0, "reviews": 0})
            a["flagged"] += flagged
            a["redacted"] += redacted
            a["kept_sum"] += utility_kept
            a["reviews"] += 1
            cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
            for d in [d for d in self._state["activity"] if d < cutoff]:
                del self._state["activity"][d]
            self._save()

    def weekly(self, today: date | None = None) -> list[dict]:
        today = today or date.today()
        with self._lock:
            out = []
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                a = self._state["activity"].get(d.isoformat(), {})
                out.append({"date": d.isoformat(), "weekday": d.strftime("%a"),
                            "flagged": a.get("flagged", 0), "redacted": a.get("redacted", 0),
                            "kept_sum": a.get("kept_sum", 0.0), "reviews": a.get("reviews", 0)})
            return out
