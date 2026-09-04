from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from app.models import (
    DocumentType,
    ExtractedDocument,
    ExtractedField,
    ExtractionMethod,
    FundRecord,
    ReconciliationItem,
    ReconciliationReport,
    ReconciliationStatus,
    Severity,
)
from app.review import (
    ReviewFinding,
    ReviewMethod,
    ReviewReport,
    ReviewSourceReference,
    ReviewStatus,
)


EXPECTED_TABS = [
    "Reconciliation Results",
    "Exception Queue (2)",
    "Audit Log",
    "Fund Record",
    "Extraction Ledger",
    "Evals",
]


def _button(app: AppTest, *, label: str, key: str | None = None):
    return next(
        button
        for button in app.button
        if button.label == label and (key is None or button.key == key)
    )


def _dataframe_with_column(app: AppTest, column: str):
    return next(frame for frame in app.dataframe if column in frame.value.columns)


def _reviewer_only_escalation_app(
    monkeypatch,
    tmp_path,
    review_status: ReviewStatus,
) -> AppTest:
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    case_id = f"reviewer-only-{review_status.value.casefold()}"
    record = FundRecord(
        case_id=case_id,
        fund_name="Northstar Growth Fund II",
        investor_name="Alderstone Civic Pension Partnership",
        commitment_amount=Decimal("10000000"),
        capital_call_amount=Decimal("625000"),
        call_date=date(2026, 9, 1),
        due_date=date(2026, 9, 30),
        currency="GBP",
    )
    extracted = ExtractedField(
        value=Decimal("625000"),
        source="reviewer-only.pdf",
        page=1,
        confidence=0.99,
        evidence="Capital Call Amount: GBP 625,000",
        method=ExtractionMethod.DETERMINISTIC,
    )
    document = ExtractedDocument(
        case_id=case_id,
        source_document="reviewer-only.pdf",
        document_type=DocumentType.CAPITAL_CALL,
        fields={"capital_call_amount": extracted},
        extraction_method=ExtractionMethod.DETERMINISTIC,
    )
    item = ReconciliationItem(
        field="capital_call_amount",
        expected=Decimal("625000"),
        observed=Decimal("625000"),
        status=ReconciliationStatus.PASS,
        severity=Severity.NONE,
        difference=Decimal("0"),
        explanation="Values match exactly",
        provenance=extracted,
    )
    report = ReconciliationReport(
        case_id=case_id,
        source_document=document.source_document,
        document_type=document.document_type,
        overall_status=ReconciliationStatus.PASS,
        results=[item],
        counts={
            status.value: int(status is ReconciliationStatus.PASS)
            for status in ReconciliationStatus
        },
    )
    finding = ReviewFinding(
        case_id=case_id,
        source_document=document.source_document,
        field=item.field,
        reviewed_value=item.observed,
        status=review_status,
        review_reason="The supplied evidence needs human confirmation.",
        challenged_value=(
            Decimal("650000") if review_status is ReviewStatus.CHALLENGE else None
        ),
        source_references=[
            ReviewSourceReference(
                source=document.source_document,
                page=1,
                evidence=extracted.evidence,
            )
        ],
        reconciliation_status=item.status,
        requires_human_review=True,
        review_method=ReviewMethod.DETERMINISTIC_FIXTURE,
    )
    review_report = ReviewReport(
        case_id=case_id,
        source_document=document.source_document,
        findings=[finding],
        counts={status.value: int(status is review_status) for status in ReviewStatus},
    )

    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
    app.session_state.record = record
    app.session_state.document = document
    app.session_state.report = report
    app.session_state.review_report = review_report
    app.session_state.case_name = "Reviewer-only escalation"
    app.session_state.source_display_name = document.source_document
    app.session_state.document_id = f"sha256:{review_status.value.casefold()}"
    app.session_state.selected_field = item.field
    return app.run()


