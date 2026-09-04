from __future__ import annotations

from streamlit.testing.v1 import AppTest


EXPECTED_TABS = [
    "Reconciliation Results",
    "Exception Queue (2)",
    "Audit Log",
    "Fund Record",
    "Extraction Ledger",
]


def _button(app: AppTest, *, label: str, key: str | None = None):
    return next(
        button
        for button in app.button
        if button.label == label and (key is None or button.key == key)
    )


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
            "Expected": "18 Sep 2026",
            "Observed": "20 Sep 2026",
            "Status": "MISMATCH",
            "Severity": "HIGH",
        },
    ]
    assert any("SUPPORTED:" in block.value for block in app.markdown)

    app.text_input[0].input("Confirm with administrator")
    _button(app, label="Needs investigation").click().run()

    assert list(app.exception) == []
    audit_rows = app.dataframe[1].value.to_dict("records")
    assert audit_rows[0]["Field"] == "Capital call"
    assert audit_rows[0]["Action"] == "Needs investigation"
    assert audit_rows[0]["Reason"] == "Confirm with administrator"
    assert audit_rows[0]["Source"] == "discrepancy_capital_call.pdf"


def test_one_click_matching_case_has_no_exception_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()

    _button(app, label="Load Clean Match").click().run()

    assert list(app.exception) == []
    assert "Exception Queue (0)" in [tab.label for tab in app.tabs]
    assert any("All controls passed" in message.value for message in app.success)


def test_audit_decision_does_not_leak_between_demo_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
    _button(app, label="Load Demo Case", key="load_northstar").click().run()
    app.text_input[0].input("Confirm with administrator")
    _button(app, label="Approved").click().run()
    assert len(app.dataframe) == 2

    _button(app, label="Load Clean Match").click().run()

    assert "Exception Queue (0)" in [tab.label for tab in app.tabs]
    assert len(app.dataframe) == 1
    assert all(
        status == "Not required"
        for status in app.dataframe[0].value["Review status"].tolist()
    )
