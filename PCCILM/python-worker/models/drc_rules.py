"""Frozen copy of the Desktop Research Collector's (DRC) screenshot-capture decision.

DRC is the author's earlier rule-based collector. This module reproduces *only its decision*, "would
DRC take a screenshot now?", with no screenshot taken and no dependency on DRC's folder or database.
It is a baseline to compare Model 1 against, not part of the live decision path.

Source (DRC repo, ``src/DRC.Collector/Services``): ``ScreenshotManager.TryCapture`` (gates, in order),
``CollectionEngine`` (triggers), ``PrivacyFilter.Assess`` (privacy), and the policy values DRC
actually ran with (``collection_policies`` v7 in collector.db, identical to ``DefaultExclusions``).

Triggers: ``application_change``, ``window_title_change``, ``idle_resume``, ``periodic_capture``
(every 90 s), ``scroll_activity`` (50 wheel notches without a >1.5 s pause).

Gates, in DRC's order: screenshots enabled, daily quota (1800), cooldown (15 s; scroll has its own
20 s clock), then the privacy verdict on the foreground window. A refused periodic capture is retried
after 5 s. Gates that fail do not update any state.

Not reproduced: the "local storage is full" gate (disk state) and manual captures. Scroll bursts need
wheel-notch timing, so callers that only have counts per minute cannot supply ``scroll_activity``.

The rules are frozen: do not retune them. If DRC's behaviour must be reproduced differently, add a new
named ``DrcPolicy`` rather than editing this one, so earlier comparisons stay reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

APPLICATION_CHANGE = "application_change"
WINDOW_TITLE_CHANGE = "window_title_change"
IDLE_RESUME = "idle_resume"
PERIODIC_CAPTURE = "periodic_capture"
SCROLL_ACTIVITY = "scroll_activity"
TRIGGERS = (APPLICATION_CHANGE, WINDOW_TITLE_CHANGE, IDLE_RESUME, PERIODIC_CAPTURE, SCROLL_ACTIVITY)

# DRC's WindowInspector: processes whose address bar it reads.
DRC_BROWSER_PROCESSES = frozenset({"chrome", "msedge", "firefox"})
SENSITIVE_TITLE_PATTERNS = ("password", "private browsing", "incognito", "inprivate")

_DEFAULT_EXCLUDED_DOMAINS = (
    "*.bank", "*.banking",
    "online.absa.co.za", "www.fnb.co.za", "www.standardbank.co.za", "www.nedbank.co.za",
    "www.capitecbank.co.za", "online.bankofamerica.com", "www.chase.com", "www.wellsfargo.com",
    "www.hsbc.com", "www.barclays.co.uk", "www.lloydsbank.com", "www.paypal.com", "venmo.com",
    "mail.yahoo.com", "mail.proton.me", "mail.zoho.com",
    "www.webmd.com", "www.mayoclinic.org", "patient.info", "www.nhs.uk", "mychart.com",
)
_DEFAULT_EXCLUDED_APPS = (
    "WhatsApp", "Telegram", "Signal", "Discord", "Slack", "Microsoft Teams",
    "KeePass", "1Password", "Bitwarden", "LastPass", "KeePassXC", "WhatAapp", "Floorp",
)


@dataclass(frozen=True)
class DrcPolicy:
    """The settings DRC ran with (collection policy v7)."""

    capture_on_app_change: bool = True
    capture_on_window_change: bool = True
    capture_after_idle: bool = True
    capture_on_scroll: bool = True
    periodic_interval_seconds: int = 90
    cooldown_seconds: int = 15
    scroll_cooldown_seconds: int = 20
    scroll_capture_threshold: int = 50
    max_per_day: int = 1800
    privacy_retry_seconds: int = 5
    excluded_domains: tuple[str, ...] = _DEFAULT_EXCLUDED_DOMAINS
    excluded_apps: tuple[str, ...] = _DEFAULT_EXCLUDED_APPS


@dataclass(frozen=True)
class DrcDecision:
    """``capture`` is DRC's decision. ``blocked_by`` says which gate refused when it is False."""

    capture: bool
    trigger: str
    blocked_by: str | None = None


