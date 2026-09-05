"""Deterministic reconciliation rules for extracted fund documents."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Mapping
import unicodedata

from .models import (
    DocumentType,
    ExtractedDocument,
    ExtractedField,
    FieldValue,
    FundRecord,
    ReconciliationItem,
    ReconciliationReport,
    ReconciliationStatus,
    Severity,
)
from .normalization import (
    AmbiguousValueError,
    NormalizationError,
    NormalizedMoney,
    UnsupportedValueError,
    normalize_currency_code,
    normalize_monetary_evidence,
    normalize_monetary_value,
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
    return normalize_monetary_value(value).amount


def _normalized_currency_or_none(value: object) -> str | None:
    if _is_missing(value):
        return None
    try:
        return normalize_currency_code(value)
    except NormalizationError:
        return None


def _monetary_context(
    expected_currency: object,
    observed_currency: object,
    currency_status: ReconciliationStatus | None,
) -> dict[str, object]:
    return {
        "expected_currency": _normalized_currency_or_none(expected_currency),
        "observed_currency": _normalized_currency_or_none(observed_currency),
        "currency_status": currency_status,
    }


def _normalize_extracted_money(extracted: ExtractedField) -> NormalizedMoney:
    """Cross-check a structured amount against its cited source evidence."""

    normalized = normalize_monetary_value(extracted.value)
    if not extracted.evidence:
        return normalized

    if "%" in extracted.evidence:
        raise UnsupportedValueError("percentage evidence is not a monetary amount")
    try:
        evidence = normalize_monetary_evidence(extracted.evidence)
    except UnsupportedValueError:
        # OCR-corrupted evidence cannot validate or invalidate an already
        # structured numeric value. Evidence support remains the independent
        # reviewer's concern; ambiguous or parseable contradictory evidence
        # still forces reconciliation to abstain below.
        return normalized
    if evidence.amount != normalized.amount:
        raise AmbiguousValueError(
            "structured monetary value conflicts with its source evidence"
        )
    if (
        normalized.currency is not None
        and evidence.currency is not None
        and normalized.currency != evidence.currency
    ):
        raise AmbiguousValueError(
            "structured monetary currency conflicts with its source evidence"
        )
    return NormalizedMoney(
        amount=normalized.amount,
        currency=normalized.currency or evidence.currency,
    )


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


def _low_confidence_severity(
    field_name: str,
    expected: FieldValue,
    extracted: ExtractedField,
    base_severity: Severity,
) -> Severity:
    """Keep matching low-confidence noise low, but prioritize uncertain breaks."""

    observed = extracted.value
    try:
        if field_name in NUMERIC_FIELDS:
            values_match = (
                _as_decimal(expected) == _normalize_extracted_money(extracted).amount
            )
        elif field_name in DATE_FIELDS:
            values_match = _as_date(expected) == _as_date(observed)
        elif field_name == "currency":
            values_match = (
                normalize_currency_code(expected)
                == normalize_currency_code(observed)
            )
        elif field_name == "bank_account_reference":
            values_match = (
                _normalize_reference(expected) == _normalize_reference(observed)
            )
        else:
            values_match = _normalize_text(expected) == _normalize_text(observed)
    except (NormalizationError, TypeError, ValueError):
        return base_severity
    return Severity.LOW if values_match else base_severity


def reconcile_field(
    field_name: str,
    expected: FieldValue,
    extracted: ExtractedField | None,
    *,
    currency: str = "",
    observed_currency: str | None = None,
    currency_status: ReconciliationStatus | None = None,
    numeric_tolerance: Decimal = Decimal("0"),
    confidence_threshold: float = 0.80,
) -> ReconciliationItem:
    """Compare one field without invoking an LLM or any external service."""

    observed = extracted.value if extracted else None
    base_severity = _FIELD_SEVERITY.get(field_name, Severity.MEDIUM)
    monetary_context = (
        _monetary_context(currency, observed_currency, currency_status)
        if field_name in NUMERIC_FIELDS
        else {}
    )

    if extracted is not None and extracted.abstention_reason is not None:
        return ReconciliationItem(
            field=field_name,
            expected=expected,
            observed=observed,
            status=ReconciliationStatus.REVIEW,
            severity=base_severity,
            explanation=(
                "Extraction abstained from this field: "
                f"{extracted.abstention_reason}"
            ),
            provenance=extracted,
            **monetary_context,
        )

    if _is_missing(expected) and _is_missing(observed):
        return ReconciliationItem(
            field=field_name,
            expected=expected,
            observed=observed,
            status=ReconciliationStatus.PASS,
            severity=Severity.NONE,
            explanation="No value is expected and none is present in the document",
            provenance=extracted,
            **monetary_context,
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
            **monetary_context,
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
            **monetary_context,
        )

    if extracted is not None and extracted.confidence < confidence_threshold:
        return ReconciliationItem(
            field=field_name,
            expected=expected,
            observed=observed,
            status=ReconciliationStatus.REVIEW,
            severity=_low_confidence_severity(
                field_name, expected, extracted, base_severity
            ),
            explanation=(
                "Extraction confidence is below the control threshold; "
                "deterministic comparison abstained"
            ),
            provenance=extracted,
            **monetary_context,
        )

    if field_name in NUMERIC_FIELDS:
        try:
            expected_number = _as_decimal(expected)
            observed_money = _normalize_extracted_money(extracted)
            observed_number = observed_money.amount
            if monetary_context["observed_currency"] is None:
                monetary_context["observed_currency"] = observed_money.currency
            elif (
                observed_money.currency is not None
                and observed_money.currency
                != monetary_context["observed_currency"]
            ):
                raise AmbiguousValueError(
                    "amount evidence conflicts with the document currency"
                )
        except NormalizationError:
            return ReconciliationItem(
                field=field_name,
                expected=expected,
                observed=observed,
                status=ReconciliationStatus.REVIEW,
                severity=base_severity,
                explanation=(
                    "Extracted number could not be normalized safely; "
                    "deterministic comparison abstained"
                ),
                provenance=extracted,
                **monetary_context,
            )

        difference = observed_number - expected_number
        if abs(difference) <= numeric_tolerance:
            if currency_status is ReconciliationStatus.PASS:
                explanation = (
                    "Document numeric units match the fund record; currency "
                    "matches in the separate currency control"
                )
            elif currency_status is not None:
                explanation = (
                    "Document numeric units match the fund record; monetary "
                    "equivalence is not asserted because the separate currency "
                    f"control is {currency_status.value}"
                )
            else:
                explanation = (
                    "Document numeric units match the fund record; currency is "
                    "controlled separately"
                )
            return ReconciliationItem(
                field=field_name,
                expected=expected_number,
                observed=observed_number,
                status=ReconciliationStatus.PASS,
                severity=Severity.NONE,
                difference=difference,
                explanation=explanation,
                provenance=extracted,
                **monetary_context,
            )

        direction = "higher" if difference > 0 else "lower"
        if currency_status in (None, ReconciliationStatus.PASS):
            difference_description = _money(abs(difference), currency)
        else:
            difference_description = f"{abs(difference):f} numeric units"
        return ReconciliationItem(
            field=field_name,
            expected=expected_number,
            observed=observed_number,
            status=ReconciliationStatus.MISMATCH,
            severity=base_severity,
            difference=difference,
            explanation=(
                f"Document numeric value is {difference_description} {direction} "
                "than the fund record; currency is controlled separately"
            ),
            provenance=extracted,
            **monetary_context,
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

    if field_name == "currency":
        try:
            expected_code = normalize_currency_code(expected)
            observed_code = normalize_currency_code(observed)
        except NormalizationError:
            return ReconciliationItem(
                field=field_name,
                expected=expected,
                observed=observed,
                status=ReconciliationStatus.REVIEW,
                severity=base_severity,
                explanation=(
                    "Extracted currency is unsupported; deterministic comparison "
                    "abstained"
                ),
                provenance=extracted,
            )

        if expected_code == observed_code:
            return ReconciliationItem(
                field=field_name,
                expected=expected,
                observed=observed,
                status=ReconciliationStatus.PASS,
                severity=Severity.NONE,
                explanation="Document currency matches the fund record",
                provenance=extracted,
            )
        return ReconciliationItem(
            field=field_name,
            expected=expected,
            observed=observed,
            status=ReconciliationStatus.MISMATCH,
            severity=base_severity,
            explanation="Document currency does not match the fund record",
            provenance=extracted,
        )

    if field_name == "document_type":
        try:
            expected_type = DocumentType(str(getattr(expected, "value", expected)).upper())
            observed_type = DocumentType(str(getattr(observed, "value", observed)).upper())
        except ValueError:
            return ReconciliationItem(
                field=field_name,
                expected=expected,
                observed=observed,
                status=ReconciliationStatus.REVIEW,
                severity=base_severity,
                explanation=(
                    "Extracted document type is unsupported; deterministic "
                    "comparison abstained"
                ),
                provenance=extracted,
            )
        if observed_type is DocumentType.UNKNOWN and expected_type is not DocumentType.UNKNOWN:
            return ReconciliationItem(
                field=field_name,
                expected=expected,
                observed=observed,
                status=ReconciliationStatus.REVIEW,
                severity=base_severity,
                explanation=(
                    "Document type is unknown; deterministic comparison abstained"
                ),
                provenance=extracted,
            )

    if field_name == "bank_account_reference":
        values_match = _normalize_reference(expected) == _normalize_reference(observed)
    else:
        values_match = _normalize_text(expected) == _normalize_text(observed)

    if values_match:
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

    record_values = fund_record.reconciliation_values()
    currency_field = extracted_fields.get("currency")
    currency_result = reconcile_field(
        "currency",
        record_values["currency"],
        currency_field,
        confidence_threshold=confidence_threshold,
    )
    document_currency = currency_field.value if currency_field is not None else None

    results = []
    for field_name, expected in record_values.items():
        if field_name == "currency":
            results.append(currency_result)
            continue
        results.append(
            reconcile_field(
                field_name,
                expected,
                extracted_fields.get(field_name),
                currency=fund_record.currency,
                observed_currency=document_currency,
                currency_status=currency_result.status,
                numeric_tolerance=numeric_tolerance,
                confidence_threshold=confidence_threshold,
            )
        )

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
