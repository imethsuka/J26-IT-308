"""Trim a stored page down to the text worth reading before it goes to the summary model.

Screen text arrives as one line per accessibility element, so it carries a lot of page furniture: menu items, tab and toolbar labels,
buttons, icon glyphs, a site's navigation and footer repeated on every page. The collector already skips real window chrome; what
is left sits inside the page and looks like ordinary text. Reading it wastes the model's small window (about 6,000 characters),
so the summariser asks this module for the useful part first.

Non-destructive: this only decides what the model is *shown*. The stored page is not changed.

Rules, in order:
1. drop lines with no letter or digit (bullets, icon glyphs, separators);
2. drop lines that a whole site repeats on most of its pages (navigation, footers, cookie banners);
3. drop a repeated short line after its first occurrence;
4. drop a *run* of five or more consecutive short lines (three words or fewer, no sentence punctuation): menu bars, tab strips,
   toolbars and label lists. A heading or a lone short line between sentences is kept.
"""

from __future__ import annotations

import re
from collections import Counter

SHORT_WORDS = 3
MIN_RUN = 5                    # this many short lines in a row are page furniture
SITE_MIN_PAGES = 3             # a line must be on at least this many pages of the site...
SITE_MIN_SHARE = 0.4           # ...and on at least this share of them, to count as furniture

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _is_short(line: str) -> bool:
    return len(line.split()) <= SHORT_WORDS and not re.search(r"[.!?;:]\s*$", line)


def site_line_counts(pages: list[list[str]]) -> tuple[Counter, int]:
    """(how many of the site's pages each line appears on, number of pages)."""
    counts: Counter = Counter()
    for lines in pages:
        counts.update({ln.strip() for ln in lines if ln.strip()})
    return counts, len(pages)


def clean_page(text: str, *, site: tuple[Counter, int] | None = None) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    counts, n_pages = site if site else (Counter(), 0)

    keep: list[str] = []
    seen_short: set[str] = set()
    for ln in lines:
        if not ln or not _WORD.search(ln):                                        # 1. nothing readable
            continue
        if n_pages >= SITE_MIN_PAGES and counts[ln] >= SITE_MIN_PAGES and counts[ln] / n_pages >= SITE_MIN_SHARE:
            continue                                                              # 2. the site's own furniture
        if _is_short(ln):
            if ln in seen_short:                                                  # 3. repeated label
                continue
            seen_short.add(ln)
        keep.append(ln)

    out: list[str] = []                                                           # 4. runs of short lines
    run: list[str] = []
    for ln in keep + [""]:                                                        # the sentinel flushes the last run
        if ln and _is_short(ln):
            run.append(ln)
            continue
        if len(run) < MIN_RUN:
            out.extend(run)
        run = []
        if ln:
            out.append(ln)
    return "\n".join(out)


MIN_KEEP_SHARE = 0.15          # if cleaning would leave less than this share of a page, the page is mostly short items (a file
MIN_KEEP_CHARS = 200           # listing, a chat of one-liners): show the model the original rather than almost nothing


def readable(text: str, *, site: tuple[Counter, int] | None = None) -> str:
    """The text the summary model should read: the cleaned page, or the original when cleaning would remove nearly everything."""
    cleaned = clean_page(text, site=site)
    if len(cleaned) >= MIN_KEEP_SHARE * len(text) and len(cleaned) >= min(MIN_KEEP_CHARS, len(text)):
        return cleaned
    return text.strip()
