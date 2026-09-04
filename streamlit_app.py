from __future__ import annotations

import html
import hashlib
import inspect
import os
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import pandas as pd
import streamlit as st

from app.errors import WorkflowError
from app.extraction import OpenAICompatibleExtractor, extract_document
from app.file_handling import temporary_upload
from app.models import AuditEvent, ReviewDecision
from app.reconciliation import reconcile_document
from app.review import review_reconciliation
from app.sample_data import DEMO_FILES, load_demo_case, load_fund_record
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
STATUS_LABELS = {
    "PASS": "PASS",
    "MISMATCH": "MISMATCH",
    "MISSING": "MISSING",
    "REVIEW": "REVIEW",
}
ACTION_TO_DECISION = {
    "Approved": ReviewDecision.APPROVED,
    "Rejected": ReviewDecision.REJECTED,
    "Needs investigation": ReviewDecision.NEEDS_INVESTIGATION,
}
DECISION_LABELS = {
    ReviewDecision.APPROVED.value: "Approved",
    ReviewDecision.REJECTED.value: "Rejected",
    ReviewDecision.NEEDS_INVESTIGATION.value: "Needs investigation",
}


st.set_page_config(
    page_title="FundOps Copilot",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def _css() -> None:
    st.markdown(
        """
        <style>
        :root {
          --ink: #17211d;
          --muted: #63716c;
          --line: #d8e0dc;
          --line-strong: #c4cfca;
          --surface: #ffffff;
          --wash: #f4f6f5;
          --forest: #153b32;
          --forest-2: #215a4c;
          --green: #17634f;
          --green-soft: #e8f3ee;
          --amber: #8d570f;
          --amber-soft: #fff2dc;
          --red: #a2352e;
          --red-soft: #fbe8e6;
          --violet: #65508e;
          --violet-soft: #f1ecfa;
          --slate-soft: #edf1ef;
        }
        .stApp { background: var(--wash); color: var(--ink); }
        .block-container { max-width: 1680px; padding: 1.4rem 2rem 3rem; }
        header[data-testid="stHeader"] { background: transparent; }
        #MainMenu, footer { visibility: hidden; }
        html, body, [class*="st-"] { font-size: 16px; }
        h1, h2, h3, h4 { color: var(--ink); letter-spacing: -.02em; }

        [data-testid="stSidebar"] { background: #112d26; border-right: 0; }
        [data-testid="stSidebar"] * { color: #f4f7f5; }
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
          background: rgba(255,255,255,.06); border: 1px dashed rgba(255,255,255,.28);
        }
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] small,
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] span {
          color: #cbd8d3 !important;
        }
        [data-testid="stSidebar"] .stButton button {
          background: #f0f5f3; color: #173c32; border: 0;
        }

        .masthead {
          display:flex; justify-content:space-between; align-items:center; gap:2rem;
          padding:1.2rem 1.35rem; margin-bottom:1rem; background:var(--forest);
          color:white; border-radius:5px; border:1px solid #0e3028;
        }
        .masthead h1 { color:white; font-size:1.72rem; line-height:1.15; margin:0 0 .28rem; }
        .masthead p { color:#d7e4df; font-size:.96rem; line-height:1.45; margin:0; }
        .masthead-meta { color:#a8c8bd; text-transform:uppercase; font-size:.72rem; font-weight:750; letter-spacing:.11em; white-space:nowrap; }
        .toolbar-meta { color:var(--muted); font-size:.8rem; line-height:1.35; padding-top:.42rem; }
        .toolbar-meta strong { color:var(--ink); }

        .section-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:1rem; margin:1.35rem 0 .68rem; }
        .section-heading h2 { font-size:1.14rem; margin:0; }
        .section-heading span { font-size:.72rem; color:var(--muted); letter-spacing:.07em; text-transform:uppercase; }
        .case-strip {
          display:flex; justify-content:space-between; gap:1rem; align-items:center;
          background:white; border:1px solid var(--line); border-left:4px solid var(--red);
          border-radius:5px; padding:.72rem .95rem; margin:.9rem 0;
        }
        .case-strip.pass { border-left-color:var(--green); }
        .case-strip .case-name { font-size:.88rem; font-weight:760; color:var(--ink); }
        .case-strip .case-meta { font-size:.77rem; color:var(--muted); margin-top:.1rem; }

        .metric-card { background:white; border:1px solid var(--line); border-radius:5px; padding:.82rem .9rem; min-height:103px; }
        .metric-label { color:var(--muted); text-transform:uppercase; font-size:.7rem; font-weight:760; letter-spacing:.075em; line-height:1.3; }
        .metric-value { color:var(--ink); font-size:1.55rem; line-height:1.15; font-weight:780; margin-top:.34rem; }
        .metric-note { color:var(--muted); font-size:.72rem; margin-top:.2rem; }
        .metric-value.red { color:var(--red); }
        .metric-value.green { color:var(--green); }
        .metric-value.amber { color:var(--amber); }

        .status { display:inline-block; padding:.2rem .5rem; border-radius:3px; font-size:.7rem; font-weight:800; letter-spacing:.055em; }
        .status-match, .status-pass { color:var(--green); background:var(--green-soft); }
        .status-mismatch { color:var(--red); background:var(--red-soft); }
        .status-missing { color:var(--amber); background:var(--amber-soft); }
        .status-ambiguous, .status-review { color:var(--violet); background:var(--violet-soft); }
        .status-pending { color:#53615c; background:var(--slate-soft); }

        .drawer-title { color:var(--ink); font-size:1.04rem; font-weight:760; line-height:1.3; margin-bottom:.75rem; }
        .drawer-kicker { color:var(--muted); text-transform:uppercase; font-size:.69rem; font-weight:800; letter-spacing:.09em; margin-bottom:.2rem; }
        .drawer-row { padding:.55rem 0; border-top:1px solid #e8eeeb; }
        .drawer-label { color:var(--muted); text-transform:uppercase; font-size:.65rem; font-weight:760; letter-spacing:.075em; margin-bottom:.14rem; }
        .drawer-value { color:var(--ink); font-size:.83rem; font-weight:620; overflow-wrap:anywhere; }
        .evidence {
          background:#f1f5f3; border-left:3px solid #739e91; padding:.72rem .8rem;
          font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.78rem;
          line-height:1.52; color:#35443f; overflow-wrap:anywhere;
        }
        .annotation { background:#f8faf9; border:1px solid var(--line); border-radius:4px; padding:.78rem .85rem; color:#46534f; font-size:.84rem; line-height:1.48; min-height:78px; }

        .comparison-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.65rem; margin:.55rem 0 .85rem; }
        .comparison-cell { background:white; border:1px solid var(--line); border-radius:5px; padding:.82rem .9rem; }
        .comparison-label { color:var(--muted); text-transform:uppercase; font-size:.67rem; font-weight:760; letter-spacing:.07em; }
        .comparison-value { color:var(--ink); font-size:1rem; font-weight:740; margin-top:.24rem; overflow-wrap:anywhere; }

        .panel { background:var(--surface); border:1px solid var(--line); border-radius:5px; padding:1.05rem 1.1rem; min-height:100%; }
        .panel-label { font-size:.68rem; font-weight:800; letter-spacing:.1em; color:var(--muted); margin-bottom:.68rem; }
        .panel-title { font-size:1.03rem; font-weight:760; margin-bottom:.16rem; }
        .panel-subtitle { color:var(--muted); font-size:.8rem; margin-bottom:.72rem; }
        .kv { display:grid; grid-template-columns:42% 58%; gap:.6rem; padding:.46rem 0; border-top:1px solid #edf1ef; font-size:.82rem; }
        .kv:first-of-type { border-top:0; }
        .kv .key { color:var(--muted); }
        .kv .value { color:var(--ink); font-weight:650; text-align:right; overflow-wrap:anywhere; }
        .empty-state { border:1px dashed #c4d0cb; background:white; border-radius:5px; padding:2rem; text-align:center; color:var(--muted); }
        .empty-state strong { display:block; color:var(--ink); font-size:1.03rem; margin-bottom:.28rem; }
        .rail-brand { padding:.2rem 0 1.1rem; }
        .rail-brand .mark { color:#89bcae; font-size:.7rem; letter-spacing:.13em; font-weight:800; }
        .rail-brand h2 { color:white; margin:.3rem 0 .12rem; font-size:1.22rem; }
        .rail-brand p { color:#acc1ba; font-size:.8rem; line-height:1.45; }
        .rail-label { color:#92aaa2; font-size:.65rem; letter-spacing:.1em; font-weight:800; margin:1rem 0 .36rem; }
        .provider { display:flex; gap:.42rem; align-items:center; color:#d1e1dc; font-size:.78rem; margin-top:.65rem; }
        .provider-dot { width:7px; height:7px; border-radius:50%; background:#5abb96; }

        div[data-testid="stButton"] button,
        div[data-testid="stFormSubmitButton"] button { border-radius:4px; min-height:42px; font-weight:720; }
        div[data-testid="stButton"] button[kind="primary"],
        div[data-testid="stFormSubmitButton"] button[kind="primary"] { background:var(--forest-2); border-color:var(--forest-2); }
        div[data-testid="stTabs"] button { min-height:44px; font-size:.86rem; font-weight:680; }
        div[data-testid="stExpander"] { border-color:var(--line); background:white; }
        [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:5px; background:white; }
        [data-testid="stDataFrame"] * { font-size:.84rem; }
        [data-testid="stTextInput"] input { min-height:42px; }

        @media (max-width: 900px) {
          .block-container { padding:1rem; }
          .masthead { align-items:flex-start; flex-direction:column; gap:.55rem; }
          .masthead-meta { white-space:normal; }
          .comparison-grid { grid-template-columns:1fr; }
          .metric-card { min-height:92px; }
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


def _escape(value: Any) -> str:
    return html.escape(_clean(value), quote=True)


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


def _format_difference(item: Any, currency: str) -> str:
    difference = getattr(item, "difference", None)
    if difference is None:
        return "—"
    field = str(getattr(item, "field", ""))
    if field in AMOUNT_FIELDS:
        try:
            amount = Decimal(str(difference))
            sign = "+" if amount > 0 else "−" if amount < 0 else ""
            absolute = abs(amount)
            decimals = 0 if absolute == absolute.to_integral() else 2
            return f"{currency} {sign}{absolute:,.{decimals}f}"
        except InvalidOperation:
            return _clean(difference)
    if field in {"call_date", "due_date"}:
        try:
            days = int(difference)
            sign = "+" if days > 0 else ""
            unit = "day" if abs(days) == 1 else "days"
            return f"{sign}{days} {unit}"
        except (TypeError, ValueError):
            return _clean(difference)
    return _clean(difference)


def _field_value(document: Any, field: str) -> Any:
    extracted = getattr(document, "fields", {}).get(field)
    if extracted is not None:
        return getattr(extracted, "value", None)
    if field == "document_type":
        return _value(getattr(document, "document_type", None))
    return None


def _item_status(item: Any) -> str:
    return str(_value(getattr(item, "status", "REVIEW"))).upper()


def _display_status(item: Any) -> str:
    return STATUS_LABELS.get(_item_status(item), "AMBIGUOUS")


def _item_severity(item: Any) -> str:
    return str(_value(getattr(item, "severity", "MEDIUM"))).upper()


def _report_items(report: Any) -> list[Any]:
    return list(getattr(report, "results", getattr(report, "items", [])))


def _field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " ").title())


def _default_selected_field(report: Any) -> Optional[str]:
    items = _report_items(report)
    candidates = [
        item
        for item in items
        if _item_status(item) != "PASS" and _item_severity(item) == "HIGH"
    ]
    if not candidates:
        candidates = [item for item in items if _item_status(item) != "PASS"]
    if not candidates:
        candidates = items
    return str(getattr(candidates[0], "field", "")) if candidates else None


def _lookup(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _evaluation_view(payload: Any) -> dict[str, Any]:
    """Adapt optional eval-service output without inventing performance values."""

    if payload is None:
        return {"available": False, "metrics": {}, "failure_cases": []}
    metrics_source = _lookup(payload, "metrics", {}) or {}
    metrics = {
        key: _lookup(metrics_source, key)
        for key in (
            "field_accuracy",
            "exception_recall",
            "exception_precision",
            "cases_evaluated",
            "latency_ms",
        )
    }
    failures = _lookup(payload, "failure_cases")
    if failures is None:
        failures = _lookup(payload, "failures", [])
    return {
        "available": True,
        "metrics": metrics,
        "failure_cases": list(failures or []),
    }


def _format_eval_metric(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if key in {"field_accuracy", "exception_recall", "exception_precision"}:
        try:
            number = float(value)
            percentage = number * 100 if 0 <= number <= 1 else number
            return f"{percentage:.1f}%"
        except (TypeError, ValueError):
            return _clean(value)
    if key == "cases_evaluated":
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return _clean(value)
    if key == "latency_ms":
        try:
            return f"{float(value):,.0f} ms"
        except (TypeError, ValueError):
            return _clean(value)
    return _clean(value)


@st.cache_resource
def _audit_store() -> AuditStore:
    return AuditStore(DB_PATH)


def _review_map(
    store: AuditStore,
    case_id: str,
    document_id: str,
    fields: list[str],
) -> dict[str, AuditEvent]:
    latest: dict[str, AuditEvent] = {}
    for field in fields:
        event = store.latest_decision(
            case_id,
            field,
            document_id=document_id,
        )
        if event is not None:
            latest[field] = event
    return latest


def _document_scope_id(content: bytes, record: Any, document: Any) -> str:
    """Bind decisions to source, canonical data, and finalized extraction."""

    if hasattr(record, "model_dump_json"):
        record_payload = record.model_dump_json().encode("utf-8")
    else:
        record_payload = repr(record).encode("utf-8")
    if hasattr(document, "model_dump_json"):
        extraction_payload = document.model_dump_json().encode("utf-8")
    else:
        extraction_payload = repr(document).encode("utf-8")
    digest = hashlib.sha256(
        content + b"\0" + record_payload + b"\0" + extraction_payload
    ).hexdigest()
    return f"sha256:{digest}"


def _scope_token() -> str:
    document_id = str(st.session_state.get("document_id", "unspecified"))
    return hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:12]


def _set_case(case: str) -> None:
    record, document, report = load_demo_case(case)
    review_report = review_reconciliation(report)
    document_id = _document_scope_id(DEMO_FILES[case].read_bytes(), record, document)
    case_name = (
        "Northstar exception demo" if case == "discrepancy" else "Northstar clean match"
    )
    st.session_state.record = record
    st.session_state.document = document
    st.session_state.report = report
    st.session_state.review_report = review_report
    st.session_state.document_id = document_id
    st.session_state.case_name = case_name
    st.session_state.source_display_name = document.source_document
    st.session_state.selected_field = _default_selected_field(report)
    st.session_state.show_upload = False
    st.session_state.pop("upload_error", None)
    st.session_state.flash = f"{st.session_state.case_name} loaded"


def _process_upload(uploaded: Any) -> None:
    record = st.session_state.record
    content_type = getattr(uploaded, "type", None)
    content = uploaded.getvalue()
    source_name = Path(str(uploaded.name).replace("\\", "/")).name
    extractor = (
        OpenAICompatibleExtractor()
        if st.session_state.get("use_ai_extraction", False)
        else None
    )
    with temporary_upload(uploaded.name, content, content_type) as path:
        document = extract_document(
            path,
            extractor=extractor,
            case_id=record.case_id,
        )
    document = document.model_copy(
        update={
            "source_document": source_name,
            "fields": {
                name: field.model_copy(update={"source": source_name})
                for name, field in document.fields.items()
            },
        }
    )
    report = reconcile_document(record, document)
    review_report = review_reconciliation(report)
    document_id = _document_scope_id(content, record, document)
    st.session_state.document = document
    st.session_state.report = report
    st.session_state.review_report = review_report
    st.session_state.document_id = document_id
    st.session_state.case_name = source_name
    st.session_state.source_display_name = st.session_state.case_name
    st.session_state.selected_field = _default_selected_field(report)
    st.session_state.show_upload = False
    st.session_state.pop("upload_error", None)
    st.session_state.flash = f"{st.session_state.case_name} extracted and reconciled"


def _capture_upload_error(error: BaseException) -> None:
    if isinstance(error, WorkflowError):
        message = error.public_message
        request_id = error.request_id
    else:
        message = "The workflow could not be completed. Check the package and try again."
        request_id = str(uuid4())
    st.session_state.upload_error = {"message": message, "request_id": request_id}


def _render_upload_error() -> None:
    error = st.session_state.get("upload_error")
    if not error:
        return
    st.error(f"{error['message']} Reference: {error['request_id']}")


def _render_sidebar() -> None:
    """Keep the original demo utilities available without consuming projector width."""

    with st.sidebar:
        st.markdown(
            """
            <div class="rail-brand">
              <div class="mark">FUNDOPS / CONTROL ROOM</div>
              <h2>Case utilities</h2>
              <p>Load the canonical demo cases or process a single text-based notice.</p>
            </div>
            <div class="rail-label">DEMO CASES</div>
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
            help="Text-based PDF or plain text.",
            key="sidebar_upload",
        )
        if st.button(
            "Extract & reconcile",
            use_container_width=True,
            disabled=uploaded is None,
            key="sidebar_process",
        ):
            try:
                with st.spinner("Extracting fields and applying controls…"):
                    _process_upload(uploaded)
                st.rerun()
            except Exception as exc:
                _capture_upload_error(exc)
        _render_upload_error()

        st.markdown('<div class="rail-label">EXTRACTION MODE</div>', unsafe_allow_html=True)
        ai_available = bool(os.getenv("OPENAI_API_KEY"))
        st.checkbox(
            "Use OpenAI-compatible extraction",
            value=False,
            disabled=not ai_available,
            key="use_ai_extraction",
            help=(
                "The provider structures document text only; all comparisons remain deterministic."
                if ai_available
                else "Set OPENAI_API_KEY to enable this optional mode."
            ),
        )
        st.markdown(
            '<div class="provider"><span class="provider-dot"></span>Rules engine available</div>',
            unsafe_allow_html=True,
        )
        if ai_available:
            st.caption("OpenAI-compatible extractor available")


def _render_header() -> None:
    st.markdown(
        """
        <div class="masthead">
          <div>
            <h1>FundOps Copilot</h1>
            <p>Turn messy fund documents into auditable exceptions.</p>
          </div>
          <div class="masthead-meta">Private markets · Operations control</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_quick_actions() -> None:
    load_col, upload_col, case_col = st.columns([1.45, 1.25, 4.3])
    with load_col:
        if st.button(
            "Load Demo Case",
            type="primary",
            use_container_width=True,
            key="load_northstar",
        ):
            try:
                with st.spinner("Loading Northstar and running controls…"):
                    _set_case("discrepancy")
                st.rerun()
            except Exception:
                st.error("The Northstar demo could not be loaded. Please try again.")
    with upload_col:
        if st.button("Upload Document", use_container_width=True, key="toggle_upload"):
            st.session_state.show_upload = not st.session_state.get("show_upload", False)
    with case_col:
        current = st.session_state.get("case_name", "No package loaded")
        st.markdown(
            f'<div class="toolbar-meta">Active case<br><strong>{_escape(current)}</strong></div>',
            unsafe_allow_html=True,
        )

    if st.session_state.get("show_upload", False):
        with st.container(border=True):
            st.markdown("#### Upload Document")
            st.caption("Process one text-based PDF or TXT notice against the active fund record.")
            upload_file, process_col = st.columns([4, 1.3])
            with upload_file:
                uploaded = st.file_uploader(
                    "Capital-call notice",
                    type=["pdf", "txt"],
                    help="The last good reconciliation stays on screen if processing fails.",
                    key="main_upload",
                )
            with process_col:
                st.write("")
                process = st.button(
                    "Process package",
                    type="primary",
                    use_container_width=True,
                    disabled=uploaded is None,
                    key="main_process",
                )
            if process:
                try:
                    with st.spinner("Extracting fields and applying controls…"):
                        _process_upload(uploaded)
                    st.rerun()
                except Exception as exc:
                    _capture_upload_error(exc)
            _render_upload_error()


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
    status_class = "match" if is_pass else "mismatch"
    source = st.session_state.get("source_display_name") or Path(
        str(getattr(document, "source_document", "Document"))
    ).name
    case_id = str(getattr(report, "case_id", "Case"))
    generated_at = getattr(report, "generated_at", None)
    completed = (
        generated_at.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
        if isinstance(generated_at, datetime)
        else "just now"
    )
    st.markdown(
        f"""
        <div class="case-strip {'pass' if is_pass else ''}">
          <div>
            <div class="case-name">{_escape(case_id)}</div>
            <div class="case-meta">{_escape(source)} · Reconciliation completed {_escape(completed)}</div>
          </div>
          <span class="status status-{status_class}">{_escape(label)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_summary(report: Any, review_by_field: Mapping) -> None:
    items = _report_items(report)
    exceptions = [item for item in items if _item_status(item) != "PASS"]
    high_risk = [item for item in exceptions if _item_severity(item) == "HIGH"]
    awaiting = [
        item for item in exceptions if str(getattr(item, "field", "")) not in review_by_field
    ]
    metrics = [
        ("Documents processed", 1, "Current package", ""),
        ("Fields reconciled", len(items), "Deterministic controls", "green"),
        ("Exceptions", len(exceptions), "Requiring attention", "red" if exceptions else "green"),
        ("High-risk exceptions", len(high_risk), "Priority queue", "red" if high_risk else "green"),
        ("Awaiting review", len(awaiting), "No human decision", "amber" if awaiting else "green"),
    ]
    for column, metric in zip(st.columns(5), metrics):
        with column:
            _render_metric(*metric)


def _table_items(report: Any) -> list[Any]:
    return sorted(
        _report_items(report),
        key=lambda item: (
            _item_status(item) == "PASS",
            _item_severity(item) != "HIGH",
        ),
    )


def _review_status(item: Any, review_by_field: Mapping) -> str:
    if _item_status(item) == "PASS":
        return "Not required"
    field = str(getattr(item, "field", ""))
    event = review_by_field.get(field)
    if event is None:
        return "Awaiting review"
    return DECISION_LABELS.get(event.decision.value, event.decision.value.replace("_", " ").title())


def _display_source_name(raw_source: Any) -> str:
    session_name = st.session_state.get("source_display_name")
    return str(session_name) if session_name else Path(str(raw_source)).name


def _table_frame(report: Any, record: Any, review_by_field: Mapping) -> tuple[pd.DataFrame, list[Any]]:
    items = _table_items(report)
    currency = _currency(record)
    rows = []
    for item in items:
        provenance = getattr(item, "provenance", None)
        source = getattr(provenance, "source", None) or getattr(report, "source_document", "—")
        page = getattr(provenance, "page", None)
        source_label = _display_source_name(source)
        if page is not None:
            source_label = f"{source_label} · p.{page}"
        field = str(getattr(item, "field", ""))
        rows.append(
            {
                "Field": _field_label(field),
                "Expected": _format_value(field, getattr(item, "expected", None), currency),
                "Observed": _format_value(field, getattr(item, "observed", None), currency),
                "Status": _display_status(item),
                "Severity": _item_severity(item),
                "Source": source_label,
                "Review status": _review_status(item, review_by_field),
            }
        )
    return pd.DataFrame(rows), items


def _status_style(value: Any) -> str:
    return {
        "PASS": "background-color:#e8f3ee;color:#17634f;font-weight:750",
        "MISMATCH": "background-color:#fbe8e6;color:#a2352e;font-weight:800",
        "MISSING": "background-color:#fff2dc;color:#8d570f;font-weight:800",
        "REVIEW": "background-color:#f1ecfa;color:#65508e;font-weight:800",
    }.get(str(value), "")


def _severity_style(value: Any) -> str:
    if str(value) == "HIGH":
        return "color:#a2352e;font-weight:800"
    if str(value) == "MEDIUM":
        return "color:#8d570f;font-weight:750"
    return "color:#63716c;font-weight:650"


def _review_style(value: Any) -> str:
    if str(value) == "Awaiting review":
        return "background-color:#fff2dc;color:#8d570f;font-weight:750"
    if str(value) == "Investigating":
        return "background-color:#f1ecfa;color:#65508e;font-weight:750"
    return "color:#46534f;font-weight:650"


def _selected_item(report: Any) -> Optional[Any]:
    items = _report_items(report)
    selected = st.session_state.get("selected_field")
    return next(
        (item for item in items if str(getattr(item, "field", "")) == selected),
        items[0] if items else None,
    )


def _render_reconciliation_table(report: Any, record: Any, review_by_field: Mapping) -> None:
    frame, items = _table_frame(report, record, review_by_field)
    if frame.empty:
        st.info("No reconciliation fields were returned for this package.")
        return
    styled = (
        frame.style.map(_status_style, subset=["Status"])
        .map(_severity_style, subset=["Severity"])
        .map(_review_style, subset=["Review status"])
    )
    dataframe_args = {
        "use_container_width": True,
        "hide_index": True,
        "height": min(465, 38 * (len(frame) + 1) + 6),
        "column_config": {
            "Field": st.column_config.TextColumn("Field", width="medium"),
            "Expected": st.column_config.TextColumn("Expected", width="medium"),
            "Observed": st.column_config.TextColumn("Observed", width="medium"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Severity": st.column_config.TextColumn("Severity", width="small"),
            "Source": st.column_config.TextColumn("Source", width="medium"),
            "Review status": st.column_config.TextColumn("Review status", width="medium"),
        },
    }
    if "on_select" in inspect.signature(st.dataframe).parameters:
        event = st.dataframe(
            styled,
            on_select="rerun",
            selection_mode="single-row",
            key=f"reconciliation_table_{_scope_token()}",
            **dataframe_args,
        )
        selection = getattr(event, "selection", None)
        selected_rows = (
            selection.get("rows", [])
            if isinstance(selection, Mapping)
            else getattr(selection, "rows", [])
        )
        if selected_rows:
            row = int(selected_rows[0])
            if 0 <= row < len(items):
                st.session_state.selected_field = str(getattr(items[row], "field", ""))
    else:
        selected_field = st.selectbox(
            "Inspect field",
            [str(getattr(item, "field", "")) for item in items],
            format_func=_field_label,
            key=f"legacy_field_selector_{_scope_token()}",
        )
        st.session_state.selected_field = selected_field
        st.dataframe(styled, **dataframe_args)


def _optional_attr(value: Any, *names: str) -> Any:
    for name in names:
        candidate = _lookup(value, name)
        if candidate not in (None, ""):
            return candidate
    return None


def _source_location(provenance: Any) -> str:
    sheet = _optional_attr(provenance, "sheet", "sheet_name")
    cell = _optional_attr(provenance, "cell", "cell_reference")
    if sheet or cell:
        parts = [f"Sheet {sheet}" if sheet else None, f"Cell {cell}" if cell else None]
        return " · ".join(part for part in parts if part)
    page = _optional_attr(provenance, "page")
    return f"PDF page {page}" if page is not None else "Location not provided"


def _drawer_row(label: str, value: Any) -> str:
    return (
        '<div class="drawer-row">'
        f'<div class="drawer-label">{_escape(label)}</div>'
        f'<div class="drawer-value">{_escape(value)}</div>'
        "</div>"
    )


def _render_evidence_drawer(item: Any, report: Any) -> None:
    if item is None:
        st.info("Select a field to inspect its source evidence.")
        return
    field = str(getattr(item, "field", ""))
    provenance = getattr(item, "provenance", None)
    raw_source = _optional_attr(provenance, "source") or getattr(report, "source_document", "Not provided")
    method = _optional_attr(provenance, "method", "extraction_method")
    confidence = _optional_attr(provenance, "confidence")
    normalization = _optional_attr(
        provenance,
        "normalization_performed",
        "normalization",
        "normalization_notes",
    )
    evidence = _optional_attr(provenance, "evidence")
    with st.container(border=True):
        st.markdown(
            f'<div class="drawer-kicker">Source evidence</div><div class="drawer-title">{_escape(_field_label(field))}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            _drawer_row("Source document", _display_source_name(raw_source))
            + _drawer_row("Location", _source_location(provenance))
            + _drawer_row(
                "Extractor",
                str(_value(method)).replace("_", " ").title() if method else "Not provided",
            )
            + _drawer_row(
                "Confidence",
                f"{float(confidence):.0%}" if confidence is not None else "Not provided",
            )
            + _drawer_row(
                "Normalization performed",
                normalization or "Not provided by extractor",
            ),
            unsafe_allow_html=True,
        )
        if confidence is not None:
            st.progress(float(confidence))
        st.markdown('<div class="drawer-label">Evidence snippet</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="evidence">{_escape(evidence or "No evidence snippet was returned.")}</div>',
            unsafe_allow_html=True,
        )


def _render_reconciliation_workspace(report: Any, record: Any, review_by_field: Mapping) -> None:
    st.caption("Select any row to inspect the document evidence. Exceptions are prioritized at the top.")
    table_col, evidence_col = st.columns([4.2, 1.45])
    with table_col:
        _render_reconciliation_table(report, record, review_by_field)
    with evidence_col:
        _render_evidence_drawer(_selected_item(report), report)


def _new_audit_event(item: Any, decision: ReviewDecision, note: str) -> AuditEvent:
    report = st.session_state.report
    document = st.session_state.document
    case_id = str(
        getattr(report, "case_id", getattr(document, "document_id", getattr(document, "id", "case")))
    )
    kwargs = {
        "case_id": case_id,
        "document_id": st.session_state.get("document_id", "unspecified"),
        "source_document": str(getattr(document, "source_document", "")) or None,
        "field": getattr(item, "field", "unknown"),
        "decision": decision,
        "note": note.strip(),
        "actor": "demo-user",
        "created_at": datetime.now(timezone.utc),
    }
    model_fields = getattr(AuditEvent, "model_fields", {})
    return AuditEvent(
        **{key: value for key, value in kwargs.items() if not model_fields or key in model_fields}
    )


def _render_exception_detail(
    report: Any,
    record: Any,
    store: AuditStore,
    review_by_field: Mapping,
) -> None:
    exceptions = [item for item in _table_items(report) if _item_status(item) != "PASS"]
    if not exceptions:
        st.success("All controls passed. No exceptions require human review.")
        return

    fields = [str(getattr(item, "field", "")) for item in exceptions]
    by_field = {str(getattr(item, "field", "")): item for item in exceptions}
    selected = st.session_state.get("selected_field")
    default_index = fields.index(selected) if selected in fields else 0
    selected_field = st.selectbox(
        "Exception",
        fields,
        index=default_index,
        format_func=lambda field: f"{_field_label(field)} · {_display_status(by_field[field])}",
        key=(
            f"exception_selector_{getattr(report, 'case_id', 'case')}_"
            f"{_scope_token()}"
        ),
    )
    st.session_state.selected_field = selected_field
    item = by_field[selected_field]
    provenance = getattr(item, "provenance", None)
    evidence = _optional_attr(provenance, "evidence") or "No evidence snippet was returned."
    review_report = st.session_state.get("review_report")
    finding = review_report.finding_for(selected_field) if review_report is not None else None
    if finding is None:
        reviewer_finding = "Independent evidence check was unavailable."
    else:
        reviewer_finding = f"{finding.status.value}: {finding.review_reason}"
    currency = _currency(record)
    latest = review_by_field.get(selected_field)

    status_col, severity_col, review_col = st.columns([1, 1, 2])
    status_col.metric("Status", _display_status(item))
    severity_col.metric("Severity", _item_severity(item))
    review_col.metric("Review status", _review_status(item, review_by_field))
    st.markdown(
        "<div class=\"comparison-grid\">"
        f'<div class="comparison-cell"><div class="comparison-label">Expected</div><div class="comparison-value">{_escape(_format_value(selected_field, getattr(item, "expected", None), currency))}</div></div>'
        f'<div class="comparison-cell"><div class="comparison-label">Observed</div><div class="comparison-value">{_escape(_format_value(selected_field, getattr(item, "observed", None), currency))}</div></div>'
        f'<div class="comparison-cell"><div class="comparison-label">Difference</div><div class="comparison-value">{_escape(_format_difference(item, currency))}</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )

    why_col, reviewer_col = st.columns(2)
    with why_col:
        st.markdown("##### Why flagged")
        st.markdown(
            f'<div class="annotation">{_escape(getattr(item, "explanation", "Review required."))}</div>',
            unsafe_allow_html=True,
        )
    with reviewer_col:
        st.markdown("##### Independent reviewer finding")
        st.markdown(
            f'<div class="annotation">{_escape(reviewer_finding)}</div>',
            unsafe_allow_html=True,
        )
    st.markdown("##### Evidence")
    st.markdown(f'<div class="evidence">{_escape(evidence)}</div>', unsafe_allow_html=True)

    if latest is not None:
        st.caption(
            f"Latest decision: {DECISION_LABELS.get(latest.decision.value, latest.decision.value)} "
            f"by {latest.actor} at {latest.created_at.astimezone(timezone.utc).strftime('%d %b %Y %H:%M UTC')}."
        )

    st.markdown("##### Human action")
    st.caption("A short reason is required. Decisions are appended to the audit log and never overwrite source data.")
    form_key = (
        f"review-{getattr(report, 'case_id', 'case')}-"
        f"{_scope_token()}-{selected_field}"
    )
    with st.form(form_key, clear_on_submit=True):
        reason = st.text_input(
            "Reason (required)",
            placeholder="At least 8 characters — cite the control or follow-up",
            max_chars=280,
        )
        approve_col, keep_col, investigate_col = st.columns(3)
        approve = approve_col.form_submit_button(
            "Approved",
            type="primary",
            use_container_width=True,
        )
        keep = keep_col.form_submit_button("Rejected", use_container_width=True)
        investigate = investigate_col.form_submit_button(
            "Needs investigation",
            use_container_width=True,
        )
        action = (
            "Approved"
            if approve
            else "Rejected"
            if keep
            else "Needs investigation"
            if investigate
            else None
        )
        if action is not None:
            clean_reason = reason.strip()
            if len(clean_reason) < 8:
                st.error("Enter a reason of at least 8 characters before recording this action.")
            else:
                try:
                    decision = ACTION_TO_DECISION[action]
                    store.append(_new_audit_event(item, decision, clean_reason))
                    st.session_state.decision_flash = f"{action} recorded for {_field_label(selected_field)}"
                    st.rerun()
                except Exception:
                    st.error("The decision could not be recorded. No audit event was added; please try again.")


def _event_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        data = event.model_dump(mode="json")
    elif isinstance(event, dict):
        data = dict(event)
    else:
        data = vars(event)
    return {key: _value(value) for key, value in data.items()}


def _render_audit_log(store: AuditStore, report: Any) -> None:
    case_id = str(getattr(report, "case_id", ""))
    document_id = st.session_state.get("document_id", "unspecified")
    events = store.list_events(
        limit=200,
        case_id=case_id,
        document_id=document_id,
    )
    if not events:
        st.markdown(
            '<div class="empty-state"><strong>No review events yet</strong>Human actions will appear here with timestamp, reviewer, and reason.</div>',
            unsafe_allow_html=True,
        )
        return
    rows = []
    for event in events:
        data = _event_dict(event)
        timestamp = data.get("timestamp") or data.get("created_at")
        decision = str(data.get("decision", ""))
        rows.append(
            {
                "Timestamp (UTC)": str(timestamp).replace("T", " ")[:19],
                "Field": _field_label(str(data.get("field", ""))),
                "Action": DECISION_LABELS.get(decision, decision.replace("_", " ").title()),
                "Reviewer": data.get("actor") or data.get("reviewer") or "demo-user",
                "Reason": data.get("note") or "—",
                "Source": data.get("source_document") or "—",
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Timestamp (UTC)": st.column_config.TextColumn(width="medium"),
            "Field": st.column_config.TextColumn(width="medium"),
            "Action": st.column_config.TextColumn(width="medium"),
            "Reviewer": st.column_config.TextColumn(width="medium"),
            "Reason": st.column_config.TextColumn(width="large"),
            "Source": st.column_config.TextColumn(width="medium"),
        },
    )


def _render_evaluation() -> None:
    error = st.session_state.get("evaluation_error")
    if error:
        st.error(f"Evaluation results could not be loaded: {error}")
    view = _evaluation_view(st.session_state.get("evaluation_results"))
    if not view["available"]:
        st.markdown(
            '<div class="empty-state"><strong>Evaluation service not connected</strong>This view is ready for eval-service results. No model-performance values are substituted or estimated.</div>',
            unsafe_allow_html=True,
        )
        return

    metrics = view["metrics"]
    metric_specs = [
        ("Field accuracy", "field_accuracy", "Exact field agreement"),
        ("Exception recall", "exception_recall", "Known exceptions found"),
        ("Exception precision", "exception_precision", "Flagged exceptions correct"),
        ("Cases evaluated", "cases_evaluated", "Evaluation sample"),
        ("Latency", "latency_ms", "End-to-end median"),
    ]
    for column, (label, key, note) in zip(st.columns(5), metric_specs):
        with column:
            _render_metric(label, _format_eval_metric(key, metrics.get(key)), note)

    st.markdown(
        '<div class="section-heading"><h2>Failure cases</h2><span>Eval-service output</span></div>',
        unsafe_allow_html=True,
    )
    failures = view["failure_cases"]
    if not failures:
        st.info("No failure cases were returned with this evaluation result.")
        return
    rows = []
    for failure in failures:
        rows.append(
            {
                "Case": _lookup(failure, "case_id", _lookup(failure, "case", "—")),
                "Field": _field_label(str(_lookup(failure, "field", ""))),
                "Expected": _lookup(failure, "expected", "—"),
                "Observed": _lookup(failure, "observed", "—"),
                "Failure": _lookup(
                    failure,
                    "reason",
                    _lookup(failure, "error", _lookup(failure, "failure", "—")),
                ),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_context(record: Any, document: Any) -> None:
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
        confidence_values = [
            float(field.confidence)
            for field in fields.values()
            if getattr(field, "confidence", None) is not None
        ]
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
            ("Mean confidence", f"{sum(confidence_values) / len(confidence_values):.0%}" if confidence_values else "—"),
            ("Parser", str(_value(getattr(document, "extraction_method", "Deterministic"))).replace("_", " ").title()),
        ]
        body = "".join(
            f'<div class="kv"><span class="key">{_escape(key)}</span><span class="value">{_escape(value)}</span></div>'
            for key, value in rows
        )
        st.markdown(
            f'<div class="panel"><div class="panel-label">SOURCE DOCUMENT</div><div class="panel-title">{_escape(_display_source_name(source))}</div><div class="panel-subtitle">Structured extraction with field-level provenance</div>{body}</div>',
            unsafe_allow_html=True,
        )


def _render_extraction_ledger(document: Any) -> None:
    fields = getattr(document, "fields", {})
    if not fields:
        st.info("No fields were extracted from this document.")
        return
    for name, extracted in fields.items():
        with st.expander(
            f"{_field_label(name)} · {_format_value(name, getattr(extracted, 'value', None), _currency(st.session_state.record))}"
        ):
            meta = st.columns(4)
            meta[0].metric("Source", _display_source_name(getattr(extracted, "source", "Unavailable")))
            meta[1].metric("Location", _source_location(extracted))
            confidence = getattr(extracted, "confidence", None)
            meta[2].metric("Confidence", f"{float(confidence):.0%}" if confidence is not None else "Unavailable")
            method = getattr(extracted, "method", "deterministic")
            meta[3].metric("Extractor", str(_value(method)).replace("_", " ").title())
            st.markdown(
                f'<div class="evidence">{_escape(getattr(extracted, "evidence", None) or "No evidence snippet was returned.")}</div>',
                unsafe_allow_html=True,
            )


def _init_state() -> None:
    if "record" not in st.session_state:
        st.session_state.record = load_fund_record()
    if "document" not in st.session_state:
        st.session_state.document = None
    if "report" not in st.session_state:
        st.session_state.report = None
    if "case_name" not in st.session_state:
        st.session_state.case_name = "No package loaded"
    if "selected_field" not in st.session_state:
        st.session_state.selected_field = None
    if "source_display_name" not in st.session_state:
        st.session_state.source_display_name = None
    if "document_id" not in st.session_state:
        st.session_state.document_id = "unspecified"
    if "review_report" not in st.session_state:
        st.session_state.review_report = None
    if "show_upload" not in st.session_state:
        st.session_state.show_upload = False


def main() -> None:
    _css()
    _init_state()
    _render_sidebar()
    _render_header()
    _render_quick_actions()

    if st.session_state.pop("flash", None):
        st.toast("Package ready. Reconciliation controls completed.", icon="✅")
    decision_flash = st.session_state.pop("decision_flash", None)
    if decision_flash:
        st.toast(decision_flash, icon="✅")

    record = st.session_state.record
    document = st.session_state.document
    report = st.session_state.report

    if document is None or report is None:
        st.markdown(
            '<div class="section-heading"><h2>Reconciliation workspace</h2><span>Ready for a package</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="empty-state"><strong>No package loaded</strong>Load the Northstar demo for an immediate exception-review workflow, or upload a text-based capital-call notice.</div>',
            unsafe_allow_html=True,
        )
        return

    store = _audit_store()
    case_id = str(getattr(report, "case_id", ""))
    document_id = st.session_state.get("document_id", "unspecified")
    review_by_field = _review_map(
        store,
        case_id,
        document_id,
        [str(getattr(item, "field", "")) for item in _report_items(report)],
    )
    _render_case_status(report, document)
    _render_summary(report, review_by_field)

    if getattr(document, "warnings", None):
        with st.expander("Extraction notes", expanded=False):
            for warning in document.warnings:
                st.warning(str(warning))

    st.markdown(
        '<div class="section-heading"><h2>Reconciliation control</h2><span>Reconcile · evidence · decide</span></div>',
        unsafe_allow_html=True,
    )
    exception_count = sum(_item_status(item) != "PASS" for item in _report_items(report))
    tabs = st.tabs(
        [
            "Reconciliation Results",
            f"Exception Queue ({exception_count})",
            "Audit Log",
            "Fund Record",
            "Extraction Ledger",
        ]
    )
    with tabs[0]:
        _render_reconciliation_workspace(report, record, review_by_field)
    with tabs[1]:
        _render_exception_detail(report, record, store, review_by_field)
    with tabs[2]:
        st.caption("Timestamped, append-only human review events for the active case.")
        _render_audit_log(store, report)
    with tabs[3]:
        _render_context(record, document)
    with tabs[4]:
        st.caption("Field-level values, source locations, confidence, and evidence snippets.")
        _render_extraction_ledger(document)


if __name__ == "__main__":
    main()
