"""Pure, dependency-free metric calculations for FundOps evaluations."""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


NUMERIC_FIELDS = frozenset(
    {"commitment_amount", "capital_call_amount", "management_fee"}
)
DATE_FIELDS = frozenset({"call_date", "due_date"})
PASS_STATUS = "PASS"


class NormalizationError(ValueError):
    """Raised when a labelled value cannot be normalized safely."""


@dataclass(frozen=True)
class FieldObservation:
    case_id: str
    field: str
    expected: Any
    observed: Any
    confidence: Optional[float] = None
    evidence: Optional[str] = None
    source: Optional[str] = None
    page: Optional[int] = None
    method: Optional[str] = None
    pipeline_failed: bool = False


@dataclass(frozen=True)
class StatusObservation:
    case_id: str
    field: str
    expected_status: str
    observed_status: Optional[str]
    severity: str = "NONE"
    pipeline_failed: bool = False


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def is_missing(value: Any) -> bool:
    value = _enum_value(value)
    return value is None or (isinstance(value, str) and not value.strip())


def normalize_text(value: Any) -> str:
    value = _enum_value(value)
    normalized = unicodedata.normalize("NFKC", str(value))
    return " ".join(normalized.split()).casefold()


def normalize_decimal(value: Any) -> Decimal:
    value = _enum_value(value)
    if isinstance(value, bool):
        raise NormalizationError("boolean is not a numeric value")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise NormalizationError("numeric value must be finite")
        number = Decimal(str(value))
    else:
        raw = unicodedata.normalize("NFKC", str(value)).strip()
        if not raw or "%" in raw:
            raise NormalizationError("value is not an amount")

        negative_parentheses = raw.startswith("(") and raw.endswith(")")
        if negative_parentheses:
            raw = raw[1:-1].strip()
        raw = re.sub(r"(?i)\b(?:GBP|USD|EUR)\b", "", raw)
        raw = raw.translate(str.maketrans({"£": "", "$": "", "€": "", "'": ""}))
        raw = re.sub(r"\s+", "", raw)

        if "," in raw and "." in raw:
            if raw.rfind(",") > raw.rfind("."):
                raw = raw.replace(".", "").replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "," in raw:
            pieces = raw.split(",")
            if len(pieces) == 2 and 1 <= len(pieces[1]) <= 2:
                raw = ".".join(pieces)
            elif len(pieces) > 1 and all(len(piece) == 3 for piece in pieces[1:]):
                raw = "".join(pieces)
            else:
                raise NormalizationError("ambiguous comma-separated amount")
        elif raw.count(".") > 1:
            pieces = raw.split(".")
            if all(len(piece) == 3 for piece in pieces[1:]):
                raw = "".join(pieces)
            else:
                raise NormalizationError("ambiguous decimal-separated amount")

        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw):
            raise NormalizationError("value is not a canonical amount")
        try:
            number = Decimal(raw)
        except InvalidOperation as exc:
            raise NormalizationError("value is not a decimal") from exc
        if negative_parentheses:
            number = -number

    if not number.is_finite():
        raise NormalizationError("numeric value must be finite")
    return number


