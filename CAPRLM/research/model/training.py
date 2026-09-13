"""Turn labelled scenarios into (candidate, context) -> tier training pairs, and train/save/load
the default model. The dataset used is whatever scenarios are passed in; the default entry
points use the PLACEHOLDER set, and say so in the saved model's `trained_on`."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from research.data.placeholder_labels import PLACEHOLDER_DATASET_NAME
from research.data.placeholder_scenarios import Scenario, generate
from research.detection import Detector

from .risk_model import Pair, RiskModel, select_model

log = logging.getLogger(__name__)

# CAPRLM_MODEL_PATH lets the installed app keep the model in the user's data folder instead of next to the program.
ARTIFACT_PATH = Path(os.environ.get("CAPRLM_MODEL_PATH")
                     or Path(__file__).resolve().parent.parent / "artifacts" / "caprlm_model.joblib")

DEFAULT_N_TRAIN = 600
DEFAULT_SEED = 7


def build_pairs(scenarios: list[Scenario], detector: Detector) -> tuple[list[Pair], list[str]]:
    """Run detection, then label each detected span with the tier of the gold span it
    overlaps. A detected span overlapping no gold span is labelled "none": the scenario
    generator knows every sensitive entity it put in the text."""
    pairs: list[Pair] = []
    tiers: list[str] = []
    for sc in scenarios:
        gold = []
        for g in sc.gold:
            i = sc.text.find(g.surface)
            gold.append((i, i + len(g.surface), g.tier))
        for cand in detector.detect(sc.text):
            tier = next((t for a, b, t in gold if cand.start < b and a < cand.end), "none")
            pairs.append((cand, sc.ctx))
            tiers.append(tier)
    return pairs, tiers


def train_model(scenarios: list[Scenario], detector: Detector, dataset_name: str) -> tuple[RiskModel, dict]:
    pairs, tiers = build_pairs(scenarios, detector)
    config, selection = select_model(pairs, tiers)
    model = RiskModel(config).fit(pairs, tiers, trained_on=dataset_name)
    return model, {**selection, "n_training_spans": len(pairs)}


def train_default(detector: Detector) -> tuple[RiskModel, dict]:
    scenarios = generate(DEFAULT_N_TRAIN, DEFAULT_SEED, test=False)
    return train_model(scenarios, detector, PLACEHOLDER_DATASET_NAME)


def load_or_train_default(detector: Detector, path: Path = ARTIFACT_PATH) -> RiskModel:
    if path.exists():
        return RiskModel.load(path)
    log.warning("No saved model at %s — training one on the PLACEHOLDER dataset", path)
    model, _ = train_default(detector)
    model.save(path)
    return model
