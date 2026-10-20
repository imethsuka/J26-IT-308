import pytest

from research.data.placeholder_labels import PLACEHOLDER_DATASET_NAME
from research.data.placeholder_scenarios import generate
from research.detection import Detector
from research.filters import build_filters
from research.model.training import train_model


@pytest.fixture(scope="session")
def detector() -> Detector:
    return Detector.default()


@pytest.fixture(scope="session")
def model(detector):
    # Small training set: enough to exercise the pipeline, quick to fit.
    m, _ = train_model(generate(150, 3, test=False), detector, PLACEHOLDER_DATASET_NAME)
    return m


@pytest.fixture(scope="session")
def filters(model, detector):
    return build_filters(model, detector)
