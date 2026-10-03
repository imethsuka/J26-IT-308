"""Keep-Worthiness (KW): Model 2's label, learned from how this user behaves after a capture.

An opt-in alternative to the default rule (``v3``: "the context came back within the horizon"). Stamped
``kw1/h<horizon>`` so its labels are never mixed with the default's. Full write-up:
``docs/Importnace Algorithm.md``.

KW is the *teacher*: it looks at what happened **after** a capture, which Model 1 cannot see at capture
time, and scores how worth keeping the capture was. Model 1 learns to predict it from the moment itself.

For a capture ``x`` at time ``t`` with context key ``k`` and horizon ``H`` (default 48 h):

``N`` need from returning   ``min(1, sum over later visits j of k within H of exp(-dt_j / tau))``, tau = 24 h
                            (sooner and more frequent returns count more; each visit once)
``A`` attention -> action   0 if the visit's active dwell < 10 s, else ``1 - exp(-max(0, K_after - K_usual) / kappa)``
                            where ``K_after`` is typing in *other* contexts in the 10 minutes after leaving, and
                            ``K_usual`` this user's usual typing for that hour (:class:`UserProfile`)
``U`` reuse                 screen text flowing into later typing. **Missing**: no screen text is stored yet
``R`` retrieval             Member 3's feedback, injected as ``retrieval_fn``; missing unless provided
``L`` loss                  ``1 - r``, ``r = 1 - (1-r_familiar)(1-r_own)(1-r_stable)``, halved for ephemeral
                            sources: how hard is it to find this again?

    need = 1 - (1-N)(1-A)(1-U)(1-R)          noisy-OR over the signals that are available
    KW   = need * (lambda + (1-lambda) * L)  lambda = 0.3: easy-to-find things are ranked lower, not zeroed
    label = 1 if KW >= threshold else 0      threshold 0.35

Every constant is a **starting guess** (:class:`KWParams`), to be reported, tested for sensitivity and
ideally fitted to hand labels. Two honest approximations: the collector sends no URL, so "stable page" is a
browser page on a domain not listed as ephemeral; and typing is read from the key counts of snapshots
(a 10 s window per snapshot), consistently for the observed value and the user's baseline.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from models.rule_based import DEFAULT_APP_CATEGORIES

from .label_construction import (
    DEFAULT_WEIGHTS,
    LabelConstructor,
    LabelResult,
    Timeline,
    _retrieval_strength,
)
from .user_profile import DEFAULT_WINDOW, ActivityIndex, UserProfile, _app_name

KW_RULE_VERSION = 1

EPHEMERAL_DOMAINS = frozenset({
    "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "reddit.com", "linkedin.com",
    "web.whatsapp.com", "messenger.com", "discord.com", "news.google.com", "t.me",
})
_SEARCH_TITLE = re.compile(r"(?:-|—)\s*(?:google search|bing|search results)\b|\bsearch results?\b", re.IGNORECASE)
_FILE_TITLE = re.compile(r"\.[A-Za-z0-9]{1,5}\b")
_OWN_FILE_APPS = frozenset({"explorer"})


@dataclass(frozen=True)
class KWParams:
    """Starting-guess constants. Change one and the label is no longer ``kw1``: bump ``KW_RULE_VERSION``."""

    tau_need_hours: float = 24.0
    min_dwell_seconds: float = 10.0
    window: timedelta = DEFAULT_WINDOW
    kappa_keys: float = 200.0
    familiar_scale: float = 2.0
    own_file: float = 0.8
    stable_page: float = 0.5
    ephemeral_factor: float = 0.5
    lam: float = 0.3
    threshold: float = 0.35


DEFAULT_KW_PARAMS = KWParams()


@dataclass
class KWResult(LabelResult):
    """A :class:`LabelResult` (so every caller of the default rule works) plus KW's components."""

    kw: float = 0.0
    need_from_returns: float = 0.0
    action: float = 0.0
    reuse: float | None = None
    loss: float = 1.0
    need: float = 0.0
    refindability: float = 0.0


def kw_score(
    returns: float, action: float, reuse: float | None, retrieval: float | None, loss: float, lam: float
) -> tuple[float, float]:
    """``(KW, need)``. Missing signals (``None``) drop out of the noisy-OR."""
    none_fired = (1.0 - returns) * (1.0 - action)
    if reuse is not None:
        none_fired *= 1.0 - reuse
    if retrieval is not None:
        none_fired *= 1.0 - retrieval
    need = 1.0 - none_fired
    return need * (lam + (1.0 - lam) * loss), need


