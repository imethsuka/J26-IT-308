"""Unit tests for feature assembly, with synthetic history and embeddings.

Focus (per spec) is the edges of revisit and novelty scoring:
  * first-ever context (empty history / empty ring),
  * exact repeat,
  * completely novel context.
Dimension-agnostic short vectors are used so no model or weights are needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from features.feature_assembly import (
    ContextHistoryEntry,
    SavedEmbeddingRing,
    SensingEvent,
    assemble_features,
)

T0 = datetime(2026, 9, 24, 14, 0, 0, tzinfo=timezone.utc)


def event(app="chrome", title="Docs - Example", domain="docs.example.com", ts=T0, **kw) -> SensingEvent:
    return SensingEvent(ts=ts, app=app, title=title, domain=domain, **kw)


def hist(app, ts, title="", domain="") -> ContextHistoryEntry:
    return ContextHistoryEntry(ts=ts, app=app, title=title, domain=domain)


# --------------------------------------------------------------------------- revisit edges

def test_first_ever_context_is_not_a_revisit() -> None:
    feats = assemble_features(event(), embedding=None, recent_context_history=[])
    assert feats["ctx_seen_before"] == 0
    assert feats["ctx_revisit_count"] == 0
    assert feats["ctx_revisit_ratio"] == 0.0


def test_exact_repeat_counts_prior_visits() -> None:
    history = [
        hist("chrome", T0 - timedelta(minutes=5), title="Docs - Example", domain="docs.example.com"),
        hist("code", T0 - timedelta(minutes=3), title="main.py"),
        hist("chrome", T0 - timedelta(minutes=1), title="Docs - Example", domain="docs.example.com"),
    ]
    # The visit that began at -1 min is still in progress at T0; the one at -5 min is a prior visit.
    feats = assemble_features(event(), embedding=None, recent_context_history=history)
    assert feats["ctx_seen_before"] == 1
    assert feats["ctx_revisit_count"] == 1
    assert feats["ctx_revisit_ratio"] == round(1 / 2, 4)  # 1 of 2 earlier visits (docs, code)


def test_consecutive_captures_of_one_window_are_not_a_revisit() -> None:
    # The collector sends a snapshot on every screen change: one stretch in a window is one visit.
    history = [
        hist("chrome", T0 - timedelta(seconds=s), title="Docs - Example", domain="docs.example.com")
        for s in (40, 30, 20, 10)
    ]
    feats = assemble_features(event(), embedding=None, recent_context_history=history)
    assert feats["ctx_seen_before"] == 0
    assert feats["ctx_revisit_count"] == 0
    assert feats["ctx_revisit_ratio"] == 0.0


def test_a_multi_frame_earlier_visit_counts_once() -> None:
    history = [
        hist("chrome", T0 - timedelta(minutes=10), title="Docs - Example", domain="docs.example.com"),
        hist("chrome", T0 - timedelta(minutes=9), title="Docs - Example", domain="docs.example.com"),
        hist("code", T0 - timedelta(minutes=5), title="main.py"),
    ]
    feats = assemble_features(event(), embedding=None, recent_context_history=history)
    assert feats["ctx_revisit_count"] == 1


def test_same_context_after_a_long_gap_is_a_new_visit() -> None:
    # No switch in between, but over VISIT_GAP (30 min) apart: the earlier stretch was its own visit.
    history = [hist("chrome", T0 - timedelta(minutes=45), title="Docs - Example", domain="docs.example.com")]
    feats = assemble_features(event(), embedding=None, recent_context_history=history)
    assert feats["ctx_seen_before"] == 1


def test_revisit_key_is_case_and_whitespace_insensitive() -> None:
    history = [
        hist("Chrome", T0 - timedelta(minutes=2), title="  docs - example  ", domain="Docs.Example.com"),
        hist("code", T0 - timedelta(minutes=1), title="main.py"),
    ]
    feats = assemble_features(event(), embedding=None, recent_context_history=history)
    assert feats["ctx_revisit_count"] == 1


def test_different_title_is_not_a_revisit() -> None:
    history = [hist("chrome", T0 - timedelta(minutes=1), title="Other page", domain="docs.example.com")]
    feats = assemble_features(event(), embedding=None, recent_context_history=history)
    assert feats["ctx_seen_before"] == 0


def test_excluded_context_is_never_a_revisit() -> None:
    history = [hist(None, T0 - timedelta(minutes=1))]
    ev = SensingEvent(ts=T0, excluded=True)
    feats = assemble_features(ev, embedding=None, recent_context_history=history)
    assert feats["ctx_excluded"] == 1
    assert feats["ctx_seen_before"] == 0


# --------------------------------------------------------------------------- novelty edges

def test_novelty_is_max_when_nothing_saved_yet() -> None:
    feats = assemble_features(event(), embedding=[1.0, 0.0, 0.0], recent_context_history=[])
    assert feats["vis_has_saved"] == 0
    assert feats["vis_novelty"] == 1.0
    assert feats["vis_max_saved_similarity"] == 0.0


def test_exact_repeat_embedding_has_zero_novelty() -> None:
    ring = SavedEmbeddingRing()
    ring.add([3.0, 0.0, 0.0], T0 - timedelta(minutes=1))  # stored normalized -> unit x
    feats = assemble_features(event(), embedding=[10.0, 0.0, 0.0], recent_context_history=[], saved_embeddings=ring)
    assert feats["vis_has_saved"] == 1
    assert feats["vis_max_saved_similarity"] == 1.0
    assert feats["vis_novelty"] == 0.0


def test_completely_novel_embedding_has_full_novelty() -> None:
    ring = SavedEmbeddingRing()
    ring.add([1.0, 0.0, 0.0], T0 - timedelta(minutes=1))
    # Orthogonal to everything saved.
    feats = assemble_features(event(), embedding=[0.0, 1.0, 0.0], recent_context_history=[], saved_embeddings=ring)
    assert feats["vis_max_saved_similarity"] == 0.0
    assert feats["vis_novelty"] == 1.0


def test_opposite_embedding_is_clipped_to_full_novelty() -> None:
    ring = SavedEmbeddingRing()
    ring.add([1.0, 0.0, 0.0], T0 - timedelta(minutes=1))
    feats = assemble_features(event(), embedding=[-1.0, 0.0, 0.0], recent_context_history=[], saved_embeddings=ring)
    assert feats["vis_max_saved_similarity"] == -1.0
    assert feats["vis_novelty"] == 1.0  # clipped from 1 - (-1) = 2


def test_novelty_uses_the_nearest_saved_embedding() -> None:
    ring = SavedEmbeddingRing()
    ring.add([1.0, 0.0, 0.0], T0 - timedelta(minutes=5))
    ring.add([0.0, 1.0, 0.0], T0 - timedelta(minutes=1))
    # Closer to the second saved vector (cos 0.8) than the first (cos 0.6).
    feats = assemble_features(event(), embedding=[0.6, 0.8, 0.0], recent_context_history=[], saved_embeddings=ring)
    assert feats["vis_max_saved_similarity"] == 0.8
    assert feats["vis_novelty"] == round(1.0 - 0.8, 6)


def test_ring_capacity_evicts_oldest() -> None:
    ring = SavedEmbeddingRing(capacity=2)
    ring.add([1.0, 0.0, 0.0], T0 - timedelta(minutes=3))
    ring.add([0.0, 1.0, 0.0], T0 - timedelta(minutes=2))
    ring.add([0.0, 0.0, 1.0], T0 - timedelta(minutes=1))  # evicts the first
    # The evicted [1,0,0] no longer contributes, so a query along x is fully novel again.
    feats = assemble_features(event(), embedding=[1.0, 0.0, 0.0], recent_context_history=[], saved_embeddings=ring)
    assert len(ring) == 2
    assert feats["vis_max_saved_similarity"] == 0.0
    assert feats["vis_novelty"] == 1.0


# --------------------------------------------------------------------------- prototype (visual) edges

def test_no_prototypes_at_cold_start_gives_neutral_similarity() -> None:
    feats = assemble_features(event(), embedding=[1.0, 0.0, 0.0], recent_context_history=[])
    assert feats["vis_has_prototypes"] == 0
    assert feats["vis_proto_similarity"] == 0.0
    assert feats["vis_proto_distance"] == 1.0
    assert feats["vis_nearest_prototype"] is None


def test_prototype_similarity_picks_nearest_and_reports_label() -> None:
    prototypes = [
        ("email", [1.0, 0.0, 0.0]),
        ("coding", [0.0, 1.0, 0.0]),
    ]
    feats = assemble_features(
        event(), embedding=[0.2, 1.0, 0.0], recent_context_history=[], prototypes=prototypes
    )
    assert feats["vis_has_prototypes"] == 1
    assert feats["vis_nearest_prototype"] == "coding"
    assert feats["vis_proto_similarity"] == round(1.0 / np.sqrt(1.04), 6)


def test_prototypes_without_labels_use_index() -> None:
    feats = assemble_features(
        event(), embedding=[1.0, 0.0], recent_context_history=[], prototypes=[[1.0, 0.0], [0.0, 1.0]]
    )
    assert feats["vis_nearest_prototype"] == 0
    assert feats["vis_proto_similarity"] == 1.0


def test_embedding_none_disables_visual_but_keeps_flags() -> None:
    feats = assemble_features(event(), embedding=None, recent_context_history=[], prototypes=[[1.0, 0.0]])
    assert feats["vis_has_prototypes"] == 0
    assert feats["vis_novelty"] == 1.0
    assert feats["vis_proto_similarity"] == 0.0


def test_raw_embedding_is_not_leaked_into_features() -> None:
    ring = SavedEmbeddingRing()
    ring.add([1.0, 0.0, 0.0], T0)
    feats = assemble_features(
        event(), embedding=[0.0, 1.0, 0.0], recent_context_history=[],
        prototypes=[[1.0, 0.0, 0.0]], saved_embeddings=ring,
    )
    for value in feats.values():
        assert not isinstance(value, (list, np.ndarray)) or all(isinstance(v, str) for v in value)


# --------------------------------------------------------------------------- behavioural + temporal

def test_app_switch_rate_over_history() -> None:
    history = [
        hist("chrome", T0 - timedelta(minutes=4)),
        hist("code", T0 - timedelta(minutes=3)),
        hist("chrome", T0 - timedelta(minutes=2)),
    ]
    # apps over time: chrome, code, chrome, then current chrome -> 2 switches across 4 minutes.
    feats = assemble_features(event(app="chrome"), embedding=None, recent_context_history=history)
    assert feats["beh_app_switches"] == 2
    assert feats["beh_distinct_apps"] == 2
    assert feats["beh_app_switch_rate_per_min"] == round(2 / 4, 4)


def test_behavioural_counts_and_interaction_rate() -> None:
    ev = event(key_presses=20, mouse_clicks=5, mouse_scrolls=5, mouse_moves=100, window_seconds=10)
    feats = assemble_features(ev, embedding=None, recent_context_history=[])
    assert feats["beh_key_presses"] == 20
    assert feats["beh_interactions_per_second"] == round(30 / 10, 4)


def test_time_since_last_save_absent_then_present() -> None:
    cold = assemble_features(event(), embedding=None, recent_context_history=[], saved_embeddings=SavedEmbeddingRing())
    assert cold["tmp_has_prior_save"] == 0
    assert cold["tmp_time_since_last_save_seconds"] == -1.0

    ring = SavedEmbeddingRing()
    ring.add([1.0, 0.0, 0.0], T0 - timedelta(seconds=90))
    warm = assemble_features(event(ts=T0), embedding=None, recent_context_history=[], saved_embeddings=ring)
    assert warm["tmp_has_prior_save"] == 1
    assert warm["tmp_time_since_last_save_seconds"] == 90.0


def test_save_without_embedding_still_updates_time_since_last_save() -> None:
    ring = SavedEmbeddingRing()
    ring.add([1.0, 0.0, 0.0], T0 - timedelta(seconds=90))
    ring.mark_saved(T0 - timedelta(seconds=30))  # a later save that had no frame/embedding
    feats = assemble_features(event(ts=T0), embedding=[1.0, 0.0, 0.0], recent_context_history=[], saved_embeddings=ring)
    assert feats["tmp_time_since_last_save_seconds"] == 30.0
    assert len(ring) == 1                       # novelty still only sees real embeddings
    assert feats["vis_max_saved_similarity"] == 1.0


def test_from_snapshot_adapter_reads_wire_shape() -> None:
    class FakeSnapshot:
        ts = T0
        context = {"app": "chrome", "title": "Docs", "domain": "docs.example.com", "excluded": False, "dwell_seconds": 4.0}
        activity = {"window_seconds": 10, "key_presses": 3, "mouse_clicks": 1, "mouse_scrolls": 0, "mouse_moves": 9}
        idle = {"is_idle": False, "idle_seconds": 0.0}

    feats = assemble_features(FakeSnapshot(), embedding=None, recent_context_history=[])
    assert feats["ctx_app"] == "chrome"
    assert feats["tmp_dwell_seconds"] == 4.0
    assert feats["beh_key_presses"] == 3
