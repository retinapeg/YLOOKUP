"""Stable service surface for CLI and frontend consumers of eval results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from .runner import DEFAULT_OUTPUT_PATH, REPORT_SCHEMA_VERSION


class EvaluationResultsError(ValueError):
    """Raised when an evaluation artifact does not match the public contract."""


def load_evaluation_results(path: Path = DEFAULT_OUTPUT_PATH) -> Dict[str, Any]:
    artifact_path = Path(path).expanduser().resolve()
    if not artifact_path.is_file():
        raise EvaluationResultsError(f"evaluation results not found: {artifact_path}")
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationResultsError(
            f"could not read evaluation results: {type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict):
        raise EvaluationResultsError("evaluation results must be a JSON object")
    version = payload.get("schema_version")
    if not isinstance(version, str) or version.split(".", 1)[0] != REPORT_SCHEMA_VERSION.split(".", 1)[0]:
        raise EvaluationResultsError(
            f"unsupported evaluation result schema: {version!r}"
        )
    for key in ("run", "dataset", "summary", "cases", "failures"):
        if key not in payload:
            raise EvaluationResultsError(f"evaluation results are missing {key!r}")
    return payload


def frontend_evaluation_summary(
    path: Path = DEFAULT_OUTPUT_PATH,
) -> Dict[str, Any]:
    """Return a compact, presentation-safe DTO for the Streamlit frontend.

    The application is a single-process Streamlit app, so an HTTP framework
    would add an artificial boundary.  This service function is the clean local
    endpoint: UI code does not need to know the full artifact schema.
    """

    report = load_evaluation_results(path)
    summary = report["summary"]
    extraction = summary["extraction"]["exact_normalized_field_accuracy"]
    exception = summary["exception_detection"]["field_level"]
    rules = summary["reconciliation"]["isolated_rule_correctness"][
        "rule_correctness"
    ]
    reviewer = summary["reviewer"]
    return {
        "label": summary["label"],
        "generated_at": report["generated_at"],
        "dataset": {
            "id": report["dataset"]["id"],
            "schema_version": report["dataset"]["schema_version"],
            "synthetic": report["dataset"]["synthetic"],
        },
        "sample_size": summary["sample_size"],
        "extraction_accuracy": extraction,
        "exception_precision": exception["precision"],
        "exception_recall": exception["recall"],
        "exception_f1": exception["f1"],
        "reconciliation_rule_correctness": rules,
        "reviewer": reviewer,
        "operating": {
            "documents": summary["operating"]["documents"],
            "latency_ms": summary["operating"]["latency_ms"]["total"],
            "model_calls": summary["operating"]["model_calls"],
            "token_usage": summary["operating"]["token_usage"],
            "estimated_cost": summary["operating"]["estimated_cost"],
        },
        "confidence": summary["confidence"],
        "regression_gates": summary["regression_gates"],
        "worst_failed_cases": summary["failure_analysis"]["worst_failed_cases"],
    }


__all__ = [
    "EvaluationResultsError",
    "frontend_evaluation_summary",
    "load_evaluation_results",
]
