"""Features for the risk model: entity category + detection confidence + PrivacyContext + the
words around the span + span shape + document signals.

Raw app/domain/title strings are too high-cardinality for a small dataset, so they are reduced
to coarse context buckets by keyword. The bucket list is a hand-written default (BUILD_LOG.md),
not a research result. Cross features (category x context) are included because a linear model
can only express "the same entity is riskier on a banking page than in news" through an
interaction term.

Features are organised in named GROUPS so an experiment can switch one off and measure what it
was worth (research/eval/benchmark.py). The default is every group.
"""

from __future__ import annotations

from shared.types import PrivacyContext

from research.detection.candidate import Candidate

# base: category, detector confidence, which detector     source: uia/ocr
# app: app name + app bucket                              context: domain/title buckets
# window: words around the span                           shape: span shape buckets
# doc: document-level signals
ALL_GROUPS: tuple[str, ...] = ("base", "source", "app", "context", "window", "shape", "doc")

_BUCKET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "finance": ("bank", "wallet", "finance", "transaction", "transfer", "account summary", "paypal",
                "quickbooks", "invoice", "payment", "statement", "billing"),
    "health": ("health", "clinic", "hospital", "medical", "patient", "pharmacy", "lab results",
               "prescription", "doctor", "rx"),
    "hr": ("payroll", "salary", "employee", "recruit", "hr.", "workday", "bamboo", "resume", "cv"),
    "email": ("mail", "inbox", "outlook", "gmail", "thunderbird"),
    "chat": ("whatsapp", "messages", "telegram", "sms", "signal", "messenger", "viber", "voice note"),
    "social": ("facebook", "twitter", "instagram", "linkedin", "reddit", "social", "forum", "tiktok", "x.com"),
    "code": ("github", "gitlab", "stackoverflow", "pull request", "commit", "visual studio code",
             "vs code", "vscode", "terminal", ".log", "intellij", "pycharm", "postman", "docker", "console"),
    "news": ("news", "bbc", "cnn", "wikipedia", "article"),
    "shopping": ("amazon", "ebay", "shop", "cart", "checkout", "store", "order"),
    "work": ("slack", "teams", "jira", "confluence", "zendesk", "ticket", "calendar", "meeting", "#"),
    "notes": ("notepad", "notes", "onenote", "notion", "evernote", "keep"),
}


def context_buckets(*values: str | None) -> list[str]:
    haystack = " ".join(v.lower() for v in values if v)
    return [b for b, kws in _BUCKET_KEYWORDS.items() if any(k in haystack for k in kws)]


def featurize(cand: Candidate, ctx: PrivacyContext, groups: tuple[str, ...] = ALL_GROUPS) -> dict[str, float]:
    on = set(groups)
    cat = cand.category
    f: dict[str, float] = {}

    if "base" in on:
        f[f"cat={cat}"] = 1.0
        f["confidence"] = cand.confidence
        f[f"det={cand.detector}"] = 1.0
    if "source" in on:
        f[f"src={ctx.source.value}"] = 1.0
        f[f"cat={cat}|src={ctx.source.value}"] = 1.0

    app_b = context_buckets(ctx.app)
    dom = context_buckets(ctx.domain)
    title = context_buckets(ctx.title)

    if "app" in on:
        f[f"app={ctx.app.lower()}"] = 1.0
        f[f"cat={cat}|app={ctx.app.lower()}"] = 1.0
        for b in app_b:
            f[f"appb={b}"] = 1.0
    if "context" in on:
        # Domain and title buckets are kept separate (a bank page titled "Inbox" is not the
        # same as an inbox), then merged into one "where am I" set for the crosses.
        for b in dom:
            f[f"dom={b}"] = 1.0
        for b in title:
            f[f"title={b}"] = 1.0
        if not dom:
            f["dom=none"] = 1.0
        for b in set(app_b) | set(dom) | set(title):
            f[f"cat={cat}|ctx={b}"] = 1.0
    if "window" in on:
        for w in cand.window:
            f[f"w={w}"] = 1.0
            f[f"cat={cat}|w={w}"] = 1.0
    if "shape" in on:
        for sh in cand.shape:
            f[f"shape={sh}"] = 1.0
        f[f"cat={cat}|shape={cand.shape[0] if cand.shape else 'none'}"] = 1.0
    if "doc" in on:
        for d in cand.doc:
            f[f"doc={d}"] = 1.0
            if d.startswith("kw:"):
                f[f"cat={cat}|doc={d}"] = 1.0
    return f