def refindability(
    *, earlier_visits: int, app: str | None, domain: str | None, title: str | None, params: KWParams = DEFAULT_KW_PARAMS
) -> float:
    """``r``: how easily this context can be found again (the complement of "loss")."""
    name = _app_name(app)
    category = DEFAULT_APP_CATEGORIES.get(name, "unknown")
    familiar = 1.0 - math.exp(-earlier_visits / params.familiar_scale)
    ephemeral = is_ephemeral(name, category, domain, title)
    own = params.own_file if (category == "productivity" or name in _OWN_FILE_APPS) and _FILE_TITLE.search(title or "") else 0.0
    stable = params.stable_page if category == "browser" and domain and not ephemeral else 0.0
    r = 1.0 - (1.0 - familiar) * (1.0 - own) * (1.0 - stable)
    return r * params.ephemeral_factor if ephemeral else r


def is_ephemeral(name: str, category: str, domain: str | None, title: str | None) -> bool:
    host = (domain or "").lower()
    if category == "communication":
        return True
    if any(host == d or host.endswith("." + d) for d in EPHEMERAL_DOMAINS):
        return True
    return bool(category == "browser" and _SEARCH_TITLE.search(title or ""))


class KWLabelConstructor(LabelConstructor):
    """Drop-in for :class:`LabelConstructor` that labels with KW. Records should carry ``.features``
    (as stored context records do) for the typing signals; without them ``A`` is 0."""

    def __init__(self, *, profile: UserProfile | None = None, params: KWParams = DEFAULT_KW_PARAMS, **kwargs: Any) -> None:
        kwargs.setdefault("threshold", params.threshold)
        super().__init__(**kwargs)
        self.params = params
        # A fixed profile (e.g. one loaded from a model version) or None to learn it from each batch.
        self._fixed_profile = profile
        self._index: ActivityIndex | None = None
        self._profile: UserProfile | None = profile

    @property
    def label_version(self) -> str:
        return f"kw{KW_RULE_VERSION}/h{self.label_horizon.total_seconds() / 3600.0:g}"

    @property
    def profile(self) -> UserProfile | None:
        return self._profile

    def construct_batch(self, records: Iterable[Any], *, now: datetime | None = None, labelled_ids: set[Any] | None = None):
        all_records = list(records)
        self._index = ActivityIndex(all_records, session_gap=self.session_gap, title_normalizer=self.title_normalizer)
        self._profile = self._fixed_profile or UserProfile.fit(
            all_records, horizon=self.label_horizon, window=self.params.window,
            session_gap=self.session_gap, title_normalizer=self.title_normalizer, index=self._index,
        )
        return super().construct_batch(all_records, now=now, labelled_ids=labelled_ids)

    def label_for(self, target: Any, timeline: Timeline) -> KWResult:
        index, profile, p = self._index, self._profile, self.params
        if index is None or profile is None:
            raise RuntimeError("KWLabelConstructor.label_for needs construct_batch to build its index first")
        i = index.index_of_id[target.id]
        visit = index.visits[index.visit_of[i]]

        delays = index.later_visit_delays(i, self.label_horizon)
        returns = min(1.0, sum(math.exp(-d / p.tau_need_hours) for d in delays))

        action = 0.0
        if visit.active_dwell >= p.min_dwell_seconds:
            after = index.keys_after(visit.last_ts, visit.key, p.window)
            usual = profile.typing_usual(index.hours[i])
            action = 1.0 - math.exp(-max(0.0, after - usual) / p.kappa_keys)

        retrieval = self.retrieval_fn(target) if self.retrieval_fn is not None else None
        retrieval_strength = None if retrieval is None else _retrieval_strength(retrieval)

        r = refindability(earlier_visits=index.earlier_visits(i), app=target.app, domain=target.domain, title=target.title, params=p)
        kw, need = kw_score(returns, action, None, retrieval_strength, 1.0 - r, p.lam)
        return KWResult(
            label=1 if kw >= self.threshold else 0,
            score=round(kw, 6),
            revisited=bool(delays),
            continued=timeline.is_continued(target),
            retrieval=retrieval,
            retrieval_available=retrieval is not None,
            threshold=self.threshold,
            weights=DEFAULT_WEIGHTS,
            kw=round(kw, 6),
            need_from_returns=round(returns, 6),
            action=round(action, 6),
            reuse=None,
            loss=round(1.0 - r, 6),
            need=round(need, 6),
            refindability=round(r, 6),
        )
