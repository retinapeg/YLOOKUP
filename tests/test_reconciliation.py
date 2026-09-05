from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import (
    DocumentType,
    ExtractedDocument,
    ExtractedField,
    FundRecord,
    ReconciliationStatus,
    Severity,
)
from app.reconciliation import reconcile_document
from app.normalization import (
    AmbiguousValueError,
    normalize_monetary_value,
)


def _record() -> FundRecord:
    return FundRecord(
        case_id="northstar-call-04",
        fund_name="Northstar Growth Fund II",
        investor_name="Albion Capital Partners",
        commitment_amount=Decimal("5000000"),
        capital_call_amount=Decimal("625000"),
        call_date=date(2026, 9, 4),
        due_date=date(2026, 9, 18),
        currency="GBP",
        bank_account_reference="NSGF2-ALBION-4471",
        management_fee=Decimal("75000"),
        document_type=DocumentType.CAPITAL_CALL,
    )


def _field(name: str, value, confidence: float = 0.98) -> ExtractedField:
    return ExtractedField(
        value=value,
        source="notice.pdf",
        page=1,
        confidence=confidence,
        evidence="{}: {}".format(name, value),
    )


def _document(**overrides) -> ExtractedDocument:
    values = _record().reconciliation_values()
    values.update(overrides)
    return ExtractedDocument(
        case_id="northstar-call-04",
        source_document="notice.pdf",
        document_type=DocumentType.CAPITAL_CALL,
        fields={name: _field(name, value) for name, value in values.items()},
    )


def _by_field(report, name):
    return next(item for item in report.results if item.field == name)


def test_exact_match_passes_all_controls():
    report = reconcile_document(_record(), _document())

    assert report.overall_status is ReconciliationStatus.PASS
    assert report.counts["PASS"] == 10
    assert report.exceptions == []


def test_demo_discrepancies_are_numeric_and_date_mismatches():
    report = reconcile_document(
        _record(),
        _document(
            capital_call_amount=Decimal("650000"),
            due_date=date(2026, 9, 20),
        ),
    )

    assert [item.field for item in report.exceptions] == [
        "capital_call_amount",
        "due_date",
    ]
    amount = _by_field(report, "capital_call_amount")
    due_date = _by_field(report, "due_date")
    assert amount.status is ReconciliationStatus.MISMATCH
    assert amount.severity is Severity.HIGH
    assert amount.difference == Decimal("25000")
    assert due_date.status is ReconciliationStatus.MISMATCH
    assert due_date.severity is Severity.HIGH
    assert due_date.difference == 2


def test_one_penny_difference_is_not_rounded_away():
    report = reconcile_document(
        _record(), _document(capital_call_amount=Decimal("625000.01"))
    )

    assert _by_field(report, "capital_call_amount").status is ReconciliationStatus.MISMATCH


def test_missing_required_field_is_flagged():
    document = _document()
    document.fields.pop("due_date")

    result = _by_field(reconcile_document(_record(), document), "due_date")

    assert result.status is ReconciliationStatus.MISSING
    assert result.severity is Severity.HIGH


def test_not_available_marker_is_treated_as_missing():
    report = reconcile_document(_record(), _document(due_date="N/A"))

    assert _by_field(report, "due_date").status is ReconciliationStatus.MISSING


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_amount_requires_review_instead_of_raising(value):
    report = reconcile_document(_record(), _document(capital_call_amount=value))

    result = _by_field(report, "capital_call_amount")
    assert result.status is ReconciliationStatus.REVIEW
    assert "number" in result.explanation


def test_equal_low_confidence_value_requires_review():
    document = _document()
    document.fields["fund_name"] = _field(
        "fund_name", "Northstar Growth Fund II", confidence=0.62
    )

    result = _by_field(reconcile_document(_record(), document), "fund_name")

    assert result.status is ReconciliationStatus.REVIEW
    assert result.severity is Severity.LOW


def test_bank_reference_formatting_does_not_create_false_mismatch():
    report = reconcile_document(
        _record(), _document(bank_account_reference="nsgf2 albion 4471")
    )

    assert _by_field(report, "bank_account_reference").status is ReconciliationStatus.PASS


def test_case_mismatch_is_rejected():
    document = _document()
    document.case_id = "another-case"

    with pytest.raises(ValueError, match="case_id"):
        reconcile_document(_record(), document)


