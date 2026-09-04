from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_one_click_demo_loads_complete_exception_workflow(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()

    assert list(app.exception) == []
    assert "Load Demo Case" in [button.label for button in app.button]

    app.button[0].click().run()

    assert list(app.exception) == []
    assert [tab.label for tab in app.tabs] == [
        "Reconciliation Results",
        "Extraction Ledger",
        "Exception Queue (2)",
        "Audit Log",
    ]
    exception_labels = [
        expander.label for expander in app.expander if "MISMATCH" in expander.label
    ]
    assert exception_labels == [
        "Capital call  ·  MISMATCH / HIGH",
        "Due date  ·  MISMATCH / HIGH",
    ]

    app.selectbox[0].select("NEEDS_INVESTIGATION")
    app.text_input[0].input("Confirm with administrator")
    app.button[2].click().run()

    assert list(app.exception) == []
    audit_rows = app.dataframe[0].value.to_dict("records")
    assert audit_rows[0]["Field"] == "Capital call"
    assert audit_rows[0]["Decision"] == "Needs Investigation"
    assert audit_rows[0]["Note"] == "Confirm with administrator"


def test_one_click_matching_case_has_no_exception_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "audit.db"))
    app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()

    app.button[1].click().run()

    assert list(app.exception) == []
    assert "Exception Queue (0)" in [tab.label for tab in app.tabs]
    assert any("All controls passed" in message.value for message in app.success)
