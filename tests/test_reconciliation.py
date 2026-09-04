from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models import (
    DocumentType,
    ExtractedDocument,
    ExtractedField,
    FundRecord,
    ReconciliationStatus,
    Severity,
)
from app.reconciliation import reconcile_document


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