def test_provenance_supports_pdf_and_workbook_locators():
    timestamp = datetime(2026, 9, 5, 9, 30, tzinfo=timezone.utc)
    pdf_field = ExtractedField(
        value="Albion Capital Partners",
        source="notice.pdf",
        source_type="application/pdf",
        page=4,
        confidence=0.97,
        evidence="Investor: Albion Capital Partners",
        extractor="deterministic-label-parser-v1",
        timestamp=timestamp,
    )
    workbook_field = ExtractedField(
        value=Decimal("625000"),
        source="register.xlsx",
        source_type="workbook",
        sheet="LP Register",
        cell="G42",
        confidence=1.0,
        evidence="625000",
        extractor="openpyxl-cell-reader-v1",
        timestamp=timestamp,
    )

    assert pdf_field.page == 4
    assert pdf_field.timestamp == timestamp
    assert workbook_field.sheet == "LP Register"
    assert workbook_field.cell == "G42"


def test_provenance_rejects_incoherent_locators_and_naive_timestamp():
    common = {
        "value": "value",
        "source": "source",
        "confidence": 0.9,
    }

    with pytest.raises(ValidationError, match="cell requires"):
        ExtractedField(**common, cell="A1")
    with pytest.raises(ValidationError, match="page cannot be combined"):
        ExtractedField(**common, page=1, sheet="Register")
    with pytest.raises(ValidationError, match="timezone-aware"):
        ExtractedField(**common, timestamp=datetime(2026, 9, 5, 9, 30))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("625,00", Decimal("625.00")),
        ("625,000", Decimal("625000")),
        ("EUR 625.000,00", Decimal("625000.00")),
        ("USD 625,000.00", Decimal("625000.00")),
        ("(GBP 1,234.56)", Decimal("-1234.56")),
    ],
)
def test_locale_and_accounting_money_normalization_is_deterministic(raw, expected):
    assert normalize_monetary_value(raw).amount == expected


@pytest.mark.parametrize("raw", ["0.125", "0,125", "62,50,0"])
def test_ambiguous_monetary_values_fail_closed(raw):
    with pytest.raises(AmbiguousValueError):
        normalize_monetary_value(raw)


def test_upstream_comma_decimal_inflation_is_caught_from_evidence():
    document = _document()
    document.fields["capital_call_amount"] = ExtractedField(
        value=Decimal("62500"),
        source="notice.pdf",
        source_type="application/pdf",
        page=1,
        confidence=0.99,
        evidence="Capital Call Amount: GBP 625,00",
    )

    result = _by_field(reconcile_document(_record(), document), "capital_call_amount")

    assert result.status is ReconciliationStatus.REVIEW
    assert result.severity is Severity.HIGH
    assert result.difference is None
    assert "abstained" in result.explanation


def test_low_confidence_mismatch_abstains_before_comparison():
    document = _document()
    document.fields["capital_call_amount"] = _field(
        "capital_call_amount", Decimal("650000"), confidence=0.60
    )

    result = _by_field(reconcile_document(_record(), document), "capital_call_amount")

    assert result.status is ReconciliationStatus.REVIEW
    assert result.severity is Severity.HIGH
    assert result.difference is None
    assert "abstained" in result.explanation


def test_explicit_ambiguous_extraction_abstains_even_with_a_value():
    document = _document()
    document.fields["fund_name"] = ExtractedField(
        value="Northstar Growth Fund II",
        source="notice.pdf",
        page=1,
        confidence=0.99,
        evidence="Northstar Growth Fund II / III",
        abstention_reason="conflicting candidate values",
    )

    result = _by_field(reconcile_document(_record(), document), "fund_name")

    assert result.status is ReconciliationStatus.REVIEW
    assert result.severity is Severity.HIGH


def test_percentage_presented_as_amount_abstains():
    document = _document()
    document.fields["capital_call_amount"] = ExtractedField(
        value=Decimal("12.5"),
        source="notice.pdf",
        page=1,
        confidence=0.99,
        evidence="Capital Call Amount: 12.5%",
    )

    result = _by_field(reconcile_document(_record(), document), "capital_call_amount")

    assert result.status is ReconciliationStatus.REVIEW
    assert result.difference is None


def test_numeric_control_exposes_currency_context_without_claiming_equivalence():
    report = reconcile_document(_record(), _document(currency="USD"))

    currency = _by_field(report, "currency")
    amount = _by_field(report, "capital_call_amount")
    assert currency.status is ReconciliationStatus.MISMATCH
    assert amount.status is ReconciliationStatus.PASS
    assert amount.expected_currency == "GBP"
    assert amount.observed_currency == "USD"
    assert amount.currency_status is ReconciliationStatus.MISMATCH
    assert "equivalence is not asserted" in amount.explanation


def test_unsupported_currency_abstains():
    result = _by_field(
        reconcile_document(_record(), _document(currency="US D")), "currency"
    )

    assert result.status is ReconciliationStatus.REVIEW
    assert "unsupported" in result.explanation