def normalize_date(value: Any) -> date:
    value = _enum_value(value)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, bool):
        raise NormalizationError("boolean is not a date")
    cleaned = " ".join(str(value).strip().replace(",", "").split())
    for date_format in (
        "%Y-%m-%d",
        "%d %B %Y",
        "%d %b %Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    raise NormalizationError("value is not a supported date")


def normalize_value(field: str, value: Any) -> Any:
    if is_missing(value):
        return None
    if field in NUMERIC_FIELDS:
        return normalize_decimal(value)
    if field in DATE_FIELDS:
        return normalize_date(value)
    return normalize_text(value)


def values_equal(field: str, expected: Any, observed: Any) -> bool:
    expected_missing = is_missing(expected)
    observed_missing = is_missing(observed)
    if expected_missing or observed_missing:
        return expected_missing and observed_missing
    try:
        return normalize_value(field, expected) == normalize_value(field, observed)
    except NormalizationError:
        return False


def rate(numerator: int, denominator: int) -> Dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def _observation_correct(observation: FieldObservation) -> bool:
    return not observation.pipeline_failed and values_equal(
        observation.field, observation.expected, observation.observed
    )


def extraction_metrics(
    observations: Iterable[FieldObservation],
) -> Dict[str, Any]:
    rows = list(observations)
    correct = sum(_observation_correct(row) for row in rows)
    gold_present_rows = [row for row in rows if not is_missing(row.expected)]
    gold_missing_rows = [row for row in rows if is_missing(row.expected)]
    numeric_rows = [
        row for row in gold_present_rows if row.field in NUMERIC_FIELDS
    ]
    date_rows = [row for row in gold_present_rows if row.field in DATE_FIELDS]

    correct_presence = sum(
        not row.pipeline_failed
        and is_missing(row.expected) == is_missing(row.observed)
        for row in rows
    )
    correct_abstentions = sum(
        not row.pipeline_failed and is_missing(row.observed)
        for row in gold_missing_rows
    )
    false_abstentions = sum(
        row.pipeline_failed or is_missing(row.observed)
        for row in gold_present_rows
    )
    spurious_extractions = sum(
        not row.pipeline_failed and not is_missing(row.observed)
        for row in gold_missing_rows
    )
    required_values_present = sum(
        not row.pipeline_failed and not is_missing(row.observed)
        for row in gold_present_rows
    )

    per_field: Dict[str, Any] = {}
    for field_name in sorted({row.field for row in rows}):
        field_rows = [row for row in rows if row.field == field_name]
        present_rows = [row for row in field_rows if not is_missing(row.expected)]
        missing_rows = [row for row in field_rows if is_missing(row.expected)]
        per_field[field_name] = {
            "exact_normalized_accuracy": rate(
                sum(_observation_correct(row) for row in field_rows), len(field_rows)
            ),
            "value_accuracy_on_gold_present": rate(
                sum(_observation_correct(row) for row in present_rows),
                len(present_rows),
            ),
            "abstention_accuracy_on_gold_missing": rate(
                sum(
                    not row.pipeline_failed and is_missing(row.observed)
                    for row in missing_rows
                ),
                len(missing_rows),
            ),
            "labelled_count": len(field_rows),
            "gold_present_count": len(present_rows),
            "gold_missing_count": len(missing_rows),
        }

    return {
        "exact_normalized_field_accuracy": rate(correct, len(rows)),
        "value_accuracy_on_gold_present": rate(
            sum(_observation_correct(row) for row in gold_present_rows),
            len(gold_present_rows),
        ),
        "numeric_accuracy": rate(
            sum(_observation_correct(row) for row in numeric_rows), len(numeric_rows)
        ),
        "date_accuracy": rate(
            sum(_observation_correct(row) for row in date_rows), len(date_rows)
        ),
        "missing_abstention": {
            "presence_classification_accuracy": rate(correct_presence, len(rows)),
            "correct_abstention_rate": rate(
                correct_abstentions, len(gold_missing_rows)
            ),
            "required_field_coverage": rate(
                required_values_present, len(gold_present_rows)
            ),
            "correct_abstentions": correct_abstentions,
            "false_abstentions": false_abstentions,
            "spurious_extractions": spurious_extractions,
            "gold_missing_count": len(gold_missing_rows),
            "gold_present_count": len(gold_present_rows),
        },
        "per_field": per_field,
    }


def binary_classification(
    expected: Sequence[bool], predicted: Sequence[bool]
) -> Dict[str, Any]:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same length")
    true_positive = sum(gold and guess for gold, guess in zip(expected, predicted))
    false_positive = sum(
        not gold and guess for gold, guess in zip(expected, predicted)
    )
    false_negative = sum(
        gold and not guess for gold, guess in zip(expected, predicted)
    )
    true_negative = sum(
        not gold and not guess for gold, guess in zip(expected, predicted)
    )
    precision = rate(true_positive, true_positive + false_positive)
    recall = rate(true_positive, true_positive + false_negative)
    precision_value = precision["value"]
    recall_value = recall["value"]
    if precision_value is None or recall_value is None:
        f1 = {"value": None, "numerator": None, "denominator": None}
    elif precision_value + recall_value == 0:
        f1 = {"value": 0.0, "numerator": 0, "denominator": 1}
    else:
        f1 = {
            "value": 2 * precision_value * recall_value / (precision_value + recall_value),
            "numerator": 2 * true_positive,
            "denominator": 2 * true_positive + false_positive + false_negative,
        }

    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "actual_positive": true_positive + false_negative,
        "predicted_positive": true_positive + false_positive,
        "support": len(expected),
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def status_metrics(
    observations: Iterable[StatusObservation],
) -> Dict[str, Any]:
    rows = list(observations)
    expected_exception = [row.expected_status != PASS_STATUS for row in rows]
    predicted_exception = [
        row.observed_status is not None and row.observed_status != PASS_STATUS
        for row in rows
    ]
    confusion: Dict[str, Dict[str, int]] = {}
    for row in rows:
        observed = row.observed_status or "NO_OUTPUT"
        confusion.setdefault(row.expected_status, {})[observed] = (
            confusion.setdefault(row.expected_status, {}).get(observed, 0) + 1
        )

    recalls_by_status = {}
    for status in sorted({row.expected_status for row in rows}):
        status_rows = [row for row in rows if row.expected_status == status]
        recalls_by_status[status] = rate(
            sum(row.observed_status == status for row in status_rows), len(status_rows)
        )

    exception_rows = [row for row in rows if row.expected_status != PASS_STATUS]
    high_severity_rows = [
        row for row in exception_rows if row.severity.upper() == "HIGH"
    ]
    high_severity_recall = rate(
        sum(
            row.observed_status is not None and row.observed_status != PASS_STATUS
            for row in high_severity_rows
        ),
        len(high_severity_rows),
    )

    return {
        "status_accuracy": rate(
            sum(row.expected_status == row.observed_status for row in rows), len(rows)
        ),
        "exception_detection": binary_classification(
            expected_exception, predicted_exception
        ),
        "high_severity_exception_recall": high_severity_recall,
        "recall_by_gold_status": recalls_by_status,
        "status_confusion": confusion,
    }


def calibration_metrics(
    observations: Iterable[FieldObservation], *, bin_count: int = 5
) -> Dict[str, Any]:
    if bin_count < 1:
        raise ValueError("bin_count must be positive")
    rows = list(observations)
    usable: List[Tuple[FieldObservation, float]] = []
    invalid_confidence = 0
    for row in rows:
        if row.confidence is None or row.pipeline_failed:
            continue
        try:
            confidence = float(row.confidence)
        except (TypeError, ValueError):
            invalid_confidence += 1
            continue
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            invalid_confidence += 1
            continue
        usable.append((row, confidence))

    if not usable:
        return {
            "available": False,
            "reason": "No scored fields carried a valid confidence value",
            "sample_size": 0,
            "coverage": rate(0, len(rows)),
            "invalid_confidence_count": invalid_confidence,
            "expected_calibration_error": None,
            "brier_score": None,
            "bins": [],
        }

    grouped: List[List[Tuple[FieldObservation, float]]] = [
        [] for _ in range(bin_count)
    ]
    for row, confidence in usable:
        index = min(int(confidence * bin_count), bin_count - 1)
        grouped[index].append((row, confidence))

    bins = []
    weighted_gap = 0.0
    squared_errors = []
    for index, group in enumerate(grouped):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        if group:
            mean_confidence = statistics.fmean(value for _, value in group)
            empirical_accuracy = statistics.fmean(
                1.0 if _observation_correct(row) else 0.0 for row, _ in group
            )
            gap = abs(mean_confidence - empirical_accuracy)
            weighted_gap += len(group) / len(usable) * gap
            squared_errors.extend(
                (confidence - (1.0 if _observation_correct(row) else 0.0)) ** 2
                for row, confidence in group
            )
        else:
            mean_confidence = None
            empirical_accuracy = None
            gap = None
        bins.append(
            {
                "lower": lower,
                "upper": upper,
                "upper_inclusive": index == bin_count - 1,
                "count": len(group),
                "mean_confidence": mean_confidence,
                "empirical_accuracy": empirical_accuracy,
                "absolute_gap": gap,
            }
        )

    return {
        "available": True,
        "sample_size": len(usable),
        "coverage": rate(len(usable), len(rows)),
        "invalid_confidence_count": invalid_confidence,
        "mean_confidence": statistics.fmean(value for _, value in usable),
        "empirical_accuracy": statistics.fmean(
            1.0 if _observation_correct(row) else 0.0 for row, _ in usable
        ),
        "expected_calibration_error": weighted_gap,
        "brier_score": statistics.fmean(squared_errors),
        "bins": bins,
        "interpretation": (
            "Descriptive only; bin counts are small and do not establish statistical calibration."
        ),
    }


def latency_metrics(values_ms: Iterable[float]) -> Dict[str, Any]:
    values = sorted(float(value) for value in values_ms)
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("latencies must be finite and non-negative")
    if not values:
        return {
            "count": 0,
            "total_ms": 0.0,
            "mean_ms": None,
            "median_ms": None,
            "min_ms": None,
            "max_ms": None,
            "p95_ms": None,
            "p95_reason": "No latency observations",
        }
    p95 = None
    p95_reason = "At least 20 documents are required for a reported p95"
    if len(values) >= 20:
        rank = max(0, math.ceil(0.95 * len(values)) - 1)
        p95 = values[rank]
        p95_reason = None
    return {
        "count": len(values),
        "total_ms": sum(values),
        "mean_ms": statistics.fmean(values),
        "median_ms": statistics.median(values),
        "min_ms": values[0],
        "max_ms": values[-1],
        "p95_ms": p95,
        "p95_reason": p95_reason,
    }


__all__ = [
    "DATE_FIELDS",
    "NUMERIC_FIELDS",
    "FieldObservation",
    "NormalizationError",
    "StatusObservation",
    "binary_classification",
    "calibration_metrics",
    "extraction_metrics",
    "is_missing",
    "latency_metrics",
    "normalize_date",
    "normalize_decimal",
    "normalize_text",
    "normalize_value",
    "rate",
    "status_metrics",
    "values_equal",
]
