from __future__ import annotations

import html
import os
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from app.extraction import extract_document
from app.models import AuditEvent, ReconciliationStatus, ReviewDecision
from app.reconciliation import reconcile_document
from app.sample_data import load_demo_case, load_fund_record
from app.storage import AuditStore


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("FUNDOPS_DB_PATH", str(ROOT / "data" / "fundops.db")))
AMOUNT_FIELDS = {"commitment_amount", "capital_call_amount", "management_fee"}
FIELD_LABELS = {
    "document_type": "Document type",
    "fund_name": "Fund name",
    "investor_name": "Investor name",
    "commitment_amount": "Commitment",
    "capital_call_amount": "Capital call",
    "call_date": "Call date",
    "due_date": "Due date",
    "currency": "Currency",
    "bank_account_reference": "Bank / account reference",
    "management_fee": "Management fee",
}


st.set_page_config(
    page_title="FundOps Copilot",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _css() -> None:
    st.markdown(
        """
        <style>
        :root {
          --ink: #14211d;
          --muted: #62706b;
          --line: #dce3df;
          --surface: #ffffff;
          --wash: #f5f7f6;
          --forest: #163c32;
          --green: #1d6b58;
          --green-soft: #e7f2ed;
          --amber: #a46213;
          --amber-soft: #fff3df;
          --red: #a43a31;
          --red-soft: #fceae8;
        }
        .stApp { background: var(--wash); color: var(--ink); }
        .block-container { max-width: 1440px; padding: 2rem 2.5rem 4rem; }
        [data-testid="stSidebar"] { background: #102a23; border-right: 0; }
        [data-testid="stSidebar"] * { color: #f3f7f5; }
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
          background: rgba(255,255,255,.07); border: 1px dashed rgba(255,255,255,.28);
        }
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] small,
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] span {
          color: #cbd8d3 !important;
        }
        [data-testid="stSidebar"] .stButton button {
          background: #f0f5f3; color: #173c32; border: 0;
        }
        [data-testid="stSidebar"] .stButton button:hover { color: #173c32; border: 0; }
        header[data-testid="stHeader"] { background: transparent; }
        #MainMenu, footer { visibility: hidden; }
        h1, h2, h3 { letter-spacing: -.025em; color: var(--ink); }
        .brandbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem; }
        .brand { font-size:.8rem; letter-spacing:.14em; font-weight:800; color:var(--forest); }
        .environment { font-size:.72rem; color:var(--muted); letter-spacing:.08em; }
        .hero {
          background:#163c32; color:white; padding:2.15rem 2.35rem; border-radius:6px;
          box-shadow:0 10px 30px rgba(20,43,35,.09); margin-bottom:1.45rem;
        }
        .hero-kicker { font-size:.72rem; letter-spacing:.16em; font-weight:700; color:#a8d5c7; margin-bottom:.75rem; }
        .hero h1 { color:white; font-size:2.55rem; line-height:1.06; margin:0 0 .65rem; }
        .hero p { max-width:780px; color:#d7e5e0; font-size:1.03rem; line-height:1.6; margin:0; }
        .section-heading { display:flex; align-items:flex-end; justify-content:space-between; margin:2rem 0 .75rem; }
        .section-heading h2 { font-size:1.22rem; margin:0; }
        .section-heading span { font-size:.75rem; color:var(--muted); letter-spacing:.04em; }
        .panel {
          background:var(--surface); border:1px solid var(--line); border-radius:6px;
          padding:1.2rem 1.25rem; min-height:100%;
        }
        .panel-label { font-size:.67rem; font-weight:800; letter-spacing:.12em; color:var(--muted); margin-bottom:.8rem; }
        .panel-title { font-size:1.06rem; font-weight:750; margin-bottom:.2rem; }
        .panel-subtitle { color:var(--muted); font-size:.82rem; margin-bottom:.9rem; }
        .kv { display:grid; grid-template-columns:42% 58%; padding:.48rem 0; border-top:1px solid #edf1ef; font-size:.84rem; }
        .kv:first-of-type { border-top:0; }
        .kv .key { color:var(--muted); }
        .kv .value { color:var(--ink); font-weight:650; text-align:right; overflow-wrap:anywhere; }
        .metric-card { background:white; border:1px solid var(--line); border-radius:6px; padding:.95rem 1.05rem; }
        .metric-label { color:var(--muted); text-transform:uppercase; font-size:.65rem; font-weight:750; letter-spacing:.1em; }
        .metric-value { color:var(--ink); font-size:1.65rem; line-height:1.2; font-weight:760; margin-top:.25rem; }
        .metric-note { color:var(--muted); font-size:.72rem; margin-top:.18rem; }
        .metric-value.red { color:var(--red); } .metric-value.green { color:var(--green); }
        .case-strip { display:flex; justify-content:space-between; gap:1rem; align-items:center; background:white; border:1px solid var(--line); border-left:4px solid var(--red); border-radius:5px; padding:.78rem 1rem; margin:.85rem 0 1rem; }
        .case-strip.pass { border-left-color:var(--green); }
        .case-strip .case-name { font-size:.82rem; font-weight:750; color:var(--ink); }
        .case-strip .case-meta { font-size:.74rem; color:var(--muted); margin-top:.12rem; }
        .status { display:inline-block; padding:.18rem .48rem; border-radius:3px; font-size:.68rem; font-weight:800; letter-spacing:.06em; }
        .status-pass { color:#17604d; background:var(--green-soft); }
        .status-mismatch { color:#942e27; background:var(--red-soft); }
        .status-missing, .status-review { color:#8b540d; background:var(--amber-soft); }
        .status-pending { color:#58645f; background:#edf0ee; }
        .severity { font-size:.7rem; font-weight:750; color:var(--muted); }
        .recon-wrap { border:1px solid var(--line); background:white; border-radius:6px; overflow:hidden; }
        .recon-table { width:100%; border-collapse:collapse; font-size:.79rem; }
        .recon-table th { padding:.68rem .75rem; background:#f1f4f2; color:#61706a; text-align:left; text-transform:uppercase; font-size:.62rem; letter-spacing:.08em; }
        .recon-table td { padding:.76rem .75rem; border-top:1px solid #e9eeeb; vertical-align:middle; }
        .recon-table tr:hover td { background:#fafcfb; }
        .field-name { font-weight:700; color:var(--ink); }
        .explanation { color:var(--muted); max-width:390px; }
        .evidence { background:#f4f7f5; border-left:3px solid #7da99b; padding:.72rem .85rem; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.76rem; color:#3d4c46; }
        .empty-state { border:1px dashed #cbd5d0; background:white; border-radius:6px; padding:2.5rem; text-align:center; color:var(--muted); }
        .empty-state strong { display:block; color:var(--ink); font-size:1.05rem; margin-bottom:.35rem; }
        .rail-brand { padding:.25rem 0 1.4rem; }
        .rail-brand .mark { color:#83c2ae; font-size:.7rem; letter-spacing:.16em; font-weight:800; }
        .rail-brand h2 { color:white; margin:.35rem 0 .15rem; font-size:1.3rem; }
        .rail-brand p { color:#a9beb7; font-size:.78rem; line-height:1.45; }
        .rail-label { color:#91aaa1; font-size:.63rem; letter-spacing:.13em; font-weight:800; margin:1.2rem 0 .4rem; }
        .provider { display:flex; gap:.45rem; align-items:center; color:#cfe0da; font-size:.76rem; margin-top:.8rem; }
        .provider-dot { width:7px; height:7px; border-radius:50%; background:#55b58f; }
        div[data-testid="stButton"] button { border-radius:4px; font-weight:700; }
        div[data-testid="stButton"] button[kind="primary"] { background:var(--green); border-color:var(--green); }
        div[data-testid="stTabs"] button { font-weight:650; }
        div[data-testid="stExpander"] { border-color:var(--line); background:white; }
        [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:5px; }
        @media (max-width: 760px) {
          .block-container { padding:1rem; }
          .hero { padding:1.5rem; }
          .hero h1 { font-size:2rem; }
          .recon-wrap { overflow-x:auto; }
          .recon-table { min-width:900px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _clean(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(_value(value))


def _currency(record: Any) -> str:
    return _clean(getattr(record, "currency", "GBP")) if record else "GBP"


def _format_value(field: str, value: Any, currency: str = "GBP") -> str:
    if value is None or value == "":
        return "—"
    value = _value(value)
    if field in AMOUNT_FIELDS:
        try:
            amount = Decimal(str(value).replace(",", ""))
            decimals = 0 if amount == amount.to_integral() else 2
            return f"{currency} {amount:,.{decimals}f}"
        except (InvalidOperation, ValueError):
            pass
    if field in {"call_date", "due_date"}:
        try:
            parsed = date.fromisoformat(str(value))
            return parsed.strftime("%d %b %Y")
        except ValueError:
            pass
    if field == "document_type":
        return str(value).replace("_", " ").title()
    return str(value)


def _escape(value: Any) -> str:
    return html.escape(_clean(value), quote=True)


def _field_value(document: Any, field: str) -> Any:
    extracted = getattr(document, "fields", {}).get(field)
    return getattr(extracted, "value", None) if extracted else None


def _expected(record: Any, field: str) -> Any:
    return getattr(record, field, None)


def _item_status(item: Any) -> str:
    return str(_value(getattr(item, "status", "REVIEW"))).upper()


def _item_severity(item: Any) -> str:
    return str(_value(getattr(item, "severity", "MEDIUM"))).upper()


def _report_items(report: Any) -> list[Any]:
    return list(getattr(report, "results", getattr(report, "items", [])))


def _status_badge(status: str) -> str:
    css = status.lower() if status in {"PASS", "MISMATCH", "MISSING", "REVIEW"} else "pending"
    return f'<span class="status status-{css}">{_escape(status)}</span>'


@st.cache_resource
def _audit_store() -> AuditStore:
    return AuditStore(DB_PATH)


def _set_case(case: str) -> None:
    record, document, report = load_demo_case(case)
    st.session_state.record = record
    st.session_state.document = document
    st.session_state.report = report
    st.session_state.case_name = "Exception case" if case == "discrepancy" else "Matching case"
    st.session_state.flash = f"{st.session_state.case_name} loaded"


def _process_upload(uploaded: Any) -> None:
    safe_name = Path(uploaded.name).name
    record = st.session_state.record
    with tempfile.TemporaryDirectory(prefix="fundops-") as temp_dir:
        path = Path(temp_dir) / safe_name
        path.write_bytes(uploaded.getvalue())
        document = extract_document(path, case_id=record.case_id)
    st.session_state.document = document
    st.session_state.report = reconcile_document(record, document)
    st.session_state.case_name = safe_name
    st.session_state.flash = f"{safe_name} extracted and reconciled"


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            """
            <div class="rail-brand">
              <div class="mark">FUNDOPS / 01</div>
              <h2>Control desk</h2>
              <p>Reconcile capital-call notices against the fund book with a complete evidence trail.</p>
            </div>
            <div class="rail-label">LOAD DEMO CASE</div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Load Demo Case", type="primary", use_container_width=True):
            _set_case("discrepancy")
            st.rerun()
        if st.button("Load Clean Match", use_container_width=True):
            _set_case("matching")
            st.rerun()

        st.markdown('<div class="rail-label">UPLOAD DOCUMENT</div>', unsafe_allow_html=True)
        uploaded = st.file_uploader(
            "Capital-call notice",
            type=["pdf", "txt"],
            label_visibility="collapsed",
            help="Text-based PDF or plain text, up to Streamlit's configured upload limit.",
        )
        if st.button("Extract & reconcile", use_container_width=True, disabled=uploaded is None):
            try:
                with st.spinner("Extracting fields and applying controls…"):
                    _process_upload(uploaded)
                st.rerun()
            except Exception as exc:  # surface a useful workflow error without losing the record
                st.error(f"Could not process this document: {exc}")

        st.markdown('<div class="rail-label">EXTRACTION MODE</div>', unsafe_allow_html=True)
        st.caption("Deterministic parser · offline ready")
        st.markdown(
            '<div class="provider"><span class="provider-dot"></span>Rules engine available</div>',
            unsafe_allow_html=True,
        )
        if os.getenv("OPENAI_API_KEY"):
            st.caption("OpenAI-compatible credentials detected. Provider abstraction is available in `app/extraction.py`.")


def _render_header() -> None:
    st.markdown(
        """
        <div class="brandbar">
          <div class="brand">FUNDOPS COPILOT</div>
          <div class="environment">PRIVATE MARKETS · OPERATIONS CONTROL</div>
        </div>
        <div class="hero">
          <div class="hero-kicker">CAPITAL CALL RECONCILIATION</div>
          <h1>Turn messy fund documents<br>into auditable exceptions.</h1>
          <p>Extract operational terms, reconcile them against the fund book, and route only genuine breaks to a human reviewer—with evidence attached.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_quick_actions() -> None:
    left, mid, right, spacer = st.columns([1.35, 1.35, 1.7, 4.2])
    with left:
        if st.button("Load Demo Case", type="primary", use_container_width=True, key="hero_exception"):
            _set_case("discrepancy")
            st.rerun()
    with mid:
        if st.button("Load Clean Match", use_container_width=True, key="hero_match"):
            _set_case("matching")
            st.rerun()
    with right:
        current = st.session_state.get("case_name", "No document loaded")
        st.caption(f"Current case: **{current}**")


def _render_metric(label: str, value: Any, note: str, tone: str = "") -> None:
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-label">{_escape(label)}</div>
          <div class="metric-value {tone}">{_escape(value)}</div>
          <div class="metric-note">{_escape(note)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_case_status(report: Any, document: Any) -> None:
    overall = str(_value(getattr(report, "overall_status", "REVIEW"))).upper()
    is_pass = overall == "PASS"
    label = "ALL CONTROLS PASSED" if is_pass else "REVIEW REQUIRED"
    label_badge = (
        f'<span class="status status-{"pass" if is_pass else "mismatch"}">'
        f"{_escape(label)}</span>"
    )
    source = Path(str(getattr(document, "source_document", "Document"))).name
    case_id = str(getattr(report, "case_id", "Case"))
    st.markdown(
        f"""
        <div class="case-strip {'pass' if is_pass else ''}">
          <div><div class="case-name">{_escape(case_id)}</div><div class="case-meta">{_escape(source)} · Deterministic reconciliation complete</div></div>
          {label_badge}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_summary(report: Any, store: AuditStore) -> None:
    items = _report_items(report)
    passes = sum(_item_status(item) == "PASS" for item in items)
    exceptions = len(items) - passes
    high = sum(_item_severity(item) == "HIGH" and _item_status(item) != "PASS" for item in items)
    case_id = str(getattr(report, "case_id", ""))
    reviewed = sum(
        store.latest_decision(case_id, getattr(item, "field", "")) is not None
        for item in items
        if _item_status(item) != "PASS"
    )

    cols = st.columns(4)
    with cols[0]:
        _render_metric("Controls run", len(items), "Deterministic field checks")
    with cols[1]:
        _render_metric("Passed", passes, "No variance detected", "green")
    with cols[2]:
        _render_metric("Exceptions", exceptions, f"{high} high severity", "red" if exceptions else "green")
    with cols[3]:
        _render_metric("Reviewed", f"{reviewed}/{exceptions}", "Exceptions with a decision")


def _render_context(record: Any, document: Any) -> None:
    st.markdown(
        '<div class="section-heading"><h2>Case context</h2><span>RECORD + SOURCE DOCUMENT</span></div>',
        unsafe_allow_html=True,
    )
    currency = _currency(record)
    left, right = st.columns(2)
    with left:
        rows = [
            ("Fund", getattr(record, "fund_name", None)),
            ("Investor", getattr(record, "investor_name", None)),
            ("Commitment", _format_value("commitment_amount", getattr(record, "commitment_amount", None), currency)),
            ("Expected call", _format_value("capital_call_amount", getattr(record, "capital_call_amount", None), currency)),
            ("Expected due", _format_value("due_date", getattr(record, "due_date", None), currency)),
        ]
        body = "".join(
            f'<div class="kv"><span class="key">{_escape(key)}</span><span class="value">{_escape(value)}</span></div>'
            for key, value in rows
        )
        st.markdown(
            f'<div class="panel"><div class="panel-label">FUND RECORD</div><div class="panel-title">{_escape(getattr(record, "fund_name", "Fund book"))}</div><div class="panel-subtitle">Authoritative operations record</div>{body}</div>',
            unsafe_allow_html=True,
        )
    with right:
        fields = getattr(document, "fields", {})
        confidence_values = [float(field.confidence) for field in fields.values() if getattr(field, "confidence", None) is not None]
        mean_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0
        pages = [
            int(field.page)
            for field in fields.values()
            if getattr(field, "page", None) is not None
        ]
        source = getattr(document, "source_document", getattr(document, "filename", "Uploaded document"))
        rows = [
            ("Document type", _format_value("document_type", _field_value(document, "document_type"))),
            ("Fields extracted", len(fields)),
            ("Pages", max(pages) if pages else "—"),
            ("Mean confidence", f"{mean_confidence:.0%}" if confidence_values else "—"),
            ("Parser", getattr(document, "extraction_method", "Deterministic")),
        ]
        body = "".join(
            f'<div class="kv"><span class="key">{_escape(key)}</span><span class="value">{_escape(value)}</span></div>'
            for key, value in rows
        )
        st.markdown(
            f'<div class="panel"><div class="panel-label">SOURCE DOCUMENT</div><div class="panel-title">{_escape(source)}</div><div class="panel-subtitle">Structured extraction with field-level provenance</div>{body}</div>',
            unsafe_allow_html=True,
        )


def _render_reconciliation_table(report: Any, record: Any) -> None:
    currency = _currency(record)
    rows = []
    items = sorted(
        _report_items(report),
        key=lambda item: (_item_status(item) == "PASS",),
    )
    for item in items:
        field = getattr(item, "field", "")
        status = _item_status(item)
        expected = _format_value(field, getattr(item, "expected", None), currency)
        observed = _format_value(field, getattr(item, "observed", None), currency)
        explanation = getattr(item, "explanation", "")
        rows.append(
            "<tr>"
            f'<td class="field-name">{_escape(FIELD_LABELS.get(field, field.replace("_", " ").title()))}</td>'
            f"<td>{_escape(expected)}</td>"
            f"<td>{_escape(observed)}</td>"
            f"<td>{_status_badge(status)}</td>"
            f'<td><span class="severity">{_escape(_item_severity(item))}</span></td>'
            f'<td class="explanation">{_escape(explanation)}</td>'
            "</tr>"
        )
    table = (
        '<div class="recon-wrap"><table class="recon-table"><thead><tr>'
        "<th>Control</th><th>Fund record</th><th>Document</th><th>Status</th><th>Severity</th><th>Reason</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    st.markdown(table, unsafe_allow_html=True)


def _render_ledger(document: Any) -> None:
    fields = getattr(document, "fields", {})
    if not fields:
        st.info("No fields were extracted from this document.")
        return
    for name, extracted in fields.items():
        confidence = getattr(extracted, "confidence", None)
        page = getattr(extracted, "page", None)
        source = getattr(extracted, "source", getattr(document, "source_document", "Unavailable"))
        evidence = getattr(extracted, "evidence", None) or "No text snippet was available."
        method = getattr(extracted, "extraction_method", getattr(extracted, "method", "deterministic"))
        title = f"{FIELD_LABELS.get(name, name.replace('_', ' ').title())} · {_format_value(name, getattr(extracted, 'value', None), _currency(st.session_state.record))}"
        with st.expander(title):
            meta = st.columns(4)
            meta[0].metric("Source", Path(str(source)).name)
            meta[1].metric("Page", page if page is not None else "Unavailable")
            meta[2].metric("Confidence", f"{float(confidence):.0%}" if confidence is not None else "Unavailable")
            meta[3].metric("Method", str(_value(method)).replace("_", " ").title())
            st.markdown(f'<div class="evidence">{_escape(evidence)}</div>', unsafe_allow_html=True)


def _new_audit_event(item: Any, decision: str, note: str) -> AuditEvent:
    report = st.session_state.report
    document = st.session_state.document
    case_id = str(getattr(report, "case_id", getattr(document, "document_id", getattr(document, "id", "case"))))
    document_id = str(getattr(document, "document_id", getattr(document, "id", case_id)))
    kwargs = {
        "case_id": case_id,
        "document_id": document_id,
        "field": getattr(item, "field", "unknown"),
        "decision": ReviewDecision(decision),
        "note": note.strip() or None,
        "actor": "Demo reviewer",
        "created_at": datetime.now(timezone.utc),
    }
    model_fields = getattr(AuditEvent, "model_fields", {})
    return AuditEvent(**{key: value for key, value in kwargs.items() if not model_fields or key in model_fields})


def _render_exception_queue(report: Any, store: AuditStore) -> None:
    exceptions = [item for item in _report_items(report) if _item_status(item) != "PASS"]
    if not exceptions:
        st.success("All controls passed. No exceptions require human review.")
        return
    for item in exceptions:
        field = getattr(item, "field", "unknown")
        status = _item_status(item)
        provenance = getattr(item, "provenance", None)
        source = getattr(provenance, "source", getattr(st.session_state.document, "source_document", "Unavailable"))
        page = getattr(provenance, "page", None)
        confidence = getattr(provenance, "confidence", None)
        evidence = getattr(provenance, "evidence", None) or "No text snippet was available."

        with st.expander(
            f"{FIELD_LABELS.get(field, field.replace('_', ' ').title())}  ·  {status} / {_item_severity(item)}",
            expanded=True,
        ):
            st.write(getattr(item, "explanation", "Review required."))
            comparison = st.columns(3)
            comparison[0].metric(
                "Fund record",
                _format_value(
                    field,
                    getattr(item, "expected", None),
                    _currency(st.session_state.record),
                ),
            )
            comparison[1].metric(
                "Document",
                _format_value(
                    field,
                    getattr(item, "observed", None),
                    _currency(st.session_state.record),
                ),
            )
            latest = store.latest_decision(str(getattr(report, "case_id", "")), field)
            comparison[2].metric(
                "Latest decision",
                latest.decision.value.replace("_", " ").title() if latest else "Pending",
            )
            left, middle, right = st.columns([1, 1, 2])
            left.caption("SOURCE")
            left.write(Path(str(source)).name)
            middle.caption("PAGE / CONFIDENCE")
            middle.write(f"{page or 'Unavailable'} / {float(confidence):.0%}" if confidence is not None else f"{page or 'Unavailable'} / Unavailable")
            right.caption("EVIDENCE")
            right.markdown(f'<div class="evidence">{_escape(evidence)}</div>', unsafe_allow_html=True)

            form_key = f"review-{getattr(st.session_state.document, 'document_id', 'case')}-{field}"
            with st.form(form_key, clear_on_submit=True):
                form_cols = st.columns([1.45, 3.2, 1])
                decision = form_cols[0].selectbox(
                    "Decision",
                    [decision.value for decision in ReviewDecision],
                    format_func=lambda value: value.replace("_", " ").title(),
                    key=f"decision-{form_key}",
                )
                note = form_cols[1].text_input("Reviewer note", placeholder="Optional rationale or next step")
                submitted = form_cols[2].form_submit_button("Record decision", type="primary", use_container_width=True)
                if submitted:
                    store.append(_new_audit_event(item, decision, note))
                    st.session_state.decision_flash = (
                        f"Decision recorded: {decision.replace('_', ' ').title()} · "
                        f"{FIELD_LABELS.get(field, field)}"
                    )
                    st.rerun()


def _event_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        data = event.model_dump(mode="json")
    elif isinstance(event, dict):
        data = dict(event)
    else:
        data = vars(event)
    return {key: _value(value) for key, value in data.items()}


def _render_audit_log(store: AuditStore, case_id: str) -> None:
    events = store.list_events(limit=200, case_id=case_id)
    if not events:
        st.markdown(
            '<div class="empty-state"><strong>No review decisions yet</strong>Decisions are appended here with reviewer, timestamp, and rationale.</div>',
            unsafe_allow_html=True,
        )
        return
    rows = []
    for event in events:
        data = _event_dict(event)
        timestamp = data.get("timestamp") or data.get("created_at")
        rows.append(
            {
                "Time (UTC)": str(timestamp).replace("T", " ")[:19],
                "Case": data.get("case_id") or "—",
                "Field": FIELD_LABELS.get(str(data.get("field")), str(data.get("field", ""))),
                "Decision": str(data.get("decision", "")).replace("_", " ").title(),
                "Reviewer": data.get("actor") or data.get("reviewer") or "Demo reviewer",
                "Note": data.get("note") or "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _init_state() -> None:
    if "record" not in st.session_state:
        st.session_state.record = load_fund_record()
    if "document" not in st.session_state:
        st.session_state.document = None
    if "report" not in st.session_state:
        st.session_state.report = None
    if "case_name" not in st.session_state:
        st.session_state.case_name = "No document loaded"


def main() -> None:
    _css()
    _init_state()
    _render_sidebar()
    _render_header()
    _render_quick_actions()

    if st.session_state.pop("flash", None):
        st.toast("Document ready. Reconciliation controls have completed.", icon="✅")
    decision_flash = st.session_state.pop("decision_flash", None)
    if decision_flash:
        st.toast(decision_flash, icon="✅")

    record = st.session_state.record
    document = st.session_state.document
    report = st.session_state.report

    if document is None or report is None:
        st.markdown(
            '<div class="section-heading"><h2>Upload Document</h2><span>START A RECONCILIATION CASE</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="empty-state"><strong>No source document selected</strong>Load a synthetic case above for an instant offline demo, or upload a text-based capital-call PDF from the control desk.</div>',
            unsafe_allow_html=True,
        )
        # The authoritative record remains visible before a document is selected.
        placeholder_fields = type("EmptyDocument", (), {"fields": {}, "source_document": "Awaiting document"})()
        _render_context(record, placeholder_fields)
        return

    store = _audit_store()
    _render_case_status(report, document)
    _render_summary(report, store)
    _render_context(record, document)

    if getattr(document, "warnings", None):
        with st.expander("Extraction notes", expanded=True):
            for warning in document.warnings:
                st.warning(str(warning))

    st.markdown(
        '<div class="section-heading"><h2>Operations workflow</h2><span>RECONCILE · VERIFY · DECIDE</span></div>',
        unsafe_allow_html=True,
    )
    items = _report_items(report)
    exception_count = sum(_item_status(item) != "PASS" for item in items)
    tabs = st.tabs(
        [
            "Reconciliation Results",
            "Extraction Ledger",
            f"Exception Queue ({exception_count})",
            "Audit Log",
        ]
    )
    with tabs[0]:
        st.caption("Fund-book values are compared with normalized document values using deterministic controls.")
        _render_reconciliation_table(report, record)
    with tabs[1]:
        st.caption("Every observed value retains its source, page, confidence, extraction method, and evidence snippet.")
        _render_ledger(document)
    with tabs[2]:
        st.caption("Human decisions are explicit and append-only; the extracted source is never overwritten.")
        _render_exception_queue(report, store)
    with tabs[3]:
        st.caption("Chronological reviewer actions persisted in local SQLite storage.")
        _render_audit_log(store, str(getattr(report, "case_id", "")))


if __name__ == "__main__":
    main()
