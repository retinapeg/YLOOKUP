"""Deterministic reconciliation rules for extracted fund documents."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping
import unicodedata

from .models import (
    ExtractedDocument,
    ExtractedField,
    FieldValue,
    FundRecord,
    ReconciliationItem,
    ReconciliationReport,
    ReconciliationStatus,
    Severity,
)


NUMERIC_FIELDS = frozenset(
    {"commitment_amount", "capital_call_amount", "management_fee"}
)
DATE_FIELDS = frozenset({"call_date", "due_date"})

_FIELD_SEVERITY: dict[str, Severity] = {
    "fund_name": Severity.HIGH,
    "investor_name": Severity.HIGH,
    "commitment_amount": Severity.HIGH,
    "capital_call_amount": Severity.HIGH,
    "call_date": Severity.MEDIUM,
    "due_date": Severity.HIGH,
    "currency": Severity.HIGH,
    "bank_account_reference": Severity.MEDIUM,
    "management_fee": Severity.MEDIUM,
    "document_type": Severity.MEDIUM,
}


def _is_missing(value: FieldValue) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    normalized = " ".join(value.split()).casefold()
    return normalized in {"", "n/a", "na", "not available", "not provided", "—", "-"}


def _normalize_text(value: object) -> str:
    raw = getattr(value, "value", value)
    normalized = unicodedata.normalize("NFKC", str(raw))
    return " ".join(normalized.split()).casefold()


def _normalize_reference(value: object) -> str:
    return "".join(character for character in _normalize_text(value) if character.isalnum())


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise InvalidOperation("booleans are not monetary values")
    cleaned = str(value).strip().replace(",", "")
    for token in ("GBP", "USD", "EUR", "£", "$", "€"):
        cleaned = cleaned.replace(token, "").replace(token.lower(), "")
    result = Decimal(cleaned.strip())
    if not result.is_finite():
        raise InvalidOperation("monetary values must be finite")
    return result


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _money(value: Decimal, currency: str) -> str:
    quantized = value.quantize(Decimal("0.01"))
    decimals = 0 if quantized == quantized.to_integral() else 2
    return f"{currency} {quantized:,.{decimals}f}"


def reconcile_field(
    field_name: str,
    expected: FieldValue,
    extracted: ExtractedField | None,
    *,
    currency: str = "",
    numeric_tolerance: Decimal = Decimal("0"),
    confidence_threshold: float = 0.80,
) -> ReconciliationItem:
    """Compare one field without invoking an LLM or any external service."""

    observed = extracted.value if extracted else None
    base_severity = _FIELD_SEVERITY.get(field_name, Severity.MEDIUM)

    if _is_missing(expected) and _is_missing(observed):
        return ReconciliationItem(
            field=field_name,
            expected=expected,
            observed=observed,
            status=ReconciliationStatus.PASS,
            severity=Severity.NONE,
            explanation="No value is expected and none is present in the document",
            provenance=extracted,
        )

    if _is_missing(observed):
        return ReconciliationItem(
            field=field_name,
            expected=expected,
            observed=None,
            status=ReconciliationStatus.MISSING,
            severity=base_severity,
            explanation="Expected value is missing from the document",
            provenance=extracted,
        )

    if _is_missing(expected):
        return ReconciliationItem(
            field=field_name,
            expected=None,
            observed=observed,
            status=ReconciliationStatus.REVIEW,
            severity=Severity.LOW,
            explanation="Document contains a value that is absent from the fund record",
            provenance=extracted,
        )

    if field_name in NUMERIC_FIELDS:
        try:
            expected_number = _as_decimal(expected)
            observed_number = _as_decimal(observed)
        except (InvalidOperation, ValueError):
            return ReconciliationItem(
                field=field_name,
                expected=expected,
                observed=observed,
                status=ReconciliationStatus.REVIEW,
                severity=base_severity,
                explanation="Extracted value could not be interpreted as a number",
                provenance=extracted,
            )

        difference = observed_number - expected_number
        if abs(difference) <= numeric_tolerance:
            if extracted is not None and extracted.confidence < confidence_threshold:
                return ReconciliationItem(
                    field=field_name,
                    expected=expected_number,
                    observed=observed_number,
                    status=ReconciliationStatus.REVIEW,
                    severity=Severity.LOW,
                    difference=difference,
                    explanation="Values match, but extraction confidence requires review",
                    provenance=extracted,
                )
            return ReconciliationItem(
                field=field_name,
                expected=expected_number,
                observed=observed_number,
                status=ReconciliationStatus.PASS,
                severity=Severity.NONE,
                difference=difference,
                explanation="Document amount matches the fund record",
                provenance=extracted,
            )

        direction = "higher" if difference > 0 else "lower"
        return ReconciliationItem(
            field=field_name,
            expected=expected_number,
            observed=observed_number,
            status=ReconciliationStatus.MISMATCH,
            severity=base_severity,
            difference=difference,
            explanation=(
                f"Document amount is {_money(abs(difference), currency)} "
                f"{direction} than the fund record"
            ),
            provenance=extracted,
        )

    if field_name in DATE_FIELDS:
        try:
            expected_date = _as_date(expected)
            observed_date = _as_date(observed)
        except (TypeError, ValueError):
            return ReconciliationItem(
                field=field_name,
                expected=expected,
                observed=observed,
                status=ReconciliationStatus.REVIEW,
                severity=base_severity,
                explanation="Extracted value could not be interpreted as an ISO date",
                provenance=extracted,
            )

        difference = (observed_date - expected_date).days
        if difference == 0:
            if extracted is not None and extracted.confidence < confidence_threshold:
                return ReconciliationItem(
                    field=field_name,
                    expected=expected_date,
                    observed=observed_date,
                    status=ReconciliationStatus.REVIEW,
                    severity=Severity.LOW,
                    difference=0,
                    explanation="Values match, but extraction confidence requires review",
                    provenance=extracted,
                )
            return ReconciliationItem(
                field=field_name,
                expected=expected_date,
                observed=observed_date,
                status=ReconciliationStatus.PASS,
                severity=Severity.NONE,
                difference=0,
                explanation="Document date matches the fund record",
                provenance=extracted,
            )

        direction = "later" if difference > 0 else "earlier"
        unit = "day" if abs(difference) == 1 else "days"
        return ReconciliationItem(
            field=field_name,
            expected=expected_date,
            observed=observed_date,
            status=ReconciliationStatus.MISMATCH,
            severity=base_severity,
            difference=difference,
            explanation=(
                f"Document date is {abs(difference)} {unit} {direction} than the fund record"
            ),
            provenance=extracted,
        )

    if field_name == "bank_account_reference":
        values_match = _normalize_reference(expected) == _normalize_reference(observed)
    else:
        values_match = _normalize_text(expected) == _normalize_text(observed)

    if values_match:
        if extracted is not None and extracted.confidence < confidence_threshold:
            return ReconciliationItem(
                field=field_name,
                expected=expected,
                observed=observed,
                status=ReconciliationStatus.REVIEW,
                severity=Severity.LOW,
                explanation="Values match, but extraction confidence requires review",
                provenance=extracted,
            )
        return ReconciliationItem(
            field=field_name,
            expected=expected,
            observed=observed,
            status=ReconciliationStatus.PASS,
            severity=Severity.NONE,
            explanation="Document value matches the fund record",
            provenance=extracted,
        )

    return ReconciliationItem(
        field=field_name,
        expected=expected,
        observed=observed,
        status=ReconciliationStatus.MISMATCH,
        severity=base_severity,
        explanation="Document value does not match the fund record",
        provenance=extracted,
    )


def reconcile_document(
    fund_record: FundRecord | Mapping[str, object],
    document: ExtractedDocument | Mapping[str, object],
    *,
    numeric_tolerance: Decimal = Decimal("0"),
    confidence_threshold: float = 0.80,
) -> ReconciliationReport:
    """Reconcile all canonical fields in a stable, deterministic order."""

    if not isinstance(fund_record, FundRecord):
        fund_record = FundRecord.model_validate(fund_record)
    if not isinstance(document, ExtractedDocument):
        document = ExtractedDocument.model_validate(document)

    if fund_record.case_id != document.case_id:
        raise ValueError("fund record and extracted document case_id values must match")

    extracted_fields = dict(document.fields)
    if "document_type" not in extracted_fields:
        extracted_fields["document_type"] = ExtractedField(
            value=document.document_type.value,
            source=document.source_document,
            page=None,
            confidence=0.90,
            evidence=None,
            method=document.extraction_method,
        )

    results = [
        reconcile_field(
            field_name,
            expected,
            extracted_fields.get(field_name),
            currency=fund_record.currency,
            numeric_tolerance=numeric_tolerance,
            confidence_threshold=confidence_threshold,
        )
        for field_name, expected in fund_record.reconciliation_values().items()
    ]

    counts = {status.value: 0 for status in ReconciliationStatus}
    for result in results:
        counts[result.status.value] += 1

    if counts[ReconciliationStatus.MISMATCH.value]:
        overall = ReconciliationStatus.MISMATCH
    elif counts[ReconciliationStatus.MISSING.value]:
        overall = ReconciliationStatus.MISSING
    elif counts[ReconciliationStatus.REVIEW.value]:
        overall = ReconciliationStatus.REVIEW
    else:
        overall = ReconciliationStatus.PASS

    return ReconciliationReport(
        case_id=document.case_id or fund_record.case_id,
        source_document=document.source_document,
        document_type=document.document_type,
        overall_status=overall,
        results=results,
        counts=counts,
    )


# Backwards-friendly name for callers that treat extraction as another record.
reconcile_records = reconcile_document
