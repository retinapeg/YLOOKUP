"""Reproducible end-to-end evaluation runner for FundOps Control Room."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from app.extraction import DeterministicExtractor, Extractor, extract_document
from app.models import (
    DocumentType,
    ExtractedDocument,
    ExtractedField,
    ExtractionMethod,
    FundRecord,
)
from app.reconciliation import reconcile_document

from .metrics import (
    DATE_FIELDS,
    NUMERIC_FIELDS,
    FieldObservation,
    NormalizationError,
    StatusObservation,
    binary_classification,
    calibration_metrics,
    extraction_metrics,
    is_missing,
    latency_metrics,
    normalize_decimal,
    rate,
    status_metrics,
    values_equal,
)
from .schema import (
    DEFAULT_DATASET_PATH,
    PROJECT_ROOT,
    GoldCase,
    GoldDataset,
    GoldDatasetError,
    load_gold_dataset,
)
from .telemetry import InstrumentedOpenAIExtractor


REPORT_SCHEMA_VERSION = "1.0.0"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "eval_results.json"
FIXTURE_LABEL = "Deterministic demo fixture baseline"
MODEL_LABEL = "Model evaluation"


@dataclass(frozen=True)
class EvaluationConfig:
    dataset_path: Path = DEFAULT_DATASET_PATH
    output_path: Optional[Path] = DEFAULT_OUTPUT_PATH
    mode: str = "fixture"
    case_ids: Tuple[str, ...] = field(default_factory=tuple)
    numeric_tolerance: Decimal = Decimal("0")
    confidence_threshold: float = 0.80
    max_failures: int = 12
    verify_document_hashes: bool = True
    enable_reviewer: bool = True
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_timeout_seconds: float = 12.0
    input_cost_per_million: Optional[Decimal] = None
    output_cost_per_million: Optional[Decimal] = None
    baseline_path: Optional[Path] = None

    def __post_init__(self) -> None:
        mode = self.mode.casefold()
        if mode not in {"fixture", "model"}:
            raise ValueError("mode must be 'fixture' or 'model'")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "dataset_path", Path(self.dataset_path))
        if self.output_path is not None:
            object.__setattr__(self, "output_path", Path(self.output_path))
        if self.baseline_path is not None:
            object.__setattr__(self, "baseline_path", Path(self.baseline_path))
        tolerance = Decimal(str(self.numeric_tolerance))
        if not tolerance.is_finite() or tolerance < 0:
            raise ValueError("numeric_tolerance must be finite and non-negative")
        object.__setattr__(self, "numeric_tolerance", tolerance)
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if self.max_failures < 0:
            raise ValueError("max_failures must be non-negative")
        if self.model_timeout_seconds <= 0:
            raise ValueError("model_timeout_seconds must be positive")
        for name in ("input_cost_per_million", "output_cost_per_million"):
            value = getattr(self, name)
            if value is not None:
                parsed = Decimal(str(value))
                if not parsed.is_finite() or parsed < 0:
                    raise ValueError(f"{name} must be finite and non-negative")
                object.__setattr__(self, name, parsed)


def _value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    return value


def _safe_git_commit() -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else None


def _safe_base_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def _select_cases(dataset: GoldDataset, case_ids: Sequence[str]) -> Tuple[GoldCase, ...]:
    if not case_ids:
        return dataset.cases
    requested = tuple(dict.fromkeys(case_ids))
    available = {case.case_id: case for case in dataset.cases}
    unknown = sorted(set(requested) - set(available))
    if unknown:
        raise GoldDatasetError("unknown case ids: " + ", ".join(unknown))
    requested_set = set(requested)
    return tuple(case for case in dataset.cases if case.case_id in requested_set)


def _make_extractor(config: EvaluationConfig) -> Extractor:
    if config.mode == "fixture":
        return DeterministicExtractor()
    return InstrumentedOpenAIExtractor(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        timeout=config.model_timeout_seconds,
    )


def _method_name(document: Optional[ExtractedDocument]) -> Optional[str]:
    if document is None:
        return None
    return str(_value(document.extraction_method))


def _snapshot(extractor: Any) -> Optional[Mapping[str, int]]:
    snapshot = getattr(extractor, "snapshot", None)
    if callable(snapshot):
        value = snapshot()
        if isinstance(value, Mapping):
            return value
    return None


def _snapshot_delta(
    before: Optional[Mapping[str, int]], after: Optional[Mapping[str, int]]
) -> Optional[Dict[str, int]]:
    if before is None or after is None:
        return None
    return {
        key: int(after.get(key, 0)) - int(before.get(key, 0))
        for key in sorted(set(before) | set(after))
    }


def _first_gold_evidence(case: GoldCase, field_name: str) -> Optional[str]:
    candidates = case.field_evidence.get(field_name)
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    if not isinstance(first, Mapping):
        return None
    raw_text = first.get("raw_text")
    return str(raw_text) if raw_text is not None else None


def _gold_document(case: GoldCase) -> ExtractedDocument:
    fields: Dict[str, ExtractedField] = {}
    for field_name, result in case.expected_field_results.items():
        observed = result.get("observed")
        if is_missing(observed):
            continue
        fields[field_name] = ExtractedField(
            value=observed,
            source=case.primary_document.path.name,
            page=1,
            confidence=1.0,
            evidence=_first_gold_evidence(case, field_name),
            method=ExtractionMethod.DETERMINISTIC,
        )
    raw_type = case.expected_field_results.get("document_type", {}).get(
        "observed", "UNKNOWN"
    )
    try:
        document_type = DocumentType(str(raw_type))
    except ValueError:
        document_type = DocumentType.UNKNOWN
    return ExtractedDocument(
        case_id=case.case_id,
        source_document=case.primary_document.path.name,
        document_type=document_type,
        fields=fields,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        warnings=[],
    )


def _difference_equal(field_name: str, expected: Any, observed: Any) -> bool:
    if is_missing(expected) or is_missing(observed):
        return is_missing(expected) and is_missing(observed)
    if field_name in NUMERIC_FIELDS:
        try:
            return normalize_decimal(expected) == normalize_decimal(observed)
        except NormalizationError:
            return False
    if field_name in DATE_FIELDS:
        try:
            return int(expected) == int(observed)
        except (TypeError, ValueError):
            return False
    return values_equal(field_name, expected, observed)


def _isolated_reconciliation(
    cases: Sequence[GoldCase], config: EvaluationConfig
) -> Tuple[Dict[str, Any], List[StatusObservation], Dict[str, Dict[str, Any]]]:
    status_rows: List[StatusObservation] = []
    case_details: Dict[str, Dict[str, Any]] = {}
    severity_correct = 0
    difference_correct = 0
    difference_total = 0
    overall_correct = 0
    counts_correct = 0
    replayable_cases = [case for case in cases if case.replayable]

    for case in replayable_cases:
        error_type = None
        report = None
        try:
            report = reconcile_document(
                FundRecord.model_validate(case.canonical_record),
                _gold_document(case),
                numeric_tolerance=config.numeric_tolerance,
                confidence_threshold=config.confidence_threshold,
            )
        except Exception as exc:
            error_type = type(exc).__name__

        predicted_by_field = (
            {item.field: item for item in report.results} if report is not None else {}
        )
        field_details = {}
        for field_name, expected_result in case.expected_field_results.items():
            predicted = predicted_by_field.get(field_name)
            expected_status = str(expected_result["status"])
            observed_status = (
                str(_value(predicted.status)) if predicted is not None else None
            )
            expected_severity = str(expected_result["severity"])
            observed_severity = (
                str(_value(predicted.severity)) if predicted is not None else None
            )
            expected_difference = expected_result.get("difference")
            observed_difference = predicted.difference if predicted is not None else None
            severity_matches = expected_severity == observed_severity
            difference_matches = _difference_equal(
                field_name, expected_difference, observed_difference
            )
            severity_correct += severity_matches
            difference_correct += difference_matches
            difference_total += 1
            status_rows.append(
                StatusObservation(
                    case_id=case.case_id,
                    field=field_name,
                    expected_status=expected_status,
                    observed_status=observed_status,
                    severity=expected_severity,
                    pipeline_failed=report is None,
                )
            )
            field_details[field_name] = {
                "expected_status": expected_status,
                "observed_status": observed_status,
                "status_correct": expected_status == observed_status,
                "expected_severity": expected_severity,
                "observed_severity": observed_severity,
                "severity_correct": severity_matches,
                "expected_difference": _json_safe(expected_difference),
                "observed_difference": _json_safe(observed_difference),
                "difference_correct": difference_matches,
            }

        observed_overall = (
            str(_value(report.overall_status)) if report is not None else None
        )
        expected_counts = case.expected_field_results
        derived_counts = {status: 0 for status in ("PASS", "MISMATCH", "MISSING", "REVIEW")}
        for result in expected_counts.values():
            derived_counts[str(result["status"])] = derived_counts.get(str(result["status"]), 0) + 1
        report_counts = dict(report.counts) if report is not None else None
        overall_matches = observed_overall == case.expected_overall_status
        counts_match = report_counts == derived_counts
        overall_correct += overall_matches
        counts_correct += counts_match
        case_details[case.case_id] = {
            "expected_overall_status": case.expected_overall_status,
            "observed_overall_status": observed_overall,
            "overall_status_correct": overall_matches,
            "counts_correct": counts_match,
            "error_type": error_type,
            "fields": field_details,
        }

    status_summary = status_metrics(status_rows)
    status_accuracy = status_summary["status_accuracy"]
    unsupported = [case.case_id for case in cases if not case.replayable]
    metrics = {
        "rule_correctness": status_accuracy,
        "status_metrics": status_summary,
        "severity_accuracy": rate(severity_correct, len(status_rows)),
        "difference_accuracy": rate(difference_correct, difference_total),
        "overall_status_accuracy": rate(overall_correct, len(replayable_cases)),
        "counts_accuracy": rate(counts_correct, len(replayable_cases)),
        "coverage": {
            "eligible_cases": len(replayable_cases),
            "dataset_cases": len(cases),
            "unsupported_cases": unsupported,
            "unsupported_reason": (
                "Current reconciler is single-document and field-level; these gold cases "
                "require ambiguity, duplicate, cross-field, or payment controls."
            ),
        },
        "numeric_tolerance": str(config.numeric_tolerance),
        "confidence_threshold": config.confidence_threshold,
    }
    return metrics, status_rows, case_details


def _try_review(
    report: Any, reviewer: Any, enabled: bool
) -> Tuple[Any, Optional[str], float]:
    if not enabled or report is None:
        return None, None, 0.0
    started = time.perf_counter()
    try:
        from app.review import review_reconciliation

        return review_reconciliation(report, reviewer=reviewer), None, (
            time.perf_counter() - started
        ) * 1000
    except (ImportError, ModuleNotFoundError):
        return None, "ReviewerUnavailable", (time.perf_counter() - started) * 1000
    except Exception as exc:
        return None, type(exc).__name__, (time.perf_counter() - started) * 1000


def _field_failure_category(observation: FieldObservation) -> str:
    if observation.pipeline_failed:
        return "pipeline_failure"
    expected_missing = is_missing(observation.expected)
    observed_missing = is_missing(observation.observed)
    if not expected_missing and observed_missing:
        return "false_abstention"
    if expected_missing and not observed_missing:
        return "spurious_extraction"
    if observation.field in NUMERIC_FIELDS:
        try:
            normalize_decimal(observation.observed)
        except NormalizationError:
            return "numeric_parse_error"
        return "numeric_mismatch"
    if observation.field in DATE_FIELDS:
        return "date_mismatch"
    return "text_mismatch"


def _rank_failure(failure: Mapping[str, Any]) -> Tuple[Any, ...]:
    category = str(failure["failure_category"])
    severity = str(failure.get("severity") or "NONE").upper()
    category_rank = {
        "pipeline_failure": 0,
        "provider_fallback": 0,
        "exception_false_negative": 1 if severity == "HIGH" else 2,
        "reviewer_false_negative": 3,
        "false_abstention": 4,
        "spurious_extraction": 4,
        "numeric_parse_error": 5,
        "date_parse_error": 5,
        "numeric_mismatch": 6,
        "date_mismatch": 6,
        "text_mismatch": 6,
        "exception_false_positive": 7,
        "reconciliation_status_mismatch": 8,
        "reviewer_false_positive": 9,
        "unexpected_field": 10,
    }.get(category, 20)
    confidence = failure.get("confidence")
    confidence_rank = -float(confidence) if isinstance(confidence, (int, float)) else 0.0
    return (
        category_rank,
        confidence_rank,
        str(failure.get("case_id")),
        str(failure.get("field")),
    )


def _build_failures(
    field_rows: Sequence[FieldObservation],
    status_rows: Sequence[StatusObservation],
    case_methods: Mapping[str, Optional[str]],
    reviewer_rows: Sequence[Tuple[str, bool, bool]],
) -> List[Dict[str, Any]]:
    status_by_key = {(row.case_id, row.field): row for row in status_rows}
    failures: List[Dict[str, Any]] = []
    for row in field_rows:
        extraction_correct = not row.pipeline_failed and values_equal(
            row.field, row.expected, row.observed
        )
        status_row = status_by_key.get((row.case_id, row.field))
        status_correct = (
            status_row is None
            or status_row.expected_status == status_row.observed_status
        )
        if extraction_correct and status_correct:
            continue

        root_cause = _field_failure_category(row)
        category = root_cause
        expected_status = status_row.expected_status if status_row else None
        observed_status = status_row.observed_status if status_row else None
        expected_exception = expected_status is not None and expected_status != "PASS"
        observed_exception = observed_status is not None and observed_status != "PASS"
        if row.pipeline_failed and case_methods.get(row.case_id) == "FALLBACK":
            category = "provider_fallback"
        elif status_row is not None and expected_exception and not observed_exception:
            category = "exception_false_negative"
        elif status_row is not None and not expected_exception and observed_exception:
            category = "exception_false_positive"
        elif status_row is not None and not status_correct:
            category = "reconciliation_status_mismatch"

        failed_metrics = []
        if not extraction_correct:
            failed_metrics.append("extraction")
        if not status_correct:
            failed_metrics.append("reconciliation")
        failures.append(
            {
                "case_id": row.case_id,
                "field": row.field,
                "expected": _json_safe(row.expected),
                "observed": _json_safe(row.observed),
                "evidence": row.evidence,
                "failure_category": category,
                "root_cause": root_cause,
                "failed_metrics": failed_metrics,
                "expected_status": expected_status,
                "observed_status": observed_status,
                "severity": status_row.severity if status_row else None,
                "confidence": row.confidence,
                "source": row.source,
                "page": row.page,
                "extraction_method": row.method,
            }
        )

    for case_id, expected, observed in reviewer_rows:
        if expected == observed:
            continue
        failures.append(
            {
                "case_id": case_id,
                "field": "__reviewer_escalation__",
                "expected": expected,
                "observed": observed,
                "evidence": None,
                "failure_category": (
                    "reviewer_false_negative" if expected else "reviewer_false_positive"
                ),
                "root_cause": "reviewer_escalation_mismatch",
                "failed_metrics": ["reviewer_escalation"],
                "expected_status": None,
                "observed_status": None,
                "severity": "HIGH" if expected else "LOW",
                "confidence": None,
                "source": None,
                "page": None,
                "extraction_method": None,
            }
        )
    return sorted(failures, key=_rank_failure)


def _reviewer_metrics(
    cases: Sequence[GoldCase], case_results: Sequence[Mapping[str, Any]], available: bool
) -> Tuple[Dict[str, Any], List[Tuple[str, bool, bool]]]:
    results_by_id = {str(result["case_id"]): result for result in case_results}
    escalation_rows: List[Tuple[str, bool, bool]] = []
    successful_expected = []
    successful_predicted = []
    for case in cases:
        expected = bool(case.reviewer_label.get("requires_human_review", False))
        result = results_by_id[case.case_id]
        review = result.get("reviewer") or {}
        predicted = bool(review.get("requires_human_review", False))
        escalation_rows.append((case.case_id, expected, predicted))
        if review.get("completed"):
            successful_expected.append(expected)
            successful_predicted.append(predicted)

    explicit_challenge_labels = any(
        "challenge_fields" in case.reviewer_label for case in cases
    )
    if available:
        escalation = {
            "end_to_end": binary_classification(
                [row[1] for row in escalation_rows],
                [row[2] for row in escalation_rows],
            ),
            "successful_subset": binary_classification(
                successful_expected, successful_predicted
            ),
            "coverage": rate(len(successful_expected), len(cases)),
            "unit": "case",
            "gold_definition": "reviewer_label.requires_human_review",
        }
    else:
        escalation = {
            "available": False,
            "reason": "No reviewer implementation was available during this run",
            "coverage": rate(0, len(cases)),
        }

    if explicit_challenge_labels:
        expected = []
        predicted = []
        for case in cases:
            expected_fields = set(case.reviewer_label.get("challenge_fields", []))
            result = results_by_id[case.case_id]
            predicted_fields = set(
                (result.get("reviewer") or {}).get("challenge_fields", [])
            )
            universe = sorted(expected_fields | predicted_fields)
            expected.extend(field in expected_fields for field in universe)
            predicted.extend(field in predicted_fields for field in universe)
        challenge = {
            "available": True,
            "metrics": binary_classification(expected, predicted),
            "unit": "case_field",
        }
    else:
        challenge = {
            "available": False,
            "reason": (
                "The gold corpus labels case-level human escalation, not field-level "
                "reviewer CHALLENGE outcomes."
            ),
            "gold_positive_support": 0,
            "precision": None,
            "recall": None,
            "f1": None,
        }

    return {
        "implementation_available": available,
        "escalation": escalation,
        "challenge_detection": challenge,
    }, escalation_rows if available else []


def _operating_metrics(
    config: EvaluationConfig,
    case_results: Sequence[Mapping[str, Any]],
    extractor: Any,
) -> Dict[str, Any]:
    snapshots = _snapshot(extractor)
    total_cases = len(case_results)
    successful_cases = sum(result["pipeline_status"] == "SUCCESS" for result in case_results)
    fallback_cases = sum(result["pipeline_status"] == "FALLBACK" for result in case_results)
    failed_cases = sum(result["pipeline_status"] == "ERROR" for result in case_results)
    model_success_cases = sum(
        result.get("extraction_method") == "OPENAI_COMPATIBLE"
        for result in case_results
    )
    configuration_failure_documents = sum(
        result.get("configuration_failure", False) for result in case_results
    )
    stage_names = ("extraction", "reconciliation", "reviewer", "total")
    latency = {
        stage: latency_metrics(
            result["latency_ms"][stage]
            for result in case_results
            if result["latency_ms"].get(stage) is not None
        )
        for stage in stage_names
    }

    if config.mode == "fixture":
        model_calls = {
            "available": True,
            "count": 0,
            "successful": 0,
            "api_failures": 0,
        }
        token_usage = {
            "available": False,
            "reason": "No model is called in fixture mode",
            "source": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
    elif snapshots is None:
        model_calls = {
            "available": False,
            "reason": "The injected extractor did not expose call telemetry",
            "count": None,
            "successful": None,
            "api_failures": None,
        }
        token_usage = {
            "available": False,
            "reason": "The injected extractor did not expose provider usage",
            "source": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
    else:
        model_calls = {
            "available": True,
            "count": int(snapshots.get("model_calls", 0)),
            "successful": int(snapshots.get("successful_model_calls", 0)),
            "api_failures": int(snapshots.get("api_failures", 0)),
        }
        usage_events = int(snapshots.get("usage_events", 0))
        token_usage = {
            "available": usage_events > 0,
            "reason": (
                None
                if usage_events > 0
                else "Provider usage was not returned for any model call"
            ),
            "source": "provider" if usage_events > 0 else None,
            "usage_event_coverage": rate(
                usage_events, int(snapshots.get("model_calls", 0))
            ),
            "input_tokens": (
                int(snapshots.get("input_tokens", 0)) if usage_events > 0 else None
            ),
            "output_tokens": (
                int(snapshots.get("output_tokens", 0)) if usage_events > 0 else None
            ),
            "total_tokens": (
                int(snapshots.get("total_tokens", 0)) if usage_events > 0 else None
            ),
        }

    rates_available = (
        config.input_cost_per_million is not None
        and config.output_cost_per_million is not None
    )
    if token_usage["available"] and rates_available:
        input_cost = (
            Decimal(token_usage["input_tokens"])
            * config.input_cost_per_million
            / Decimal("1000000")
        )
        output_cost = (
            Decimal(token_usage["output_tokens"])
            * config.output_cost_per_million
            / Decimal("1000000")
        )
        estimated_cost = {
            "available": True,
            "currency": "USD",
            "value": str(input_cost + output_cost),
            "input_cost_per_million": str(config.input_cost_per_million),
            "output_cost_per_million": str(config.output_cost_per_million),
            "basis": "provider-reported tokens multiplied by caller-supplied rates",
        }
    else:
        reason = (
            "Token usage is unavailable"
            if not token_usage["available"]
            else "Both input and output prices must be supplied explicitly"
        )
        estimated_cost = {
            "available": False,
            "reason": reason,
            "currency": "USD",
            "value": None,
        }

    return {
        "documents": {
            "attempted": total_cases,
            "successful": successful_cases,
            "fallback": fallback_cases,
            "failed": failed_cases,
            "coverage": rate(successful_cases, total_cases),
            "model_successful": model_success_cases,
            "model_coverage": rate(model_success_cases, total_cases),
            "configuration_failure_documents": configuration_failure_documents,
        },
        "latency_ms": latency,
        "model_calls": model_calls,
        "token_usage": token_usage,
        "estimated_cost": estimated_cost,
        "structured_output_or_fallback_failures": fallback_cases,
        "workflow_failures": failed_cases,
        "per_document": [
            {
                "case_id": result["case_id"],
                "latency_ms": result["latency_ms"],
                "pipeline_status": result["pipeline_status"],
                "extraction_method": result.get("extraction_method"),
                "model_telemetry": result.get("model_telemetry"),
                "error_type": result.get("error_type"),
            }
            for result in case_results
        ],
    }


def _regression_gates(
    config: EvaluationConfig,
    report_summary: Mapping[str, Any],
    dataset: GoldDataset,
) -> Dict[str, Any]:
    operations = report_summary["operating"]
    rule_metric = report_summary["reconciliation"]["isolated_rule_correctness"][
        "rule_correctness"
    ]
    exception = report_summary["exception_detection"]["field_level"]
    high_recall = report_summary["exception_detection"][
        "high_severity_exception_recall"
    ]
    api_failures = operations["model_calls"].get("api_failures")
    gates = [
        {
            "name": "all_documents_completed_without_fallback",
            "passed": operations["documents"]["successful"]
            == operations["documents"]["attempted"],
            "observed": operations["documents"]["successful"],
            "required": operations["documents"]["attempted"],
        },
        {
            "name": "isolated_reconciliation_has_zero_wrong_statuses",
            "passed": rule_metric["denominator"] > 0
            and rule_metric["numerator"] == rule_metric["denominator"],
            "observed_wrong": rule_metric["denominator"] - rule_metric["numerator"],
            "required_maximum": 0,
        },
        {
            "name": "exception_false_negatives_are_zero",
            "passed": exception["false_negative"] == 0,
            "observed": exception["false_negative"],
            "required_maximum": 0,
        },
        {
            "name": "high_severity_exception_false_negatives_are_zero",
            "passed": high_recall["denominator"] > 0
            and high_recall["numerator"] == high_recall["denominator"],
            "observed": high_recall["denominator"] - high_recall["numerator"],
            "required_maximum": 0,
        },
    ]
    if api_failures is not None:
        gates.append(
            {
                "name": "api_failures_are_zero",
                "passed": api_failures == 0,
                "observed": api_failures,
                "required_maximum": 0,
            }
        )
    if config.mode == "model":
        coverage = operations["documents"]["model_coverage"]
        gates.append(
            {
                "name": "model_coverage_is_complete",
                "passed": coverage["denominator"] > 0
                and coverage["numerator"] == coverage["denominator"],
                "observed": coverage["value"],
                "required": 1.0,
            }
        )

    if config.baseline_path is not None:
        baseline = json.loads(config.baseline_path.read_text(encoding="utf-8"))
        same_dataset = (
            baseline.get("dataset", {}).get("sha256") == dataset.sha256
            and baseline.get("run", {}).get("mode") == config.mode
        )
        gates.append(
            {
                "name": "baseline_is_comparable",
                "passed": same_dataset,
                "observed": baseline.get("dataset", {}).get("sha256"),
                "required": dataset.sha256,
            }
        )
        if same_dataset:
            current_exact = report_summary["extraction"][
                "exact_normalized_field_accuracy"
            ]["numerator"]
            baseline_exact = baseline["summary"]["extraction"][
                "exact_normalized_field_accuracy"
            ]["numerator"]
            gates.append(
                {
                    "name": "extraction_correct_count_does_not_regress",
                    "passed": current_exact >= baseline_exact,
                    "observed": current_exact,
                    "required_minimum": baseline_exact,
                }
            )
            baseline_false_negatives = baseline["summary"]["exception_detection"][
                "field_level"
            ]["false_negative"]
            gates.append(
                {
                    "name": "exception_false_negatives_do_not_regress",
                    "passed": exception["false_negative"] <= baseline_false_negatives,
                    "observed": exception["false_negative"],
                    "required_maximum": baseline_false_negatives,
                }
            )

    return {
        "passed": all(gate["passed"] for gate in gates),
        "gates": gates,
        "policy": (
            "Transparent count gates for a small synthetic corpus; these are not "
            "statistical performance claims."
        ),
    }


def run_evaluation(
    config: Optional[EvaluationConfig] = None,
    *,
    extractor: Optional[Extractor] = None,
    reviewer: Any = None,
    write_output: bool = True,
    clock: Callable[[], float] = time.perf_counter,
) -> Dict[str, Any]:
    """Run extraction, reconciliation, review, and failure analysis.

    The returned object is the same JSON-safe service contract consumed by the
    CLI and any future Streamlit evaluation view.
    """

    config = config or EvaluationConfig()
    dataset = load_gold_dataset(
        config.dataset_path, verify_document_hashes=config.verify_document_hashes
    )
    cases = _select_cases(dataset, config.case_ids)
    selected_extractor = extractor or _make_extractor(config)
    label = FIXTURE_LABEL if config.mode == "fixture" else MODEL_LABEL

    isolated_metrics, _, isolated_details = _isolated_reconciliation(cases, config)
    field_rows: List[FieldObservation] = []
    status_rows: List[StatusObservation] = []
    case_level_expected = []
    case_level_predicted = []
    case_results: List[Dict[str, Any]] = []
    case_methods: Dict[str, Optional[str]] = {}
    reviewer_implementation_available = False

    for case in cases:
        case_started = clock()
        before_snapshot = _snapshot(selected_extractor)
        extraction_started = clock()
        document: Optional[ExtractedDocument] = None
        extraction_error = None
        try:
            document = extract_document(
                case.primary_document.path,
                extractor=selected_extractor,
                case_id=case.case_id,
            )
        except Exception as exc:
            extraction_error = type(exc).__name__
        extraction_ms = max(0.0, (clock() - extraction_started) * 1000)
        after_snapshot = _snapshot(selected_extractor)
        telemetry_delta = _snapshot_delta(before_snapshot, after_snapshot)

        realized_method = _method_name(document)
        case_methods[case.case_id] = realized_method
        configuration_failure = bool(
            config.mode == "model"
            and document is not None
            and realized_method == "FALLBACK"
            and not bool(getattr(selected_extractor, "available", True))
        )
        model_fallback = bool(
            config.mode == "model" and realized_method != "OPENAI_COMPATIBLE"
        )
        pipeline_failed = document is None or model_fallback
        if document is None:
            pipeline_status = "ERROR"
        elif model_fallback:
            pipeline_status = "FALLBACK"
        else:
            pipeline_status = "SUCCESS"

        extracted_fields = document.fields if document is not None else {}
        case_field_details = {}
        for field_name, expected in case.expected_values.items():
            extracted = extracted_fields.get(field_name)
            observed = extracted.value if extracted is not None else None
            row = FieldObservation(
                case_id=case.case_id,
                field=field_name,
                expected=expected,
                observed=observed,
                confidence=extracted.confidence if extracted is not None else None,
                evidence=extracted.evidence if extracted is not None else None,
                source=extracted.source if extracted is not None else None,
                page=extracted.page if extracted is not None else None,
                method=(str(_value(extracted.method)) if extracted is not None else None),
                pipeline_failed=pipeline_failed,
            )
            field_rows.append(row)
            case_field_details[field_name] = {
                "expected": _json_safe(expected),
                "observed": _json_safe(observed),
                "correct": (
                    not pipeline_failed and values_equal(field_name, expected, observed)
                ),
                "confidence": row.confidence,
                "evidence": row.evidence,
                "source": row.source,
                "page": row.page,
                "method": row.method,
                "scored_as_pipeline_failure": pipeline_failed,
            }

        unexpected_fields = sorted(set(extracted_fields) - set(case.expected_values))
        scoring_document = None if pipeline_failed else document
        reconciliation_started = clock()
        reconciliation_report = None
        reconciliation_error = None
        if scoring_document is not None:
            try:
                reconciliation_report = reconcile_document(
                    FundRecord.model_validate(case.canonical_record),
                    scoring_document,
                    numeric_tolerance=config.numeric_tolerance,
                    confidence_threshold=config.confidence_threshold,
                )
            except Exception as exc:
                reconciliation_error = type(exc).__name__
        reconciliation_ms = max(0.0, (clock() - reconciliation_started) * 1000)
        predicted_by_field = (
            {item.field: item for item in reconciliation_report.results}
            if reconciliation_report is not None
            else {}
        )

        reconciliation_fields = {}
        if case.replayable:
            for field_name, expected_result in case.expected_field_results.items():
                predicted = predicted_by_field.get(field_name)
                expected_status = str(expected_result["status"])
                observed_status = (
                    str(_value(predicted.status)) if predicted is not None else None
                )
                expected_severity = str(expected_result["severity"])
                status_rows.append(
                    StatusObservation(
                        case_id=case.case_id,
                        field=field_name,
                        expected_status=expected_status,
                        observed_status=observed_status,
                        severity=expected_severity,
                        pipeline_failed=reconciliation_report is None,
                    )
                )
                reconciliation_fields[field_name] = {
                    "expected_status": expected_status,
                    "observed_status": observed_status,
                    "correct": expected_status == observed_status,
                    "expected_severity": expected_severity,
                    "observed_severity": (
                        str(_value(predicted.severity)) if predicted is not None else None
                    ),
                    "difference": (
                        _json_safe(predicted.difference) if predicted is not None else None
                    ),
                }
            gold_case_exception = any(
                str(result["status"]) != "PASS"
                for result in case.expected_field_results.values()
            )
            predicted_case_exception = bool(
                reconciliation_report is not None
                and str(_value(reconciliation_report.overall_status)) != "PASS"
            )
            case_level_expected.append(gold_case_exception)
            case_level_predicted.append(predicted_case_exception)

        review_report, review_error, reviewer_ms = _try_review(
            reconciliation_report, reviewer, config.enable_reviewer
        )
        reviewer_implementation_available = reviewer_implementation_available or (
            review_report is not None
        )
        reviewer_payload: Dict[str, Any]
        if review_report is None:
            reviewer_payload = {
                "completed": False,
                "error_type": review_error,
                "requires_human_review": False,
                "challenge_fields": [],
                "counts": None,
            }
        else:
            findings = list(review_report.findings)
            reviewer_payload = {
                "completed": True,
                "error_type": None,
                "requires_human_review": any(
                    finding.requires_human_review for finding in findings
                ),
                "challenge_fields": [
                    finding.field
                    for finding in findings
                    if str(_value(finding.status)) == "CHALLENGE"
                ],
                "counts": dict(review_report.counts),
                "findings": [
                    {
                        "field": finding.field,
                        "status": str(_value(finding.status)),
                        "requires_human_review": finding.requires_human_review,
                        "challenged_value": _json_safe(finding.challenged_value),
                        "confidence": finding.confidence,
                        "review_method": str(_value(finding.review_method)),
                    }
                    for finding in findings
                ],
            }

        total_ms = max(0.0, (clock() - case_started) * 1000)
        case_results.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "scenario_types": list(case.scenario_types),
                "replayable_against_current_reconciler": case.replayable,
                "document": {
                    "path": case.primary_document.reference,
                    "document_id": case.primary_document.document_id,
                    "disposition": case.primary_document.disposition,
                    "all_document_count": len(case.documents),
                },
                "pipeline_status": pipeline_status,
                "configuration_failure": configuration_failure,
                "error_type": extraction_error or reconciliation_error,
                "extraction_method": realized_method,
                "warnings": list(document.warnings) if document is not None else [],
                "model_telemetry": telemetry_delta,
                "latency_ms": {
                    "extraction": extraction_ms,
                    "reconciliation": reconciliation_ms,
                    "reviewer": reviewer_ms,
                    "total": total_ms,
                },
                "extraction": {
                    "fields": case_field_details,
                    "unexpected_fields": unexpected_fields,
                },
                "reconciliation": {
                    "scored": case.replayable,
                    "expected_overall_status": case.expected_overall_status,
                    "observed_overall_status": (
                        str(_value(reconciliation_report.overall_status))
                        if reconciliation_report is not None
                        else None
                    ),
                    "fields": reconciliation_fields,
                    "unsupported_additional_controls": sorted(
                        case.expected_additional_results
                    ),
                    "error_type": reconciliation_error,
                },
                "reviewer": reviewer_payload,
                "isolated_reconciliation": isolated_details.get(case.case_id),
            }
        )

    extraction_summary = extraction_metrics(field_rows)
    end_to_end_status = status_metrics(status_rows)
    case_level_exception = binary_classification(
        case_level_expected, case_level_predicted
    )
    reviewer_summary, reviewer_rows = _reviewer_metrics(
        cases, case_results, reviewer_implementation_available
    )
    all_failures = _build_failures(
        field_rows, status_rows, case_methods, reviewer_rows
    )
    operating = _operating_metrics(config, case_results, selected_extractor)

    successful_model_ids = {
        result["case_id"]
        for result in case_results
        if result.get("extraction_method") == "OPENAI_COMPATIBLE"
    }
    model_success_rows = [
        row for row in field_rows if row.case_id in successful_model_ids
    ]
    confidence = calibration_metrics(field_rows)
    confidence["confidence_kind"] = (
        "deterministic heuristic"
        if config.mode == "fixture"
        else "model-reported on successful model outputs only"
    )

    gold_missing = sum(is_missing(row.expected) for row in field_rows)
    summary: Dict[str, Any] = {
        "label": label,
        "sample_size": {
            "dataset_cases": len(dataset.cases),
            "selected_cases": len(cases),
            "selected_documents": len(cases),
            "labelled_extraction_fields": len(field_rows),
            "gold_present_fields": len(field_rows) - gold_missing,
            "gold_missing_fields": gold_missing,
            "replayable_reconciliation_cases": sum(case.replayable for case in cases),
            "unsupported_reconciliation_cases": sum(not case.replayable for case in cases),
            "reconciliation_field_labels": len(status_rows),
            "gold_exception_fields": sum(
                row.expected_status != "PASS" for row in status_rows
            ),
            "reviewer_case_labels": len(cases),
        },
        "extraction": extraction_summary,
        "model_success_subset": (
            {
                "extraction": extraction_metrics(model_success_rows),
                "sample_documents": len(successful_model_ids),
                "note": "Fallback documents are excluded from this subset and remain failures end to end.",
            }
            if config.mode == "model"
            else None
        ),
        "exception_detection": {
            "field_level": end_to_end_status["exception_detection"],
            "case_level": case_level_exception,
            "high_severity_exception_recall": end_to_end_status[
                "high_severity_exception_recall"
            ],
            "recall_by_gold_status": end_to_end_status["recall_by_gold_status"],
            "scored_scope": "replayable canonical field controls only",
        },
        "reconciliation": {
            "end_to_end_status_accuracy": end_to_end_status["status_accuracy"],
            "end_to_end_status_confusion": end_to_end_status["status_confusion"],
            "isolated_rule_correctness": isolated_metrics,
        },
        "reviewer": reviewer_summary,
        "operating": operating,
        "confidence": confidence,
        "failure_analysis": {
            "total_failed_case_fields": len(all_failures),
            "returned_worst_count": min(config.max_failures, len(all_failures)),
            "worst_failed_cases": all_failures[: config.max_failures],
        },
    }
    summary["regression_gates"] = _regression_gates(config, summary, dataset)

    unsupported_controls = [
        {
            "case_id": case.case_id,
            "controls": sorted(case.expected_additional_results),
            "reason": "not implemented by the current single-document field reconciler",
        }
        for case in cases
        if not case.replayable or case.expected_additional_results
    ]
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": {
            "mode": config.mode,
            "label": label,
            "git_commit": _safe_git_commit(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "numeric_tolerance": str(config.numeric_tolerance),
            "confidence_threshold": config.confidence_threshold,
            "model": (
                getattr(selected_extractor, "model", None)
                if config.mode == "model"
                else None
            ),
            "base_url": (
                _safe_base_url(getattr(selected_extractor, "base_url", None))
                if config.mode == "model"
                else None
            ),
            "temperature": 0 if config.mode == "model" else None,
            "secrets_recorded": False,
        },
        "dataset": {
            "id": dataset.dataset_id,
            "schema_version": dataset.schema_version,
            "sha256": dataset.sha256,
            "path": str(dataset.path),
            "synthetic": dataset.synthetic,
            "case_count": len(dataset.cases),
            "selected_case_count": len(cases),
        },
        "summary": summary,
        "unsupported_controls": unsupported_controls,
        "failures": all_failures,
        "cases": case_results,
        "limitations": [
            (
                f"This is a synthetic gold corpus with n={len(cases)} selected cases; "
                "results are descriptive and are not statistically representative of production."
            ),
            (
                f"Deterministic reconciliation is scored on {sum(case.replayable for case in cases)} "
                f"of {len(cases)} cases; unsupported batch/cross-field controls are listed separately."
            ),
            (
                "Deterministic extractor confidences are fixed heuristics, not learned or "
                "validated probabilities; calibration bins are descriptive only."
            ),
            (
                "Reviewer challenge precision/recall is unavailable because the current gold "
                "corpus contains case-level escalation labels, not field-level challenge labels."
            ),
            (
                "Latency is a single local run and should not be used as a production SLO estimate."
            ),
        ],
    }
    safe_report = _json_safe(report)
    # Reject accidental NaN/Infinity before either callers or the filesystem see it.
    json.dumps(safe_report, allow_nan=False)
    if write_output and config.output_path is not None:
        write_evaluation_results(safe_report, config.output_path)
    return safe_report


def write_evaluation_results(report: Mapping[str, Any], path: Path) -> Path:
    """Atomically persist a complete evaluation artifact."""

    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        _json_safe(report), indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return output_path


__all__ = [
    "DEFAULT_OUTPUT_PATH",
    "FIXTURE_LABEL",
    "MODEL_LABEL",
    "REPORT_SCHEMA_VERSION",
    "EvaluationConfig",
    "run_evaluation",
    "write_evaluation_results",
]
