"""Turn a feature dict from ``assemble_features`` into a fixed numeric row for the model.

LightGBM needs a numeric matrix, but the feature dict mixes numbers, strings (``ctx_app``,
``ctx_domain``), and token lists (``ctx_title_tokens``). This vectorizer pins an explicit, ordered
column set so training and inference always produce the same layout:

- numeric/flag features pass through as-is;
- the foreground app is reduced to a one-hot over interpretable categories (reusing the
  rule-based category map), so no learned string vocabulary has to be persisted;
- free-text/id fields (domain, title tokens, nearest-prototype label) are intentionally dropped —
  they are high-cardinality and belong to later phases (tokenization/retrieval), which are out of
  scope here.

The column list is saved with each model version, so a loaded model rebuilds the identical layout.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from .rule_based import DEFAULT_APP_CATEGORIES

# Numeric features passed straight through, in this fixed order.
NUMERIC_FEATURES: tuple[str, ...] = (
    "beh_key_presses",
    "beh_mouse_clicks",
    "beh_mouse_scrolls",
    "beh_mouse_moves",
    "beh_window_seconds",
    "beh_interactions_per_second",
    "beh_app_switches",
    "beh_app_switch_rate_per_min",
    "beh_distinct_apps",
    "tmp_dwell_seconds",
    "tmp_hour_of_day",
    "tmp_tod_sin",
    "tmp_tod_cos",
    "tmp_is_weekend",
    "tmp_is_idle",
    "tmp_time_since_last_save_seconds",
    "tmp_has_prior_save",
    "ctx_title_token_count",
    "ctx_excluded",
    "ctx_seen_before",
    "ctx_revisit_count",
    "ctx_revisit_ratio",
    "vis_proto_similarity",
    "vis_proto_distance",
    "vis_has_prototypes",
    "vis_novelty",
    "vis_max_saved_similarity",
    "vis_has_saved",
)

# One-hot app categories (order fixed). Must match rule_based.APP_CATEGORY_WEIGHTS keys.
APP_CATEGORIES: tuple[str, ...] = ("productivity", "communication", "browser", "media", "unknown")
_APP_CATEGORY_COLUMNS: tuple[str, ...] = tuple(f"app_cat_{c}" for c in APP_CATEGORIES)

FEATURE_COLUMNS: tuple[str, ...] = NUMERIC_FEATURES + _APP_CATEGORY_COLUMNS


def category_of(app: str | None, app_categories: Mapping[str, str] = DEFAULT_APP_CATEGORIES) -> str:
    if not app:
        return "unknown"
    name = app.strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return app_categories.get(name, "unknown")


def vectorize(features: Mapping[str, Any], extra_columns: tuple[str, ...] = ()) -> np.ndarray:
    """One feature dict -> 1-D float32 row aligned to :data:`FEATURE_COLUMNS` + ``extra_columns``.

    ``extra_columns`` are experimental numeric features appended after the pinned layout (missing
    keys become 0). The default layout is unchanged when none are given.
    """
    row = np.empty(len(FEATURE_COLUMNS) + len(extra_columns), dtype=np.float32)
    for i, key in enumerate(NUMERIC_FEATURES):
        row[i] = _as_float(features.get(key))
    category = category_of(features.get("ctx_app"))
    base = len(NUMERIC_FEATURES)
    for j, cat in enumerate(APP_CATEGORIES):
        row[base + j] = 1.0 if cat == category else 0.0
    for k, key in enumerate(extra_columns):
        row[len(FEATURE_COLUMNS) + k] = _as_float(features.get(key))
    return row


def vectorize_many(feature_dicts, extra_columns: tuple[str, ...] = ()) -> np.ndarray:
    """Stack many feature dicts into an (N, len(FEATURE_COLUMNS) + len(extra_columns)) matrix."""
    dicts = list(feature_dicts)
    if not dicts:
        return np.empty((0, len(FEATURE_COLUMNS) + len(extra_columns)), dtype=np.float32)
    return np.vstack([vectorize(d, extra_columns) for d in dicts])


def _as_float(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return float(value or 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
