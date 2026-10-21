"""Importance prototypes via KMeans over embeddings of important contexts.

The visual feature ``vis_proto_similarity`` (see ``features.feature_assembly``) is the cosine
similarity of a frame's embedding to the nearest *importance prototype*. This module learns those
prototypes: cluster the embeddings of contexts that turned out important (from label construction)
and take the cluster centroids.

MobileCLIP embeddings live on the unit sphere and are compared by cosine similarity, so this uses
*spherical* KMeans: inputs are L2-normalized before clustering and centroids are re-normalized to
unit length afterwards, so a centroid is itself a valid direction for cosine comparison.

Prototypes are persisted *per model version* alongside the LightGBM model, in
``models/versions/<version_id>/``, so a rolled-back model reloads the prototypes it was evaluated
with. Persistence never overwrites an existing set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

# Must match models.lightgbm_model.DEFAULT_VERSIONS_DIR (kept independent to avoid importing
# lightgbm/pandas just to load prototypes).
from app_paths import versions_dir as _versions_dir

DEFAULT_VERSIONS_DIR = _versions_dir()

PROTOTYPES_ARRAY_FILE = "prototypes.npy"
PROTOTYPES_META_FILE = "prototypes.json"


@dataclass
class PrototypeResult:
    """Full KMeans result. ``centroids`` are the importance prototypes."""

    centroids: np.ndarray            # (k, dim) float32, unit-norm when spherical
    labels: np.ndarray               # (n,) cluster index per input embedding
    inertia: float                   # KMeans objective (lower = tighter clusters)
    counts: list[int]                # samples assigned to each cluster
    k: int
    dim: int
    n_samples: int
    random_state: int
    spherical: bool


def fit_prototypes(
    embeddings_of_important_contexts: Sequence[Sequence[float]] | np.ndarray,
    k: int,
    *,
    random_state: int = 0,
    spherical: bool = True,
    n_init: int = 10,
) -> PrototypeResult:
    """
    Cluster important-context embeddings into ``k`` prototypes and return the full result.

    Raises ``ValueError`` if there are no embeddings, if ``k < 1``, or if there are fewer embeddings
    than ``k`` (KMeans cannot form more clusters than samples — the caller should lower ``k`` or
    collect more important contexts before learning prototypes).
    """
    from sklearn.cluster import KMeans

    X = np.asarray(embeddings_of_important_contexts, dtype=np.float32)
    if X.ndim != 2:
        raise ValueError(f"embeddings must be a 2-D array (n, dim), got shape {X.shape}")
    n_samples, dim = X.shape
    if n_samples == 0:
        raise ValueError("no embeddings provided")
    if k < 1:
        raise ValueError("k must be >= 1")
    if k > n_samples:
        raise ValueError(
            f"cannot form {k} prototypes from {n_samples} embeddings; "
            "lower k or gather more important contexts"
        )

    fitted = _normalize_rows(X) if spherical else X

    kmeans = KMeans(n_clusters=k, random_state=random_state, n_init=n_init)
    labels = kmeans.fit_predict(fitted)
    centroids = kmeans.cluster_centers_.astype(np.float32)
    if spherical:
        centroids = _normalize_rows(centroids)

    counts = np.bincount(labels, minlength=k).tolist()
    return PrototypeResult(
        centroids=centroids,
        labels=labels.astype(np.int32),
        inertia=float(kmeans.inertia_),
        counts=counts,
        k=k,
        dim=dim,
        n_samples=n_samples,
        random_state=random_state,
        spherical=spherical,
    )


def prototype_learning(
    embeddings_of_important_contexts: Sequence[Sequence[float]] | np.ndarray,
    k: int,
    *,
    random_state: int = 0,
    spherical: bool = True,
) -> np.ndarray:
    """Learn importance prototypes and return just the centroids as an ``(k, dim)`` float32 array.

    The returned array is directly usable as the ``prototypes`` argument of
    ``features.feature_assembly.assemble_features``.
    """
    return fit_prototypes(
        embeddings_of_important_contexts, k, random_state=random_state, spherical=spherical
    ).centroids


# =============================================================================================
# Persistence (per model version, append-only)
# =============================================================================================

def save_prototypes(
    prototypes: np.ndarray | PrototypeResult,
    version_id: str,
    *,
    versions_dir: Path | str = DEFAULT_VERSIONS_DIR,
    metadata: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> Path:
    """
    Persist prototypes under ``versions_dir/<version_id>/``. Creates the version directory if
    needed and, unless ``overwrite`` is set, refuses to replace an existing prototype set so
    history is preserved for rollback. Returns the path to the saved array.
    """
    if isinstance(prototypes, PrototypeResult):
        centroids = prototypes.centroids
        derived_meta = {
            "k": prototypes.k,
            "dim": prototypes.dim,
            "n_important": prototypes.n_samples,
            "inertia": round(prototypes.inertia, 6),
            "cluster_counts": prototypes.counts,
            "spherical": prototypes.spherical,
            "random_state": prototypes.random_state,
        }
    else:
        centroids = np.asarray(prototypes, dtype=np.float32)
        derived_meta = {"k": int(centroids.shape[0]), "dim": int(centroids.shape[1] if centroids.ndim == 2 else 0)}

    if centroids.ndim != 2:
        raise ValueError(f"centroids must be 2-D (k, dim), got shape {centroids.shape}")

    target = Path(versions_dir) / version_id
    target.mkdir(parents=True, exist_ok=True)
    array_path = target / PROTOTYPES_ARRAY_FILE
    if array_path.exists() and not overwrite:
        raise FileExistsError(
            f"prototypes already exist for version {version_id!r} at {array_path}; "
            "prototypes are not overwritten (pass overwrite=True to force)"
        )

    np.save(array_path, centroids.astype(np.float32))
    meta = {
        "version_id": version_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **derived_meta,
        **(metadata or {}),
    }
    (target / PROTOTYPES_META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return array_path


def load_prototypes(version_id: str, *, versions_dir: Path | str = DEFAULT_VERSIONS_DIR) -> np.ndarray:
    """Load the centroids saved for ``version_id``. Raises ``FileNotFoundError`` if absent."""
    array_path = Path(versions_dir) / version_id / PROTOTYPES_ARRAY_FILE
    if not array_path.is_file():
        raise FileNotFoundError(f"no prototypes for version {version_id!r} at {array_path}")
    return np.load(array_path).astype(np.float32)


def load_prototype_metadata(version_id: str, *, versions_dir: Path | str = DEFAULT_VERSIONS_DIR) -> dict[str, Any]:
    meta_path = Path(versions_dir) / version_id / PROTOTYPES_META_FILE
    if not meta_path.is_file():
        raise FileNotFoundError(f"no prototype metadata for version {version_id!r} at {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def has_prototypes(version_id: str, *, versions_dir: Path | str = DEFAULT_VERSIONS_DIR) -> bool:
    return (Path(versions_dir) / version_id / PROTOTYPES_ARRAY_FILE).is_file()


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0  # leave any all-zero row untouched instead of dividing by zero
    return (matrix / norms).astype(np.float32)
