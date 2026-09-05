from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import re
from types import SimpleNamespace
from uuid import UUID

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
from app.errors import WorkflowStage
from app.reconciliation import reconcile_document
from app.storage import AuditStore
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
    assert len(_dataframe_with_column(app, "Failure").value) == 10
    assert any("Code commit" in block.value for block in app.markdown)
    assert any("Worktree" in block.value for block in app.markdown)

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
    stored_event = AuditStore(tmp_path / "audit.db").list_events()[0]
    assert stored_event.expected_currency == "GBP"
    assert stored_event.observed_currency == "GBP"
    UUID(stored_event.request_id)


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


def test_demo_uses_one_request_id_across_all_observed_stages(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    request_id = "6929230f-73d2-4b5e-8973-184922a572ad"
    observed: list[tuple[str, str]] = []

    @contextmanager
    def capture_stage(correlation_id, stage, **_kwargs):
        observed.append((correlation_id, getattr(stage, "value", str(stage))))
        yield

    monkeypatch.setattr("app.observability.new_request_id", lambda: request_id)
    monkeypatch.setattr("app.observability.observe_workflow_stage", capture_stage)

    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
    _button(app, label="Load Demo Case", key="load_northstar").click().run()

    assert list(app.exception) == []
    assert app.session_state.workflow_request_id == request_id
    assert observed == [
        (request_id, WorkflowStage.FILE_VALIDATION.value),
        (request_id, WorkflowStage.DETERMINISTIC_EXTRACTION.value),
        (request_id, WorkflowStage.RECONCILIATION.value),
        (request_id, WorkflowStage.INDEPENDENT_REVIEW.value),
    ]


def test_failed_followup_preserves_last_good_workflow(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
    _button(app, label="Load Demo Case", key="load_northstar").click().run()
    last_document_id = app.session_state.document_id
    last_case_name = app.session_state.case_name

    secret = "sk-private source document body"

    def fail_validation(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr("app.file_handling.validate_upload", fail_validation)
    _button(app, label="Load Clean Match").click().run()

    assert list(app.exception) == []
    assert app.session_state.document_id == last_document_id
    assert app.session_state.case_name == last_case_name
    UUID(app.session_state.workflow_request_id)
    rendered_errors = " ".join(message.value for message in app.error)
    assert "workflow could not be completed" in rendered_errors.lower()
    assert secret not in rendered_errors


def test_optional_model_controls_are_disabled_without_a_key(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
    controls = {checkbox.label: checkbox for checkbox in app.checkbox}

    assert controls["Use OpenAI-compatible extraction"].disabled is True
    assert controls["Use OpenAI-compatible evidence review"].disabled is True
    _button(app, label="Load Demo Case", key="load_northstar").click().run()
    assert list(app.exception) == []


def test_cross_currency_amounts_keep_their_own_units(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    case_id = "cross-currency-ui"
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
    amount = ExtractedField(
        value=Decimal("625000"),
        source="usd-notice.txt",
        page=1,
        confidence=0.99,
        evidence="Capital Call Amount: USD 625,000",
        method=ExtractionMethod.DETERMINISTIC,
    )
    currency = ExtractedField(
        value="USD",
        source="usd-notice.txt",
        page=1,
        confidence=0.99,
        evidence="Currency: USD",
        method=ExtractionMethod.DETERMINISTIC,
    )
    document = ExtractedDocument(
        case_id=case_id,
        source_document="usd-notice.txt",
        document_type=DocumentType.CAPITAL_CALL,
        fields={"capital_call_amount": amount, "currency": currency},
        extraction_method=ExtractionMethod.DETERMINISTIC,
    )
    report = reconcile_document(record, document)

    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
    app.session_state.record = record
    app.session_state.document = document
    app.session_state.report = report
    app.session_state.case_name = "Cross-currency notice"
    app.session_state.source_display_name = document.source_document
    app.session_state.document_id = "sha256:cross-currency-ui"
    app.session_state.selected_field = "currency"
    app = app.run()

    assert list(app.exception) == []
    frame = _dataframe_with_column(app, "Review status").value
    amount_row = frame.loc[frame["Field"] == "Capital call"].iloc[0]
    currency_row = frame.loc[frame["Field"] == "Currency"].iloc[0]
    assert amount_row["Expected"] == "GBP 625,000"
    assert amount_row["Observed"] == "USD 625,000"
    assert currency_row["Status"] == "MISMATCH"


def test_upload_correlates_stages_and_no_key_model_modes_fail_safely(
    monkeypatch,
):
    import streamlit_app as ui

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    request_id = "01a41259-e7af-467e-94c8-156f97dc17b5"
    observed: list[tuple[str, str]] = []

    @contextmanager
    def capture_stage(correlation_id, stage, **_kwargs):
        observed.append((correlation_id, getattr(stage, "value", str(stage))))
        yield

    class State(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    class Upload:
        name = "notice.txt"
        type = "text/plain"

        @staticmethod
        def getvalue():
            return b"""Capital Call Notice
Fund Name: Northstar Growth Fund II
Investor Name: Alderstone Civic Pension Partnership
Capital Call Amount: GBP 625,000
Call Date: 1 September 2026
Due Date: 30 September 2026
Currency: GBP
"""

    state = State(
        record=FundRecord(
            case_id="upload-model-fallback",
            fund_name="Northstar Growth Fund II",
            investor_name="Alderstone Civic Pension Partnership",
            commitment_amount=Decimal("10000000"),
            capital_call_amount=Decimal("625000"),
            call_date=date(2026, 9, 1),
            due_date=date(2026, 9, 30),
            currency="GBP",
        ),
        use_ai_extraction=True,
        use_ai_review=True,
    )
    monkeypatch.setattr(ui, "st", SimpleNamespace(session_state=state))
    monkeypatch.setattr(ui, "observe_workflow_stage", capture_stage)

    assert ui._process_upload(Upload(), request_id=request_id) == request_id

    assert state.workflow_request_id == request_id
    assert observed == [
        (request_id, WorkflowStage.FILE_VALIDATION.value),
        (request_id, WorkflowStage.DETERMINISTIC_EXTRACTION.value),
        (request_id, WorkflowStage.RECONCILIATION.value),
        (request_id, WorkflowStage.INDEPENDENT_REVIEW.value),
    ]
    assert any(
        finding.status is ReviewStatus.NOT_REVIEWED
        for finding in state.review_report.findings
    )


def test_failed_audit_append_is_observed_and_only_shows_safe_reference(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
    _button(app, label="Load Demo Case", key="load_northstar").click().run()
    observed: list[str] = []

    @contextmanager
    def capture_stage(_request_id, stage, **_kwargs):
        observed.append(getattr(stage, "value", str(stage)))
        yield

    secret = "sk-private audit backend detail"

    def fail_append(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr("app.observability.observe_workflow_stage", capture_stage)
    monkeypatch.setattr("app.storage.AuditStore.append", fail_append)
    app.text_input[0].input("Confirm with administrator")
    _button(app, label="Needs investigation").click().run()

    assert list(app.exception) == []
    assert observed == [WorkflowStage.AUDIT_APPEND.value]
    rendered_errors = " ".join(message.value for message in app.error)
    assert "could not be recorded" in rendered_errors
    assert secret not in rendered_errors
    assert re.search(
        r"Reference: [0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        rendered_errors,
    )
    assert all(
        "Human action" not in frame.value.columns for frame in app.dataframe
    )
