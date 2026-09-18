"""Rule-based importance scorer — the interpretable baseline to compare Model 1 against.

This is intentionally a *fair* baseline, not a strawman: it combines three interpretable rules as
weighted evidence in log-odds space and passes the sum through a logistic function, so it emits a
genuine probability (usable for ROC/AUC/threshold comparison), not a hand-picked constant.

Exact logic
-----------
Let::

    dwell        = features["tmp_dwell_seconds"]      (seconds in the current context)
    seen_before  = features["ctx_seen_before"]        (1 if this context was seen earlier)
    app          = features["ctx_app"]                (foreground process name)

Three rules, each producing a bounded component:

1. Dwell-time threshold  (longer dwell => more likely important)
     dwell_component = clip((dwell - DWELL_MIN) / (DWELL_SAT - DWELL_MIN), 0, 1)
   0 at/below DWELL_MIN seconds, ramping linearly to 1 at/above DWELL_SAT seconds.

2. Revisit flag  (a context seen before is more likely to matter again)
     revisit_component = 1.0 if seen_before else 0.0

3. App-category weight  (some app kinds are more work-relevant than others)
     app_component = APP_CATEGORY_WEIGHTS[category_of(app)]  in [-1, 1]
   Productivity/creation apps score +1, communication +0.5, browsers 0 (ambiguous, kept neutral on
   purpose), media/games -1, unknown 0.

Combined in log-odds and squashed::

    logit = BIAS
          + W_DWELL   * dwell_component
          + W_REVISIT * revisit_component
          + W_APP     * app_component
    p = 1 / (1 + exp(-logit))

Default weights (all overridable via the constructor for tuning):

    BIAS = -1.4     # prior ~0.20 when every signal is neutral
    W_DWELL   = 2.0
    W_REVISIT = 1.5
    W_APP     = 1.5

With defaults, p spans ~0.05 (short dwell, never seen, media app) to ~0.97 (long dwell, revisited,
productivity app). ``explain`` returns the per-rule breakdown for auditing.
"""

from __future__ import annotations

import math
from typing import Any

from .base import Features, ImportanceScorer

# App category -> log-odds weight. Case-insensitive match on the process name (".exe" stripped).
APP_CATEGORY_WEIGHTS: dict[str, float] = {
    "productivity": 1.0,
    "communication": 0.5,
    "browser": 0.0,
    "media": -1.0,
    "unknown": 0.0,
}

# Process name -> category. Extend as needed; unknown names fall through to "unknown".
DEFAULT_APP_CATEGORIES: dict[str, str] = {
    # productivity / creation
    "code": "productivity", "devenv": "productivity", "pycharm64": "productivity",
    "idea64": "productivity", "sublime_text": "productivity", "vim": "productivity",
    "winword": "productivity", "excel": "productivity", "powerpnt": "productivity",
    "onenote": "productivity", "notion": "productivity", "obsidian": "productivity",
    "photoshop": "productivity", "illustrator": "productivity", "figma": "productivity",
    "blender": "productivity", "cmd": "productivity", "powershell": "productivity",
    "windowsterminal": "productivity", "wt": "productivity",
    # communication
    "outlook": "communication", "teams": "communication", "ms-teams": "communication",
    "slack": "communication", "discord": "communication", "zoom": "communication",
    "thunderbird": "communication",
    # browsers (deliberately neutral: could be work or leisure)
    "chrome": "browser", "msedge": "browser", "firefox": "browser", "brave": "browser",
    # media / games (consumption)
    "vlc": "media", "spotify": "media", "netflix": "media", "mpc-hc64": "media",
    "steam": "media", "wmplayer": "media",
}


class RuleBasedImportanceScorer(ImportanceScorer):
    name = "rules-1.0"

    def __init__(
        self,
        *,
        dwell_min_seconds: float = 5.0,
        dwell_sat_seconds: float = 60.0,
        bias: float = -1.4,
        w_dwell: float = 2.0,
        w_revisit: float = 1.5,
        w_app: float = 1.5,
        app_categories: dict[str, str] | None = None,
        category_weights: dict[str, float] | None = None,
    ) -> None:
        if dwell_sat_seconds <= dwell_min_seconds:
            raise ValueError("dwell_sat_seconds must be greater than dwell_min_seconds")
        self.dwell_min = dwell_min_seconds
        self.dwell_sat = dwell_sat_seconds
        self.bias = bias
        self.w_dwell = w_dwell
        self.w_revisit = w_revisit
        self.w_app = w_app
        self.app_categories = {k.lower(): v for k, v in (app_categories or DEFAULT_APP_CATEGORIES).items()}
        self.category_weights = {**APP_CATEGORY_WEIGHTS, **(category_weights or {})}

    def predict_proba(self, features: Features) -> float:
        return _sigmoid(self._logit(features))

    def explain(self, features: Features) -> dict[str, Any]:
        """Per-rule contributions and the final probability, for auditing the baseline."""
        dwell_c = self._dwell_component(features)
        revisit_c = self._revisit_component(features)
        category = self.category_of(features.get("ctx_app"))
        app_c = self.category_weights.get(category, 0.0)
        logit = self.bias + self.w_dwell * dwell_c + self.w_revisit * revisit_c + self.w_app * app_c
        return {
            "dwell_component": round(dwell_c, 6),
            "revisit_component": round(revisit_c, 6),
            "app_category": category,
            "app_component": app_c,
            "contributions": {
                "bias": round(self.bias, 6),
                "dwell": round(self.w_dwell * dwell_c, 6),
                "revisit": round(self.w_revisit * revisit_c, 6),
                "app": round(self.w_app * app_c, 6),
            },
            "logit": round(logit, 6),
            "probability": round(_sigmoid(logit), 6),
        }

    def category_of(self, app: str | None) -> str:
        if not app:
            return "unknown"
        name = app.strip().lower()
        if name.endswith(".exe"):
            name = name[:-4]
        return self.app_categories.get(name, "unknown")

    # ------------------------------------------------------------------ rule components

    def _logit(self, features: Features) -> float:
        app_c = self.category_weights.get(self.category_of(features.get("ctx_app")), 0.0)
        return (
            self.bias
            + self.w_dwell * self._dwell_component(features)
            + self.w_revisit * self._revisit_component(features)
            + self.w_app * app_c
        )

    def _dwell_component(self, features: Features) -> float:
        dwell = float(features.get("tmp_dwell_seconds", 0.0) or 0.0)
        ramp = (dwell - self.dwell_min) / (self.dwell_sat - self.dwell_min)
        return _clip01(ramp)

    def _revisit_component(self, features: Features) -> float:
        return 1.0 if features.get("ctx_seen_before") else 0.0


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clip01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value
