"""Windows surfaces that are not something the user was working in.

Two kinds, both skipped before anything is embedded or stored (``pipeline.py``) and never turned into memories
(``adaptation/screen_memory.py``, ``adaptation/memory_tidy.py``):

* whole processes that are only shell: the compositor, the lock screen, search and start popups, sign-in brokers;
* windows of a real app that are shell furniture, matched by title: Explorer hosts the Alt+Tab switcher ("Task
  Switching"), the desktop ("Program Manager"), Snap Assist and the shut-down dialog. Explorer itself (folders) is real.

Measured on the author's memories (2026-10-24): "Task Switching" alone produced 45 "memories", each one a list of the
other windows' titles. Like the DRC replay (which drops the lock screen), these would also pollute labels and the profile.
"""

from __future__ import annotations

NON_CONTEXT_APPS = frozenset({
    "dwm", "lockapp", "logonui", "searchhost", "searchapp", "shellhost", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost",
    # Sign-in and "open with" brokers: a dialog over the real work, never the work itself.
    "microsoft.aad.brokerplugin", "credentialuibroker", "openwith", "consent", "pickerhost",
    # This app's own window: watching the dashboard is not the user's work, and it re-saved itself constantly.
    "contextdashboard",
})

# Per app, window titles that are shell furniture (compared case-insensitively, after trimming). "*" applies to every app.
NON_CONTEXT_TITLES: dict[str, frozenset[str]] = {
    "explorer": frozenset({"task switching", "program manager", "snap assist", "shut down windows", "task view", ""}),
    "snippingtool": frozenset({"snipping tool overlay"}),
    "*": frozenset({"picture-in-picture", "picture in picture"}),
}


def app_key(app: str | None) -> str:
    name = (app or "").strip().lower()
    return name[:-4] if name.endswith(".exe") else name


def is_non_context(app: str | None, title: str | None) -> bool:
    """True for a shell surface: not a context, never stored or remembered."""
    key = app_key(app)
    if key in NON_CONTEXT_APPS:
        return True
    text = (title or "").strip().casefold()
    return text in NON_CONTEXT_TITLES.get(key, ()) or text in NON_CONTEXT_TITLES["*"]
