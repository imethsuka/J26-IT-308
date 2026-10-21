"""Offline Model 1 training: captures -> embeddings -> features -> labels -> split -> train -> evaluate.

The notebooks in ``../Colab/`` call these functions step by step. Each step mirrors a live
component, so the trained model is valid for the live worker:

1. :func:`embed_captures`    MobileCLIP-S0 image embeddings, cached on disk (the slow step).
2. :func:`build_dataset`     ``assemble_features`` replayed in time order, exactly like
                             ``pipeline.SnapshotPipeline`` (rule-based decisions drive the novelty
                             ring and time-since-last-save, as they would have live).
3. :func:`add_labels`        the delayed weak-supervision rule (``adaptation.label_construction``).
4. :func:`temporal_split`    oldest rows train, newest rows test, with a label-horizon embargo so a
                             training label never encodes test-period behaviour.
5. :func:`learn_prototypes` + :func:`with_prototypes`
                             prototypes from the *training* split only, then every row's
                             ``vis_proto_*`` features recomputed against them. Without this, every
                             row has the cold-start similarity 0.0 and Model 1 cannot learn from
                             MobileCLIP at all.
6. :func:`train_model1`      LightGBM (``models.lightgbm_model.ImportanceModel``).
7. :func:`evaluate_models` / :func:`threshold_curve`
                             precision/recall/F1 and storage saved vs keeping every screenshot.
8. :func:`save_version` / :func:`register_version`
                             append-only model + prototype versions, optionally deployed to a
                             ``context.db`` through the same strictly-better gate as the scheduled job.

Bundles (:func:`export_bundle` / :func:`load_bundle`) carry a labelled dataset to Colab without
images, window titles, or domains.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from adaptation.label_construction import DEFAULT_LABEL_DELAY, DEFAULT_LABEL_HORIZON, LabelConstructor
from features.feature_assembly import ContextHistoryEntry, SavedEmbeddingRing, assemble_features, prototype_features
from models.base import ImportanceScorer
from models.rule_based import RuleBasedImportanceScorer

EMBED_DIM = 512
DEFAULT_HISTORY_SIZE = 200          # pipeline.DEFAULT_HISTORY_SIZE
DEFAULT_SAVE_RING_CAPACITY = 32     # pipeline.DEFAULT_SAVE_RING_CAPACITY
DEFAULT_THRESHOLD = 0.5
# Feature keys that carry raw text; dropped from exported bundles (the model never uses them).
TEXT_FEATURE_KEYS = ("ctx_title_tokens", "ctx_domain", "vis_nearest_prototype")


# =============================================================================================
# 1. Embeddings (cached)
# =============================================================================================

def embed_captures(
    captures: Sequence[Any],
    cache_path: Path | str,
    *,
    encoder: Any = None,
    checkpoint: str | None = None,
    device: str = "cpu",
    save_every: int = 200,
    progress: bool = True,
) -> dict[str, np.ndarray]:
    """Return ``{screenshot_id: 512-d unit vector}`` for every capture, embedding only what's missing.

    The cache is an ``.npz`` of ids + a float32 matrix, saved every ``save_every`` new embeddings, so
    an interrupted run resumes where it stopped. Unreadable images are skipped (no embedding).
    """
    from PIL import Image

    cache_path = Path(cache_path)
    cache = load_embedding_cache(cache_path)
    missing = [c for c in captures if c.screenshot_id not in cache]
    if missing:
        if encoder is None:
            from features.mobileclip_encoder import load_image_encoder

            encoder = load_image_encoder(checkpoint, device=device)
        iterator = _progress(missing, progress, "embedding")
        added = 0
        for capture in iterator:
            try:
                with Image.open(capture.image_path) as image:
                    cache[capture.screenshot_id] = encoder.embed_frame(image)
            except (OSError, ValueError) as exc:
                print(f"  skipped {capture.image_path}: {exc}")
                continue
            added += 1
            if added % save_every == 0:
                save_embedding_cache(cache_path, cache)
        save_embedding_cache(cache_path, cache)
    return cache


def load_embedding_cache(path: Path | str) -> dict[str, np.ndarray]:
    path = Path(path)
    if not path.is_file():
        return {}
    with np.load(path, allow_pickle=False) as data:
        return {str(i): v for i, v in zip(data["ids"], data["embeddings"])}


def save_embedding_cache(path: Path | str, cache: dict[str, np.ndarray]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = np.array(list(cache.keys()))
    matrix = np.vstack(list(cache.values())).astype(np.float32) if cache else np.empty((0, EMBED_DIM), np.float32)
    tmp = path.with_name(path.stem + ".tmp.npz")  # np.savez appends .npz unless already present
    np.savez(tmp, ids=ids, embeddings=matrix)
    tmp.replace(path)


# =============================================================================================
# 2. Features (replayed like the live pipeline)
# =============================================================================================

def build_dataset(
    captures: Sequence[Any],
    embeddings: dict[str, np.ndarray] | None,
    *,
    scorer: ImportanceScorer | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    history_size: int = DEFAULT_HISTORY_SIZE,
    ring_capacity: int = DEFAULT_SAVE_RING_CAPACITY,
    title_normalizer=None,
) -> pd.DataFrame:
    """One row per capture, in time order, with the feature dict the live worker would have built.

    ``scorer`` (default: the rule-based baseline the live worker serves at cold start) makes the
    save/discard decisions that feed the novelty ring, as it would have live. ``title_normalizer``
    (opt-in, experiments) normalizes titles for the revisit features, matching v4 labels.
    """
    scorer = scorer or RuleBasedImportanceScorer()
    history: deque[ContextHistoryEntry] = deque(maxlen=history_size)
    ring = SavedEmbeddingRing(capacity=ring_capacity)
    embeddings = embeddings or {}

    rows = []
    for index, capture in enumerate(sorted(captures, key=lambda c: c.ts)):
        event = capture.event
        embedding = embeddings.get(capture.screenshot_id)
        features = assemble_features(event, embedding, history, saved_embeddings=ring, title_normalizer=title_normalizer)
        proba = float(scorer.predict_proba(features))
        decision = "save" if proba >= threshold else "discard"
        if decision == "save":
            if embedding is not None:
                ring.add(embedding, event.ts)
            else:
                ring.mark_saved(event.ts)
        history.append(ContextHistoryEntry(ts=event.ts, app=event.app, title=event.title, domain=event.domain))
        rows.append({
            "id": index,
            "screenshot_id": capture.screenshot_id,
            "ts": event.ts,
            "session_id": getattr(capture, "session_id", None),
            "app": event.app,
            "domain": event.domain,
            "title": event.title,
            "capture_reason": getattr(capture, "capture_reason", None),
            "file_size": int(getattr(capture, "file_size", 0) or 0),
            "image_path": str(getattr(capture, "image_path", "")),
            "features": features,
            "embedding": embedding,
            "rule_proba": proba,
            "rule_decision": decision,
        })
    return pd.DataFrame(rows)


# =============================================================================================
# 3. Labels
# =============================================================================================

@dataclass
class _Rec:
    id: Any
    ts: datetime
    app: str | None
    domain: str | None
    title: str | None
    features: dict | None = None  # the KW label reads typing counts from here


def add_labels(
    df: pd.DataFrame,
    *,
    label_delay: timedelta = DEFAULT_LABEL_DELAY,
    label_horizon: timedelta = DEFAULT_LABEL_HORIZON,
    constructor: LabelConstructor | None = None,
    title_normalizer=None,
) -> pd.DataFrame:
    """Add ``label`` (1/0, or <NA> while censored), ``revisited``, ``continued``, ``label_version``.

    The reference time is the newest capture plus ``label_delay``, so the result depends only on the
    data, not on when the notebook is run. Records whose ``label_horizon`` reaches past the newest
    capture stay unlabelled, exactly as in the scheduled job. ``title_normalizer`` (opt-in) gives
    the v4 rule.
    """
    constructor = constructor or LabelConstructor(
        label_delay=label_delay, label_horizon=label_horizon, title_normalizer=title_normalizer
    )
    records = [_Rec(r.id, r.ts, _text(r.app), _text(r.domain), _text(r.title), r.features) for r in df.itertuples()]
    out = df.copy()
    if not records:
        return out.assign(label=pd.array([], dtype="Int64"), revisited=None, continued=None, label_version=None)
    now = max(r.ts for r in records) + constructor.label_delay
    results = dict(constructor.construct_batch(records, now=now))
    out["label"] = pd.array([results[i].label if i in results else None for i in out["id"]], dtype="Int64")
    out["revisited"] = [results[i].revisited if i in results else None for i in out["id"]]
    out["continued"] = [results[i].continued if i in results else None for i in out["id"]]
    out["label_version"] = [constructor.label_version if i in results else None for i in out["id"]]
    return out


# =============================================================================================
# 4. Split
# =============================================================================================

def temporal_split(
    df: pd.DataFrame,
    *,
    test_fraction: float = 0.3,
    embargo: timedelta = DEFAULT_LABEL_HORIZON,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Labelled rows only: oldest -> train, newest ``test_fraction`` -> test.

    Training rows whose label window ``(ts, ts + embargo]`` reaches into the test period are dropped,
    so no training label was decided by test-period behaviour (the same embargo as
    ``eval.model_comparison``).
    """
    labelled = df[df["label"].notna()].sort_values("ts").reset_index(drop=True)
    if len(labelled) < 2:
        raise ValueError("need at least 2 labelled rows to split")
    n_test = min(max(1, int(round(len(labelled) * test_fraction))), len(labelled) - 1)
    train, test = labelled.iloc[:-n_test], labelled.iloc[-n_test:]
    test_start = test["ts"].iloc[0]
    train = train[train["ts"] + embargo <= test_start]
    return train.reset_index(drop=True), test.reset_index(drop=True)


