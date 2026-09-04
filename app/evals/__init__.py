"""FundOps AI quality benchmark service."""

from .runner import (
    DEFAULT_OUTPUT_PATH,
    FIXTURE_LABEL,
    MODEL_LABEL,
    EvaluationConfig,
    run_evaluation,
    write_evaluation_results,
)
from .schema import DEFAULT_DATASET_PATH, GoldDatasetError, load_gold_dataset
from .service import (
    EvaluationResultsError,
    frontend_evaluation_summary,
    load_evaluation_results,
)

__all__ = [
    "DEFAULT_DATASET_PATH",
    "DEFAULT_OUTPUT_PATH",
    "FIXTURE_LABEL",
    "MODEL_LABEL",
    "EvaluationConfig",
    "EvaluationResultsError",
    "GoldDatasetError",
    "frontend_evaluation_summary",
    "load_evaluation_results",
    "load_gold_dataset",
    "run_evaluation",
    "write_evaluation_results",
]
