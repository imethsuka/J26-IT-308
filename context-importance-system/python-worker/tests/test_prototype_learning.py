"""Tests for KMeans importance-prototype learning and per-version persistence.

The synthetic data check confirms recovered centroids are reasonable (they line up with the true
cluster directions); it is not a statistical guarantee.
"""

from __future__ import annotations

import numpy as np
import pytest

from adaptation.prototype_learning import (
    fit_prototypes,
    has_prototypes,
    load_prototype_metadata,
    load_prototypes,
    prototype_learning,
    save_prototypes,
)
from features.feature_assembly import SensingEvent, assemble_features


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def make_clustered_embeddings(centers: np.ndarray, per_cluster: int = 60, noise: float = 0.05, seed: int = 0):
    """Points scattered around each unit center, returned as unit vectors (like real embeddings)."""
    rng = np.random.default_rng(seed)
    rows = []
    truth = []
    for idx, center in enumerate(centers):
        for _ in range(per_cluster):
            point = center + rng.normal(0, noise, size=center.shape)
            rows.append(_unit(point.astype(np.float32)))
            truth.append(idx)
    X = np.array(rows, dtype=np.float32)
    return X, np.array(truth)


def three_unit_centers(dim: int = 16, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centers = rng.normal(0, 1, size=(3, dim)).astype(np.float32)
    return np.array([_unit(c) for c in centers], dtype=np.float32)


def best_match_similarity(true_centers: np.ndarray, learned: np.ndarray) -> np.ndarray:
    """For each true center, the cosine similarity of its nearest learned prototype."""
    sims = _unit_rows(true_centers) @ _unit_rows(learned).T
    return sims.max(axis=1)


def _unit_rows(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


# =========================================================================== recovery quality

def test_recovers_synthetic_cluster_centers() -> None:
    centers = three_unit_centers()
    X, _ = make_clustered_embeddings(centers, per_cluster=80, noise=0.05)

    centroids = prototype_learning(X, k=3, random_state=0)

    assert centroids.shape == (3, centers.shape[1])
    # Every true center has a learned prototype pointing almost the same way.
    sims = best_match_similarity(centers, centroids)
    assert np.all(sims > 0.97), sims
    # And the matching is one-to-one (3 distinct prototypes matched).
    assignment = (_unit_rows(centers) @ _unit_rows(centroids).T).argmax(axis=1)
    assert len(set(assignment.tolist())) == 3


def test_cluster_counts_are_balanced_for_balanced_data() -> None:
    centers = three_unit_centers()
    X, _ = make_clustered_embeddings(centers, per_cluster=50, noise=0.04)
    result = fit_prototypes(X, k=3, random_state=0)
    assert sum(result.counts) == 150
    assert all(c > 0 for c in result.counts)  # no empty prototype
    assert result.inertia >= 0.0


def test_centroids_are_unit_norm_when_spherical() -> None:
    centers = three_unit_centers()
    X, _ = make_clustered_embeddings(centers, per_cluster=40)
    centroids = prototype_learning(X, k=3)
    norms = np.linalg.norm(centroids, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)


def test_deterministic_for_fixed_random_state() -> None:
    centers = three_unit_centers()
    X, _ = make_clustered_embeddings(centers, per_cluster=40)
    a = prototype_learning(X, k=3, random_state=7)
    b = prototype_learning(X, k=3, random_state=7)
    np.testing.assert_array_equal(a, b)


# =========================================================================== edge cases

def test_k_equal_to_n_gives_one_prototype_per_point() -> None:
    X = _unit_rows(np.random.default_rng(0).normal(size=(4, 8)).astype(np.float32))
    centroids = prototype_learning(X, k=4)
    assert centroids.shape == (4, 8)


def test_k_greater_than_samples_raises() -> None:
    X = _unit_rows(np.random.default_rng(0).normal(size=(3, 8)).astype(np.float32))
    with pytest.raises(ValueError, match="cannot form"):
        prototype_learning(X, k=5)


def test_empty_embeddings_raise() -> None:
    with pytest.raises(ValueError, match="no embeddings"):
        prototype_learning(np.empty((0, 8), dtype=np.float32), k=2)


def test_k_below_one_raises() -> None:
    X = _unit_rows(np.random.default_rng(0).normal(size=(3, 8)).astype(np.float32))
    with pytest.raises(ValueError, match="k must be"):
        prototype_learning(X, k=0)


# =========================================================================== persistence per version

def test_save_and_load_round_trip(tmp_path) -> None:
    centers = three_unit_centers()
    X, _ = make_clustered_embeddings(centers, per_cluster=30)
    result = fit_prototypes(X, k=3, random_state=0)

    save_prototypes(result, "0.1.0", versions_dir=tmp_path)
    assert has_prototypes("0.1.0", versions_dir=tmp_path)

    loaded = load_prototypes("0.1.0", versions_dir=tmp_path)
    np.testing.assert_array_equal(loaded, result.centroids)

    meta = load_prototype_metadata("0.1.0", versions_dir=tmp_path)
    assert meta["k"] == 3
    assert meta["dim"] == centers.shape[1]
    assert meta["n_important"] == 90
    assert meta["cluster_counts"] and sum(meta["cluster_counts"]) == 90


def test_save_is_append_only(tmp_path) -> None:
    centroids = prototype_learning(
        _unit_rows(np.random.default_rng(0).normal(size=(20, 8)).astype(np.float32)), k=2
    )
    save_prototypes(centroids, "1.0.0", versions_dir=tmp_path)
    with pytest.raises(FileExistsError):
        save_prototypes(centroids, "1.0.0", versions_dir=tmp_path)
    # Explicit overwrite is allowed.
    save_prototypes(centroids, "1.0.0", versions_dir=tmp_path, overwrite=True)


def test_load_missing_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_prototypes("nope", versions_dir=tmp_path)


def test_save_bare_array_records_shape_metadata(tmp_path) -> None:
    centroids = np.ones((2, 8), dtype=np.float32)
    save_prototypes(centroids, "0.2.0", versions_dir=tmp_path, metadata={"source_model": "lightgbm-0.2.0"})
    meta = load_prototype_metadata("0.2.0", versions_dir=tmp_path)
    assert meta["k"] == 2 and meta["dim"] == 8
    assert meta["source_model"] == "lightgbm-0.2.0"


# =========================================================================== integration with features

def test_learned_prototypes_feed_assemble_features(tmp_path) -> None:
    centers = three_unit_centers(dim=32)
    X, truth = make_clustered_embeddings(centers, per_cluster=40, noise=0.03)
    centroids = prototype_learning(X, k=3, random_state=0)

    # An embedding drawn from cluster 0 should be highly similar to its nearest prototype.
    sample = X[truth == 0][0]
    feats = assemble_features(
        SensingEvent(ts=__import__("datetime").datetime(2026, 9, 24), app="code"),
        embedding=sample,
        recent_context_history=[],
        prototypes=centroids,  # 2-D numpy array passed directly
    )
    assert feats["vis_has_prototypes"] == 1
    assert feats["vis_proto_similarity"] > 0.9
    assert feats["vis_nearest_prototype"] in (0, 1, 2)