def test_one_click_demo_loads_complete_exception_workflow(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()

    assert list(app.exception) == []
    _button(app, label="Load Demo Case", key="load_northstar").click().run()

    assert list(app.exception) == []
    assert [tab.label for tab in app.tabs] == EXPECTED_TABS
    reconciliation = app.dataframe[0].value
    assert list(reconciliation.columns) == [
        "Field",
        "Expected",
        "Observed",
        "Status",
        "Severity",
        "Source",
        "Review status",
    ]
    exceptions = reconciliation[reconciliation["Status"] != "PASS"]
    assert exceptions[["Field", "Expected", "Observed", "Status", "Severity"]].to_dict(
        "records"
    ) == [
        {
            "Field": "Capital call",
            "Expected": "GBP 625,000",
            "Observed": "GBP 650,000",
            "Status": "MISMATCH",
            "Severity": "HIGH",
        },
        {
            "Field": "Due date",
            "Expected": "30 Sep 2026",
            "Observed": "28 Sep 2026",
            "Status": "MISMATCH",
            "Severity": "HIGH",
        },
    ]
    assert any("SUPPORTED:" in block.value for block in app.markdown)
    assert any("LP Register!I2" in block.value for block in app.markdown)
    assert any("Pages</span><span class=\"value\">2" in block.value for block in app.markdown)
    assert any("Actual executable benchmark" in block.value for block in app.markdown)
    assert any("Field exception recall" in block.value for block in app.markdown)
    assert any("Isolated rule correctness" in block.value for block in app.markdown)
    assert any("Regression gates" in block.value for block in app.markdown)
    assert len(_dataframe_with_column(app, "Failure").value) == 14

    app.text_input[0].input("Confirm with administrator")
    _button(app, label="Needs investigation").click().run()

    assert list(app.exception) == []
    audit_rows = _dataframe_with_column(app, "Human action").value.to_dict("records")
    assert audit_rows[0]["Field"] == "Capital call"
    assert audit_rows[0]["Expected"] == "GBP 625,000"
    assert audit_rows[0]["Observed"] == "GBP 650,000"
    assert audit_rows[0]["Variance"] == "GBP +25,000.00"
    assert audit_rows[0]["Evidence review"].startswith("SUPPORTED")
    assert audit_rows[0]["Human action"] == "Needs investigation"
    assert audit_rows[0]["Reason"] == "Confirm with administrator"
    assert audit_rows[0]["Source"] == "capital_call_notice.pdf"
    assert audit_rows[0]["Location"] == "PDF page 1"
    assert audit_rows[0]["Package digest"].startswith("sha256:")


def test_one_click_matching_case_has_no_exception_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()

    _button(app, label="Load Clean Match").click().run()

    assert list(app.exception) == []
    assert "Exception Queue (0)" in [tab.label for tab in app.tabs]
    assert any("All controls passed" in message.value for message in app.success)


@pytest.mark.parametrize(
    "review_status",
    [ReviewStatus.CHALLENGE, ReviewStatus.INSUFFICIENT_EVIDENCE],
)
def test_reviewer_only_escalation_reaches_banner_summary_table_and_queue(
    monkeypatch,
    tmp_path,
    review_status,
):
    app = _reviewer_only_escalation_app(
        monkeypatch,
        tmp_path,
        review_status,
    )

    assert list(app.exception) == []
    assert "Exception Queue (1)" in [tab.label for tab in app.tabs]
    banner = next(
        block.value
        for block in app.markdown
        if '<div class="case-strip ' in block.value
    )
    assert "REVIEW REQUIRED" in banner
    assert "ALL CONTROLS PASSED" not in banner
    assert any(
        '<div class="metric-label">Exceptions</div>' in block.value
        and '<div class="metric-value red">1</div>' in block.value
        for block in app.markdown
    )

    reconciliation = _dataframe_with_column(app, "Review status").value
    assert reconciliation.iloc[0]["Status"] == "PASS"
    assert reconciliation.iloc[0]["Review status"] == "Awaiting review"
    exception_selector = next(
        selectbox for selectbox in app.selectbox if selectbox.label == "Exception"
    )
    assert exception_selector.value == "capital_call_amount"
    assert any(f"{review_status.value}:" in block.value for block in app.markdown)


def test_audit_decision_does_not_leak_between_demo_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
    _button(app, label="Load Demo Case", key="load_northstar").click().run()
    app.text_input[0].input("Confirm with administrator")
    _button(app, label="Approved").click().run()
    assert len(_dataframe_with_column(app, "Human action").value) == 1

    _button(app, label="Load Clean Match").click().run()

    assert "Exception Queue (0)" in [tab.label for tab in app.tabs]
    assert all(
        "Human action" not in frame.value.columns for frame in app.dataframe
    )
    assert all(
        status == "Not required"
        for status in app.dataframe[0].value["Review status"].tolist()
    )
