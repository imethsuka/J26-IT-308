"""Adaptation.

Responsibility: delayed label construction and online adaptation of importance predictions.
- ``label_construction`` — turns aged, unlabelled context records into weak-supervision labels
  (revisit / continuation / injected retrieval signal), combined by the citable
  ``combine_importance_label`` rule.
- online / incremental adaptation (river-based learners, drift detection) will live here too.
"""

from .label_construction import (  # noqa: F401
    DEFAULT_LABEL_DELAY,
    LabelConstructor,
    LabelResult,
    LabelWeights,
    Timeline,
    combine_importance_label,
    context_key,
    label_pending_records,
)
from .prototype_learning import (  # noqa: F401
    PrototypeResult,
    fit_prototypes,
    has_prototypes,
    load_prototype_metadata,
    load_prototypes,
    prototype_learning,
    save_prototypes,
)
# Two distinct, independent drift components: concept drift on the labelled error stream, and
# label-free covariate drift on the input distribution. Kept separate on purpose.
from .drift_detection import ErrorDriftDetector  # noqa: F401
from .input_monitor import InputDistributionMonitor, InputDriftStatus  # noqa: F401
from .deployment import (  # noqa: F401
    ClassificationMetrics,
    DeploymentDecision,
    decide_deployment,
    evaluate_model,
    rollback_to,
    validate_and_maybe_deploy,
)
