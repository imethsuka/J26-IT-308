"""Window-title normalization: remove the parts of a title that change while the context does not.

Revisit detection (labels) and revisit features match contexts by (app, domain, title). Exact titles
break that match for reasons unrelated to the context, measured on the author's DRC data (17,027
window titles, 2026-10-19):

- Edge appends ``and N more pages`` (6,179 titles), so opening or closing *another* tab renames the
  active one;
- invisible zero-width characters (6,510 titles, e.g. Edge's ``Microsoft\\u200b Edge``);
- leading notification counters ``(8) YouTube`` -> ``(9) YouTube`` (700 titles);
- unsaved-change markers ``● main.py`` / ``*notes.txt`` (80 titles).

Only those dynamic parts are removed; everything that identifies the page/document is kept.
Opt-in: label construction and feature assembly use exact titles unless a normalizer is passed.
"""

from __future__ import annotations

import re

# Bump when the rules below change; stamped into label versions built with this normalizer.
TITLE_NORMALIZATION_VERSION = 1

_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")
_LEADING_COUNTER = re.compile(r"^\s*[(\[]\d+\+?[)\]]\s*")
_LEADING_UNSAVED = re.compile(r"^\s*[●•◉*]\s*")
_MORE_PAGES = re.compile(r"\s+and \d+ more pages?(?=\s+-\s|\s*$)")
_SPACES = re.compile(r"\s+")


def normalize_title(title: str | None) -> str | None:
    """``"(3) ● Inbox and 2 more pages - Personal - Microsoft\\u200b Edge"`` ->
    ``"Inbox - Personal - Microsoft Edge"``. ``None``/blank stays ``None``."""
    if title is None:
        return None
    text = _ZERO_WIDTH.sub("", title)
    previous = None
    while previous != text:  # markers can be stacked: "(3) ● file"
        previous = text
        text = _LEADING_UNSAVED.sub("", _LEADING_COUNTER.sub("", text))
    text = _MORE_PAGES.sub("", text)
    text = _SPACES.sub(" ", text).strip()
    return text or None


# Browser and editor names a window title ends with, and the profile segment Edge puts before its name. They say which app,
# which the memory already records, and to a small model they read as page content ("Personal" became a topic).
_APP_SUFFIX = re.compile(
    r"(\s+[-–—]\s+(Personal|Work|Profile \d+))?"
    r"\s+[-–—]\s+(Microsoft\s*Edge|Google Chrome|Mozilla Firefox|Zen Browser|Brave|Opera|Visual Studio Code)\s*$"
)


def display_title(title: str | None) -> str:
    """The title as a person would name the page: ``normalize_title`` without the browser/editor name and Edge's profile.
    ``"(3) Inbox and 2 more pages - Personal - Microsoft​ Edge"`` -> ``"Inbox"``."""
    text = normalize_title(title) or ""
    return _APP_SUFFIX.sub("", text).strip() or text