class DrcRules:
    """Stateful DRC capture decision. Feed it triggers in time order; one instance per DRC session."""

    name = "drc-rules-v7"

    def __init__(self, policy: DrcPolicy | None = None) -> None:
        self.policy = policy or DrcPolicy()
        self._apps = frozenset(_normalize_app(a) for a in self.policy.excluded_apps)
        self._domains = frozenset(d for d in (_entry_host(e) for e in self.policy.excluded_domains if not _is_wildcard(e)) if d)
        self._suffixes = tuple("." + e.strip().lower().lstrip("*.").rstrip(".") for e in self.policy.excluded_domains if _is_wildcard(e))
        self.reset()

    def reset(self) -> None:
        """New DRC process/session: DRC keeps cooldown, quota and retry state in memory only."""
        self._last_capture: datetime | None = None
        self._last_scroll_capture: datetime | None = None
        self._next_periodic_attempt: datetime | None = None
        self._captures_today = 0
        self._quota_day: date | None = None

    @property
    def last_capture(self) -> datetime | None:
        return self._last_capture

    def periodic_due_at(self, now: datetime) -> datetime:
        """Earliest time a periodic capture may be attempted, given state at ``now``."""
        base = self._last_capture + timedelta(seconds=self.policy.periodic_interval_seconds) if self._last_capture else now
        if self._next_periodic_attempt is not None:
            base = max(base, self._next_periodic_attempt)
        return base

    def decide(
        self,
        trigger: str,
        ts: datetime,
        *,
        app: str | None,
        title: str | None = None,
        domain: str | None = None,
        application_name: str | None = None,
        address_known: bool | None = None,
    ) -> DrcDecision:
        """Would DRC capture at ``ts`` for ``trigger``, with this foreground window?

        ``address_known``: whether DRC could read the browser address (default: ``domain is not None``).
        Only matters for browsers while domain exclusions exist (DRC fails closed).
        """
        if trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {trigger!r}")
        policy = self.policy

        if trigger == APPLICATION_CHANGE and not policy.capture_on_app_change:
            return DrcDecision(False, trigger, "trigger_disabled")
        if trigger == WINDOW_TITLE_CHANGE and not policy.capture_on_window_change:
            return DrcDecision(False, trigger, "trigger_disabled")
        if trigger == IDLE_RESUME and not policy.capture_after_idle:
            return DrcDecision(False, trigger, "trigger_disabled")
        if trigger == SCROLL_ACTIVITY and not policy.capture_on_scroll:
            return DrcDecision(False, trigger, "trigger_disabled")

        if self._quota_day != ts.date():
            self._quota_day = ts.date()
            self._captures_today = 0
        if self._captures_today >= policy.max_per_day:
            return DrcDecision(False, trigger, "daily_quota")

        scroll = trigger == SCROLL_ACTIVITY
        last = self._last_scroll_capture if scroll else self._last_capture
        cooldown = policy.scroll_cooldown_seconds if scroll else policy.cooldown_seconds
        if last is not None and (ts - last).total_seconds() < cooldown:
            return DrcDecision(False, trigger, "cooldown")

        reason = self._privacy_block(app, application_name, title, domain, address_known)
        if reason:
            if trigger == PERIODIC_CAPTURE:
                self._next_periodic_attempt = ts + timedelta(seconds=policy.privacy_retry_seconds)
            return DrcDecision(False, trigger, reason)

        if scroll:
            self._last_scroll_capture = ts
        else:
            self._last_capture = ts
        self._captures_today += 1
        return DrcDecision(True, trigger)

    # ---------------------------------------------------------------- privacy (PrivacyFilter.Assess)

    def _privacy_block(self, app, application_name, title, domain, address_known) -> str | None:
        if title and any(p in title.lower() for p in SENSITIVE_TITLE_PATTERNS):
            return "sensitive_title"
        if _normalize_app(app) in DRC_BROWSER_PROCESSES:
            if domain and self._domain_excluded(domain):
                return "excluded_domain"
            known = (domain is not None) if address_known is None else address_known
            if not known and (self._domains or self._suffixes):
                return "browser_address_unknown"
        if _normalize_app(app) in self._apps or _normalize_app(application_name) in self._apps:
            return "excluded_app"
        return None

    def _domain_excluded(self, domain: str) -> bool:
        host = _entry_host(domain)
        if not host:
            return False
        if any(host.endswith(s) for s in self._suffixes):
            return True
        candidate = host
        while True:
            if candidate in self._domains:
                return True
            if "." not in candidate:
                return False
            candidate = candidate.split(".", 1)[1]


def _is_wildcard(entry: str) -> bool:
    text = entry.strip()
    return text.startswith("*.") or text.startswith(".")


def _entry_host(entry: str | None) -> str | None:
    text = (entry or "").strip().lower()
    if not text:
        return None
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].rstrip(".")
    if text.startswith("www.") and len(text) > 4:
        text = text[4:]
    return text or None


def _normalize_app(name: str | None) -> str:
    n = (name or "").strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    return n[:-4] if n.endswith(".exe") else n


class LiveDrcObserver:
    """Derives DRC's triggers from the live snapshot stream and asks :class:`DrcRules` each time.

    The live collector sends a snapshot per screen change, not per DRC trigger, so triggers are
    inferred: a changed app or title is ``application_change`` / ``window_title_change``, leaving idle is
    ``idle_resume``, and a snapshot arriving 90 s after the last capture is ``periodic_capture``. Idle
    snapshots are ignored, as DRC does nothing while idle. A periodic capture is only noticed when a
    snapshot arrives, so it can lag DRC's own timer. The offline replay in ``eval.drc_agreement`` is the
    validated version; this one is an approximation for logging.

    One instance per worker run (DRC likewise keeps its cooldown and quota in memory per process).
    """

    def __init__(self, rules: DrcRules | None = None) -> None:
        self.rules = rules or DrcRules()
        self._ctx: tuple[str, str] | None = None  # (normalized app, title)
        self._was_idle = False

    def observe(self, ts: datetime, *, app: str | None, title: str | None, domain: str | None, is_idle: bool) -> DrcDecision:
        if is_idle:
            self._was_idle = True
            return DrcDecision(False, "none", "idle")

        key = (_normalize_app(app), title or "")
        if self._was_idle:
            trigger = IDLE_RESUME
        elif self._ctx is None or self._ctx[0] != key[0]:
            trigger = APPLICATION_CHANGE
        elif self._ctx[1] != key[1]:
            trigger = WINDOW_TITLE_CHANGE
        elif self.rules.last_capture is not None and ts >= self.rules.periodic_due_at(ts):
            trigger = PERIODIC_CAPTURE
        else:
            self._ctx = key
            return DrcDecision(False, "none", "no_trigger")

        self._was_idle = False
        self._ctx = key
        return self.rules.decide(trigger, ts, app=app, title=title, domain=domain)