# =============================================================================================
# 5. Prototypes (train split only) + recomputed visual features
# =============================================================================================

def learn_prototypes(train_df: pd.DataFrame, *, k: int = 8, random_state: int = 0):
    """Spherical KMeans over the embeddings of *important* training rows. ``None`` if too few."""
    from adaptation.prototype_learning import fit_prototypes

    important = [e for e, y in zip(train_df["embedding"], train_df["label"]) if e is not None and y == 1]
    if len(important) < k:
        return None
    return fit_prototypes(np.vstack(important), k=k, random_state=random_state)


def with_prototypes(df: pd.DataFrame, prototypes: np.ndarray | None) -> pd.DataFrame:
    """Copy of ``df`` whose ``vis_proto_*`` features are recomputed against ``prototypes``."""
    out = df.copy()
    out["features"] = [
        {**features, **prototype_features(embedding, prototypes)}
        for features, embedding in zip(df["features"], df["embedding"])
    ]
    return out


# =============================================================================================
# 6. Train
# =============================================================================================

def train_model1(
    train_df: pd.DataFrame,
    *,
    params: dict | None = None,
    num_boost_round: int | None = None,
    extra_feature_columns: Sequence[str] = (),
):
    """Fit Model 1 (LightGBM) on ``features`` -> ``label``. Returns ``(model, training_summary)``.

    ``extra_feature_columns`` adds experimental numeric features (e.g. :func:`category_columns`)
    after the pinned layout; such a model is for offline experiments only.
    """
    from models.lightgbm_model import DEFAULT_NUM_BOOST_ROUND, ImportanceModel

    model = ImportanceModel(
        params=params, num_boost_round=num_boost_round or DEFAULT_NUM_BOOST_ROUND,
        extra_feature_columns=extra_feature_columns,
    )
    frame = pd.DataFrame({"features": list(train_df["features"]), "label": train_df["label"].astype(int).to_numpy()})
    summary = model.train(frame)
    return model, summary


