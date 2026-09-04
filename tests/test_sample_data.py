from decimal import Decimal

from app.models import ReconciliationStatus
from app.sample_data import load_demo_case


def test_matching_demo_case_is_completely_clean():
    _, document, report = load_demo_case("matching")

    assert len(document.fields) == 10
    assert report.overall_status is ReconciliationStatus.PASS
    assert report.counts["PASS"] == 10
    assert report.exceptions == []


def test_discrepancy_demo_case_contains_flagship_amount_break():
    record, document, report = load_demo_case("discrepancy")

    assert record.investor_name == "Alderstone Civic Pension Partnership"
    assert document.source_document == "capital_call_notice.pdf"
    assert [item.field for item in report.exceptions] == [
        "capital_call_amount",
        "due_date",
    ]
    assert report.exceptions[0].difference == Decimal("25000.00")
    assert report.exceptions[1].difference == -2
    assert all(
        item.status is ReconciliationStatus.MISMATCH
        for item in report.exceptions
    )