# =============================================================================================
# 6b. Experiments: ablations, MobileCLIP screen categories, rolling evaluation
# =============================================================================================

def with_user_features(df: pd.DataFrame, profile) -> pd.DataFrame:
    """Copy of ``df`` whose ``features`` gain the ``usr_*`` columns learned by Model 2's user profile.

    Opt-in: train with ``extra_feature_columns=USER_FEATURE_COLUMNS``; such a model is for offline
    experiments, as the live worker does not compute them. Fit ``profile`` on the training split only.
    """
    from adaptation.user_profile import user_features

    ordered = df.sort_values("ts")
    extra = user_features(profile, ordered[["app", "features"]].to_dict("records"))
    by_index = dict(zip(ordered.index, extra))
    out = df.copy()
    out["features"] = [{**features, **by_index[i]} for i, features in zip(df.index, df["features"])]
    return out


def without_vision(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of ``df`` with every visual feature (``vis_*``, incl. categories) blanked: the "no eyes" ablation."""
    out = df.copy()
    out["features"] = [{**f, **{k: 0 for k in f if k.startswith("vis_")}} for f in df["features"]]
    return out


# Zero-shot screen categories: MobileCLIP compares each screenshot with these descriptions.
# Names become feature columns ``vis_cat_<name>``; edit prompts freely (they're encoded per run).
SCREEN_CATEGORIES: dict[str, str] = {
    "shopping": "a screenshot of an online shop product page with a price",
    "code": "a screenshot of source code in a code editor",
    "terminal": "a screenshot of a command line terminal",
    "document": "a screenshot of a text document or PDF",
    "spreadsheet": "a screenshot of a spreadsheet with rows and columns",
    "slides": "a screenshot of presentation slides",
    "chart": "a screenshot of a chart or graph",
    "diagram": "a screenshot of a diagram or flowchart",
    "video": "a screenshot of a video player",
    "chat": "a screenshot of a chat conversation",
    "email": "a screenshot of an email inbox",
    "social": "a screenshot of a social media feed",
    "search": "a screenshot of web search results",
    "files": "a screenshot of a file explorer window",
    "settings": "a screenshot of a settings window",
    "game": "a screenshot of a video game",
    "learning": "a screenshot of an online course or lecture page",
    "ai_chat": "a screenshot of a conversation with an AI assistant",
}


def category_columns(names: Sequence[str] = tuple(SCREEN_CATEGORIES)) -> tuple[str, ...]:
    return tuple(f"vis_cat_{n}" for n in names)


def category_features(embedding, text_matrix: np.ndarray, names: Sequence[str], *, temperature: float = 100.0) -> dict[str, float]:
    """Per-category probability that the screenshot shows that kind of screen (softmax over
    ``temperature`` x cosine similarity, CLIP's zero-shot recipe). All 0 when there is no embedding."""
    columns = category_columns(names)
    if embedding is None:
        return {c: 0.0 for c in columns}
    sims = text_matrix @ np.asarray(embedding, dtype=np.float32)
    logits = temperature * (sims - sims.max())
    probs = np.exp(logits) / np.exp(logits).sum()
    return {c: round(float(p), 6) for c, p in zip(columns, probs)}


def with_categories(df: pd.DataFrame, text_matrix: np.ndarray, names: Sequence[str] = tuple(SCREEN_CATEGORIES)) -> pd.DataFrame:
    """Copy of ``df`` with ``vis_cat_*`` features added. Uses no labels, so it can be applied to the
    whole dataset before splitting without leaking test information."""
    out = df.copy()
    out["features"] = [{**f, **category_features(e, text_matrix, names)} for f, e in zip(df["features"], df["embedding"])]
    return out


def rolling_evaluation(
    dataset: pd.DataFrame,
    configs: dict[str, Any],
    *,
    n_folds: int = 4,
    test_fraction: float = 0.5,
    embargo: timedelta = DEFAULT_LABEL_HORIZON,
    prototype_k: int = 8,
    threshold: float = DEFAULT_THRESHOLD,
    min_train: int = 200,
    keep_budgets: Sequence[float] = (0.3, 0.5),
) -> pd.DataFrame:
    """Expanding-window evaluation over several consecutive test periods instead of one split.

    The newest ``test_fraction`` of labelled rows is cut into ``n_folds`` consecutive blocks. For each
    block, every labelled row older than the block (minus the label-horizon ``embargo``) trains;
    prototypes are learned from that fold's training rows only. ``configs`` maps a name to
    ``make(train_df, test_df) -> (scorer, test_df_to_score)``. Returns one row per (fold, config),
    with the threshold-0.5 metrics plus :func:`ranking_metrics` (AUC, average precision, recall at
    each storage budget in ``keep_budgets``).
    """
    labelled = dataset[dataset["label"].notna()].sort_values("ts").reset_index(drop=True)
    first_test = len(labelled) - int(len(labelled) * test_fraction)
    bounds = np.linspace(first_test, len(labelled), n_folds + 1).astype(int)
    rows = []
    for fold in range(n_folds):
        test = labelled.iloc[bounds[fold]:bounds[fold + 1]]
        if test.empty:
            continue
        test_start = test["ts"].iloc[0]
        train = labelled[labelled["ts"] + embargo <= test_start]
        if len(train) < min_train or train["label"].nunique() < 2 or test["label"].nunique() < 2:
            print(f"fold {fold}: skipped ({len(train)} training rows, classes {sorted(train['label'].unique())})")
            continue
        proto = learn_prototypes(train, k=prototype_k)
        centroids = proto.centroids if proto is not None else None
        train_p, test_p = with_prototypes(train, centroids), with_prototypes(test, centroids)
        for name, make in configs.items():
            scorer, scored = make(train_p, test_p)
            metrics = evaluate_models(scored, {name: scorer}, threshold=threshold).iloc[0].to_dict()
            rows.append({
                "fold": fold, "train_rows": len(train), "test_rows": len(test),
                "test_from": test_start, "test_to": test["ts"].iloc[-1], **metrics,
                **ranking_metrics(scorer, scored, keep_budgets),
            })
    return pd.DataFrame(rows)


def ranking_metrics(scorer: ImportanceScorer, df: pd.DataFrame, keep_budgets: Sequence[float] = (0.3, 0.5)) -> dict[str, float]:
    """Threshold-free comparison: how well the scores *order* contexts, independent of 0.5.

    ``roc_auc`` / ``avg_precision`` summarise the whole ranking; ``recall_at_keep_X`` is the share of
    important contexts kept when only the top X of contexts may be stored (a storage budget). These
    are fair between models whose scores are calibrated differently (e.g. rules vs LightGBM).
    """
    from sklearn.metrics import average_precision_score, roc_auc_score

    y = df["label"].astype(int).to_numpy()
    scores = np.array([scorer.predict_proba(f) for f in df["features"]], dtype=float)
    out = {"roc_auc": float(roc_auc_score(y, scores)), "avg_precision": float(average_precision_score(y, scores))}
    order = np.argsort(-scores, kind="stable")
    for budget in keep_budgets:
        kept = order[: int(round(budget * len(y)))]
        out[f"recall_at_keep_{int(round(budget * 100))}"] = float(y[kept].sum() / max(1, y.sum()))
    return out


def summarize_folds(
    results: pd.DataFrame,
    metrics: Sequence[str] = ("roc_auc", "avg_precision", "recall_at_keep_30", "recall_at_keep_50", "f1", "keep_rate", "latency_mean_ms"),
    win_metric: str = "avg_precision",
) -> pd.DataFrame:
    """Mean and standard deviation of each metric across folds, one row per config, plus how many
    folds each config was best in on ``win_metric``."""
    metrics = [m for m in metrics if m in results.columns]
    summary = results.groupby("name", sort=False)[metrics].agg(["mean", "std"]).round(3)
    best = results.loc[results.groupby("fold")[win_metric].idxmax(), "name"].value_counts()
    summary[(win_metric, "folds_won")] = [int(best.get(n, 0)) for n in summary.index]
    return summary


# =============================================================================================
# 7. Evaluate
# =============================================================================================

def evaluate_models(
    test_df: pd.DataFrame,
    models: dict[str, ImportanceScorer],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    label_column: str = "label",
) -> pd.DataFrame:
    """Precision/recall/F1, keep rate, storage saved (by real screenshot bytes) and latency per model."""
    from eval.model_comparison import EvalRecord, compare

    rows = test_df[test_df[label_column].notna()]
    records = [
        EvalRecord(features=f, label=int(y), size_bytes=int(s) if s else None, ts=t)
        for f, y, s, t in zip(rows["features"], rows[label_column], rows["file_size"], rows["ts"])
    ]
    results = compare(records, models, threshold=threshold)
    table = pd.DataFrame([r.__dict__ for r in results])
    table.insert(0, "name", list(models.keys()))
    return table.drop(columns=["model"]).rename(columns={"save_rate": "keep_rate"})


def threshold_curve(
    test_df: pd.DataFrame,
    model: ImportanceScorer,
    *,
    thresholds: Iterable[float] = tuple(np.round(np.arange(0.05, 1.0, 0.05), 2)),
    label_column: str = "label",
) -> pd.DataFrame:
    """For each decision threshold: keep rate, precision, recall, F1, and storage saved."""
    rows = test_df[test_df[label_column].notna()]
    proba = np.array([model.predict_proba(f) for f in rows["features"]])
    y = rows[label_column].astype(int).to_numpy()
    sizes = rows["file_size"].to_numpy(dtype=float)
    total = sizes.sum() or 1.0
    out = []
    for t in thresholds:
        keep = proba >= t
        tp = int((keep & (y == 1)).sum())
        precision = tp / keep.sum() if keep.sum() else 0.0
        recall = tp / (y == 1).sum() if (y == 1).sum() else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out.append({
            "threshold": float(t), "keep_rate": float(keep.mean()), "precision": precision,
            "recall": recall, "f1": f1, "storage_saved": 1.0 - sizes[keep].sum() / total,
        })
    return pd.DataFrame(out)


# =============================================================================================
# 8. Save / register
# =============================================================================================

def new_version_id(prefix: str = "lightgbm") -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"


def save_version(model, prototypes, version_id: str, *, versions_dir: Path | str | None = None, notes: dict | None = None) -> Path:
    """Save Model 1 and its prototypes as one append-only version (never overwrites)."""
    from adaptation.prototype_learning import save_prototypes
    from models.lightgbm_model import DEFAULT_VERSIONS_DIR

    versions_dir = Path(versions_dir) if versions_dir else DEFAULT_VERSIONS_DIR
    model.versions_dir = versions_dir
    target = model.save(version_id)
    if prototypes is not None:
        save_prototypes(prototypes, version_id, versions_dir=versions_dir, metadata={"source": "offline-training"})
    if notes:
        (target / "training_notes.json").write_text(json.dumps(notes, indent=2, default=str), encoding="utf-8")
    return target


def register_version(db_path: Path | str, model, test_df: pd.DataFrame, version_id: str, *, threshold: float = DEFAULT_THRESHOLD, notes: str | None = None):
    """Record ``version_id`` in a ``context.db`` and make it active only if strictly better than
    what that DB currently serves (its active model, or the rule-based baseline at cold start).
    The live worker picks it up on its next start."""
    from adaptation.deployment import validate_and_maybe_deploy
    from pipeline import load_active_scorer
    from storage.database import connect, migrate

    conn = connect(db_path)
    try:
        migrate(conn)
        current, _ = load_active_scorer(conn, versions_dir=getattr(model, "versions_dir", None))
        held_out = pd.DataFrame({"features": list(test_df["features"]), "label": test_df["label"].astype(int).to_numpy()})
        return validate_and_maybe_deploy(
            model, current, held_out, conn=conn, candidate_version=version_id,
            threshold=threshold, algorithm="lightgbm", notes=notes,
        )
    finally:
        conn.close()


# =============================================================================================
# Bundles (for Colab): labelled rows, no images / titles / domains
# =============================================================================================

def export_bundle(df: pd.DataFrame, path: Path | str, *, participant: str) -> Path:
    """Write a privacy-reduced parquet: numeric features + app name, label, embedding, timestamps.

    Window titles, title tokens, domains and image paths are removed; the model never uses them.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = pd.DataFrame({
        "participant": participant,
        "screenshot_id": df["screenshot_id"],
        "ts": df["ts"],
        "session_id": df["session_id"],
        "file_size": df["file_size"],
        "label": df["label"],
        "revisited": df["revisited"],
        "continued": df["continued"],
        "label_version": df["label_version"],
        "features_json": [json.dumps({k: v for k, v in f.items() if k not in TEXT_FEATURE_KEYS}) for f in df["features"]],
        "embedding": [None if e is None else np.asarray(e, np.float32).tobytes() for e in df["embedding"]],
    })
    out.to_parquet(path, index=False)
    return path


def load_bundle(path_or_buffer) -> pd.DataFrame:
    """Inverse of :func:`export_bundle`, with the ``features``/``embedding`` columns training expects."""
    df = pd.read_parquet(path_or_buffer)
    df["features"] = [json.loads(s) for s in df.pop("features_json")]
    df["embedding"] = [None if b is None else np.frombuffer(b, dtype=np.float32) for b in df["embedding"]]
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["label"] = df["label"].astype("Int64")
    return df


def _text(value: Any) -> str | None:
    """pandas 3 stores missing text as NaN (a float); label construction expects None."""
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else value


def _progress(items, enabled: bool, desc: str):
    if not enabled:
        return items
    try:
        from tqdm.auto import tqdm

        return tqdm(items, desc=desc)
    except ImportError:
        return items


__all__: Sequence[str] = [
    "SCREEN_CATEGORIES", "add_labels", "build_dataset", "category_columns", "category_features",
    "embed_captures", "evaluate_models", "export_bundle", "rolling_evaluation", "summarize_folds",
    "ranking_metrics", "with_categories", "without_vision",
    "learn_prototypes", "load_bundle", "load_embedding_cache", "new_version_id", "register_version",
    "save_embedding_cache", "save_version", "temporal_split", "threshold_curve", "train_model1",
    "with_prototypes",
]
