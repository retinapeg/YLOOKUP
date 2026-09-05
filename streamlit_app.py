from __future__ import annotations

import html
import hashlib
import inspect
import io
import json
import os
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

from app.errors import WorkflowError, WorkflowErrorCode, WorkflowStage
from app.evals import EvaluationConfig, run_evaluation
from app.extraction import OpenAICompatibleExtractor, extract_document
from app.file_handling import temporary_upload, validate_upload
from app.models import AuditEvent, ReviewDecision
from app.observability import new_request_id, observe_workflow_stage
from app.reconciliation import reconcile_document
from app.review import OpenAICompatibleEvidenceReviewer, review_reconciliation
from app.sample_data import (
    DEMO_FILES,
    DEMO_RECORD_FILES,
    DEMO_REGISTER_CELLS,
    DEMO_REGISTER_FILES,
    load_fund_record,
)
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
    "__reviewer_escalation__": "Reviewer escalation",
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
    page_title="FundOps Control Room",
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

        .control-flow { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:.45rem; margin:0 0 1rem; }
        .flow-step { position:relative; background:white; border:1px solid var(--line); border-radius:4px; padding:.58rem .66rem; min-height:68px; }
        .flow-step:not(:last-child)::after { content:"→"; position:absolute; right:-.34rem; top:1.3rem; z-index:2; color:#789087; font-weight:800; }
        .flow-number { color:var(--green); font-size:.62rem; font-weight:850; letter-spacing:.09em; }
        .flow-title { color:var(--ink); font-size:.78rem; font-weight:760; margin-top:.12rem; }
        .flow-note { color:var(--muted); font-size:.67rem; line-height:1.25; margin-top:.08rem; }

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
        .break-card { background:#fff; border:1px solid #e2b7b2; border-left:5px solid var(--red); border-radius:5px; padding:1rem 1.1rem; margin:.75rem 0 1rem; }
        .break-head { display:flex; justify-content:space-between; align-items:center; gap:1rem; }
        .break-kicker { color:var(--red); font-size:.7rem; font-weight:850; letter-spacing:.09em; }
        .break-title { color:var(--ink); font-size:1.18rem; font-weight:790; margin-top:.15rem; }
        .break-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.65rem; margin:.8rem 0 .65rem; }
        .break-cell { background:#fbf7f6; border:1px solid #ecd8d5; border-radius:4px; padding:.72rem .8rem; }
        .break-label { color:var(--muted); font-size:.66rem; font-weight:780; letter-spacing:.07em; text-transform:uppercase; }
        .break-value { color:var(--ink); font-size:1.42rem; font-weight:810; margin-top:.18rem; }
        .break-value.red { color:var(--red); }
        .break-sources { color:var(--muted); font-size:.74rem; line-height:1.4; }
        .eval-disclaimer { background:#eef4f1; border:1px solid #cedbd5; border-left:4px solid var(--forest-2); border-radius:4px; padding:.78rem .9rem; color:#3c4d47; font-size:.82rem; line-height:1.45; margin:.35rem 0 .9rem; }

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
          .control-flow { grid-template-columns:repeat(2,minmax(0,1fr)); }
          .flow-step::after { display:none; }
          .break-grid { grid-template-columns:1fr; }
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


def _currency_code(value: Any, fallback: str) -> str:
    if value in (None, ""):
        return fallback
    normalized = str(_value(value)).strip().upper()
    return normalized if len(normalized) == 3 and normalized.isalpha() else fallback


def _document_currency(document: Any, fallback: str) -> str:
    return _currency_code(
        _field_value(document, "currency") if document is not None else None,
        fallback,
    )


def _item_currencies(
    item: Any,
    record: Any,
    document: Any,
) -> tuple[str, str]:
    """Return truthful units for the canonical and incoming numeric values."""

    expected_fallback = _currency(record)
    expected = _currency_code(
        getattr(item, "expected_currency", None),
        expected_fallback,
    )
    if item is not None and hasattr(item, "observed_currency"):
        # Canonical reports explicitly use None when the incoming unit was not
        # established. Do not relabel that numeric value with the fund unit.
        observed = _currency_code(getattr(item, "observed_currency"), "")
    else:
        observed = _document_currency(document, "")
    return expected, observed


def _format_value(field: str, value: Any, currency: str = "GBP") -> str:
    if value is None or value == "":
        return "—"
    value = _value(value)
    if field in AMOUNT_FIELDS:
        try:
            amount = Decimal(str(value).replace(",", ""))
            decimals = 0 if amount == amount.to_integral() else 2
            rendered = f"{amount:,.{decimals}f}"
            return (
                f"{currency} {rendered}"
                if currency
                else f"{rendered} · currency unverified"
            )
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


def _format_difference(
    item: Any,
    currency: str,
    observed_currency: Optional[str] = None,
) -> str:
    difference = getattr(item, "difference", None)
    if difference is None:
        return "—"
    field = str(getattr(item, "field", ""))
    if field in AMOUNT_FIELDS:
        if not observed_currency:
            return "Not comparable without a verified incoming currency"
        if observed_currency and observed_currency.casefold() != currency.casefold():
            return "Not comparable across currencies"
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


def _review_finding(review_report: Any, field: str) -> Any:
    if review_report is None:
        return None
    finder = getattr(review_report, "finding_for", None)
    if callable(finder):
        return finder(field)
    return next(
        (
            finding
            for finding in getattr(review_report, "findings", [])
            if str(getattr(finding, "field", "")) == field
        ),
        None,
    )


def _requires_human_review(item: Any, review_report: Any) -> bool:
    if _item_status(item) != "PASS":
        return True
    field = str(getattr(item, "field", ""))
    finding = _review_finding(review_report, field)
    return bool(getattr(finding, "requires_human_review", False))


def _escalated_items(report: Any, review_report: Any) -> list[Any]:
    return [
        item
        for item in _report_items(report)
        if _requires_human_review(item, review_report)
    ]


def _default_selected_field(report: Any, review_report: Any = None) -> Optional[str]:
    items = _report_items(report)
    candidates = [
        item
        for item in items
        if _requires_human_review(item, review_report)
        and _item_severity(item) == "HIGH"
    ]
    if not candidates:
        candidates = [
            item for item in items if _requires_human_review(item, review_report)
        ]
    if not candidates:
        candidates = items
    return str(getattr(candidates[0], "field", "")) if candidates else None


def _lookup(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _evaluation_view(payload: Any) -> dict[str, Any]:
    """Adapt the real evaluation artifact without inventing performance values."""

    if payload is None:
        return {"available": False, "metrics": {}, "failure_cases": []}
    try:
        summary = _lookup(payload, "summary")
        extraction = _lookup(summary, "extraction")
        exceptions = _lookup(_lookup(summary, "exception_detection"), "field_level")
        rule_correctness = _lookup(
            _lookup(_lookup(summary, "reconciliation"), "isolated_rule_correctness"),
            "rule_correctness",
        )
        abstention = _lookup(
            _lookup(extraction, "missing_abstention"),
            "correct_abstention_rate",
        )
        reviewer = _lookup(_lookup(summary, "reviewer"), "escalation")
        reviewer_metrics = _lookup(reviewer, "end_to_end")
        operating = _lookup(summary, "operating")
        total_latency = _lookup(_lookup(operating, "latency_ms"), "total")
        gates = _lookup(summary, "regression_gates")
        failures = _lookup(
            payload,
            "failures",
            _lookup(_lookup(summary, "failure_analysis"), "worst_failed_cases", []),
        )
        return {
            "available": True,
            "label": _lookup(summary, "label"),
            "generated_at": _lookup(payload, "generated_at"),
            "artifact_provenance": {
                "schema_version": _lookup(payload, "schema_version"),
                "git_commit": _lookup(_lookup(payload, "run", {}), "git_commit"),
                "git_worktree_dirty": _lookup(
                    _lookup(payload, "run", {}), "git_worktree_dirty"
                ),
                "execution_mode": _lookup(_lookup(payload, "run", {}), "mode"),
            },
            "dataset": _lookup(payload, "dataset", {}),
            "metrics": {
                "field_accuracy": _lookup(extraction, "exact_normalized_field_accuracy"),
                "exception_recall": _lookup(exceptions, "recall"),
                "exception_precision": _lookup(exceptions, "precision"),
                "rule_correctness": rule_correctness,
                "abstention": abstention,
                "cases_evaluated": _lookup(_lookup(summary, "sample_size"), "selected_cases"),
                "latency_ms": _lookup(total_latency, "median_ms"),
                "reviewer_recall": _lookup(reviewer_metrics, "recall"),
                "reviewer_precision": _lookup(reviewer_metrics, "precision"),
            },
            "gates": gates,
            "operating": operating,
            "failure_cases": list(failures or []),
        }
    except (KeyError, TypeError):
        return {"available": False, "metrics": {}, "failure_cases": []}


def _rate_value(value: Any) -> Any:
    return _lookup(value, "value") if isinstance(value, Mapping) else value


def _rate_fraction(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    numerator = _lookup(value, "numerator")
    denominator = _lookup(value, "denominator")
    if numerator is None or denominator is None:
        return ""
    return f"{int(numerator):,}/{int(denominator):,}"


def _format_eval_metric(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if key in {
        "field_accuracy",
        "exception_recall",
        "exception_precision",
        "rule_correctness",
        "abstention",
        "reviewer_recall",
        "reviewer_precision",
    }:
        try:
            number = float(_rate_value(value))
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


@st.cache_data(show_spinner=False)
def _run_fixture_evaluation() -> dict[str, Any]:
    """Run the checked-in synthetic corpus through the current local code."""

    return run_evaluation(
        EvaluationConfig(output_path=None, mode="fixture", enable_reviewer=True),
        write_output=False,
    )


@st.cache_resource
def _audit_store(db_path: str) -> AuditStore:
    return AuditStore(Path(db_path))


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
    if hasattr(document, "model_dump"):
        # Extraction timestamps are operational metadata, not part of the
        # evidence package identity. Reprocessing identical evidence must
        # resolve to the same scope so its append-only decisions remain visible.
        extraction_data = document.model_dump(
            mode="json",
            exclude={"fields": {"__all__": {"timestamp"}}},
        )
        extraction_payload = json.dumps(
            extraction_data,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    else:
        extraction_payload = repr(document).encode("utf-8")
    digest = hashlib.sha256(
        content + b"\0" + record_payload + b"\0" + extraction_payload
    ).hexdigest()
    return f"sha256:{digest}"


def _scope_token() -> str:
    document_id = str(st.session_state.get("document_id", "unspecified"))
    return hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:12]


def _selected_extractor() -> Optional[OpenAICompatibleExtractor]:
    if not st.session_state.get("use_ai_extraction", False):
        return None
    # The adapter owns its deterministic fallback. Constructing it without a
    # key is safe and keeps a stale/externally restored UI selection from
    # crashing the offline workflow.
    return OpenAICompatibleExtractor()


def _selected_reviewer() -> Optional[OpenAICompatibleEvidenceReviewer]:
    if not st.session_state.get("use_ai_review", False):
        return None
    # Unlike extraction, review fails closed: the model reviewer returns
    # NOT_REVIEWED when it is unavailable, which keeps every affected row in
    # the human queue.
    return OpenAICompatibleEvidenceReviewer()


def _extraction_stage(
    extractor: Optional[OpenAICompatibleExtractor],
) -> WorkflowStage:
    if extractor is not None and extractor.available:
        return WorkflowStage.AI_EXTRACTION
    return WorkflowStage.DETERMINISTIC_EXTRACTION


def _commit_workflow_result(
    *,
    record: Any,
    document: Any,
    report: Any,
    review_report: Any,
    document_id: str,
    case_name: str,
    source_display_name: Optional[str],
    source_bytes: bytes,
    source_download_name: str,
    flash: str,
    register_path: Optional[Path] = None,
    register_cells: Optional[Mapping[str, str]] = None,
) -> None:
    """Publish a fully completed workflow to the UI in one state update."""

    updates = {
        "record": record,
        "document": document,
        "report": report,
        "review_report": review_report,
        "document_id": document_id,
        "case_name": case_name,
        "source_display_name": source_display_name or case_name,
        "source_bytes": source_bytes,
        "source_download_name": source_download_name,
        "selected_field": _default_selected_field(report, review_report),
        "show_upload": False,
        "flash": flash,
    }
    if register_path is not None:
        updates["register_path"] = register_path
    if register_cells is not None:
        updates["register_cells"] = dict(register_cells)
    st.session_state.update(updates)
    st.session_state.pop("upload_error", None)


def _set_case(case: str, *, request_id: Optional[str] = None) -> str:
    correlation_id = request_id or new_request_id()
    st.session_state.workflow_request_id = correlation_id
    source_path = DEMO_FILES[case]
    extractor = _selected_extractor()
    reviewer = _selected_reviewer()

    with observe_workflow_stage(correlation_id, WorkflowStage.FILE_VALIDATION):
        content = source_path.read_bytes()
        validate_upload(
            source_path.name,
            content,
            "application/pdf",
            request_id=correlation_id,
        )
        record = load_fund_record(DEMO_RECORD_FILES[case])
    with observe_workflow_stage(correlation_id, _extraction_stage(extractor)):
        document = extract_document(
            source_path,
            extractor=extractor,
            case_id=record.case_id,
        )
    with observe_workflow_stage(correlation_id, WorkflowStage.RECONCILIATION):
        report = reconcile_document(record, document)
    with observe_workflow_stage(correlation_id, WorkflowStage.INDEPENDENT_REVIEW):
        review_report = review_reconciliation(report, reviewer=reviewer)

    document_id = _document_scope_id(content, record, document)
    case_name = (
        "Northstar Call 04 · Alderstone"
        if case == "discrepancy"
        else "Northstar clean match · Albion"
    )
    _commit_workflow_result(
        record=record,
        document=document,
        report=report,
        review_report=review_report,
        document_id=document_id,
        case_name=case_name,
        source_display_name=document.source_document,
        source_bytes=content,
        source_download_name=source_path.name,
        register_path=DEMO_REGISTER_FILES[case],
        register_cells=DEMO_REGISTER_CELLS[case],
        flash=f"{case_name} loaded",
    )
    return correlation_id


def _process_upload(uploaded: Any, *, request_id: Optional[str] = None) -> str:
    correlation_id = request_id or new_request_id()
    st.session_state.workflow_request_id = correlation_id
    record = st.session_state.record
    content_type = getattr(uploaded, "type", None)
    content = uploaded.getvalue()
    source_name = Path(str(uploaded.name).replace("\\", "/")).name
    extractor = _selected_extractor()
    reviewer = _selected_reviewer()

    with ExitStack() as staged_uploads:
        with observe_workflow_stage(
            correlation_id,
            WorkflowStage.FILE_VALIDATION,
        ):
            path = staged_uploads.enter_context(
                temporary_upload(
                    uploaded.name,
                    content,
                    content_type,
                    request_id=correlation_id,
                )
            )
        with observe_workflow_stage(correlation_id, _extraction_stage(extractor)):
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
    with observe_workflow_stage(correlation_id, WorkflowStage.RECONCILIATION):
        report = reconcile_document(record, document)
    with observe_workflow_stage(correlation_id, WorkflowStage.INDEPENDENT_REVIEW):
        review_report = review_reconciliation(report, reviewer=reviewer)

    document_id = _document_scope_id(content, record, document)
    _commit_workflow_result(
        record=record,
        document=document,
        report=report,
        review_report=review_report,
        document_id=document_id,
        case_name=source_name,
        source_display_name=source_name,
        source_bytes=content,
        source_download_name=source_name,
        flash=f"{source_name} extracted and reconciled",
    )
    return correlation_id


def _capture_upload_error(
    error: BaseException,
    *,
    request_id: Optional[str] = None,
) -> dict[str, str]:
    message = (
        error.public_message
        if isinstance(error, WorkflowError)
        else "The workflow could not be completed. Check the package and try again."
    )
    correlation_id = request_id or (
        error.request_id if isinstance(error, WorkflowError) else new_request_id()
    )
    payload = {"message": message, "request_id": correlation_id}
    st.session_state.workflow_request_id = correlation_id
    st.session_state.upload_error = payload
    return payload


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
            request_id = new_request_id()
            try:
                _set_case("discrepancy", request_id=request_id)
                st.rerun()
            except Exception as exc:
                _capture_upload_error(exc, request_id=request_id)
        if st.button("Load Clean Match", use_container_width=True):
            request_id = new_request_id()
            try:
                _set_case("matching", request_id=request_id)
                st.rerun()
            except Exception as exc:
                _capture_upload_error(exc, request_id=request_id)

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
            request_id = new_request_id()
            try:
                with st.spinner("Extracting fields and applying controls…"):
                    _process_upload(uploaded, request_id=request_id)
                st.rerun()
            except Exception as exc:
                _capture_upload_error(exc, request_id=request_id)
        _render_upload_error()

        st.markdown('<div class="rail-label">OPTIONAL MODEL MODES</div>', unsafe_allow_html=True)
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
        st.checkbox(
            "Use OpenAI-compatible evidence review",
            value=False,
            disabled=not ai_available,
            key="use_ai_review",
            help=(
                "The reviewer checks one extracted field against its cited evidence; it cannot alter reconciliation."
                if ai_available
                else "Set OPENAI_API_KEY to enable this optional mode. Unreviewed evidence remains in the human queue."
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
            <h1>FundOps Control Room</h1>
            <p>Evidence-first extraction. Deterministic controls. Human decisions.</p>
          </div>
          <div class="masthead-meta">Private markets · Operations control</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_control_flow() -> None:
    steps = (
        ("01", "Messy inputs", "Excel + multi-page PDF"),
        ("02", "AI extraction boundary", "Optional · schema + citations"),
        ("03", "Reconcile", "Decimal/date rules"),
        ("04", "Verify", "Independent evidence check"),
        ("05", "Decide", "Human-owned action"),
        ("06", "Measure", "Versioned synthetic evals"),
    )
    body = "".join(
        '<div class="flow-step">'
        f'<div class="flow-number">{number}</div>'
        f'<div class="flow-title">{_escape(title)}</div>'
        f'<div class="flow-note">{_escape(note)}</div>'
        "</div>"
        for number, title, note in steps
    )
    st.markdown(f'<div class="control-flow">{body}</div>', unsafe_allow_html=True)


def _render_quick_actions() -> None:
    load_col, upload_col, case_col = st.columns([1.45, 1.25, 4.3])
    with load_col:
        if st.button(
            "Load Demo Case",
            type="primary",
            use_container_width=True,
            key="load_northstar",
        ):
            request_id = new_request_id()
            try:
                with st.spinner("Loading Northstar and running controls…"):
                    _set_case("discrepancy", request_id=request_id)
                st.rerun()
            except Exception as exc:
                _capture_upload_error(exc, request_id=request_id)
                _render_upload_error()
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
                request_id = new_request_id()
                try:
                    with st.spinner("Extracting fields and applying controls…"):
                        _process_upload(uploaded, request_id=request_id)
                    st.rerun()
                except Exception as exc:
                    _capture_upload_error(exc, request_id=request_id)
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


def _render_case_status(report: Any, document: Any, review_report: Any = None) -> None:
    overall = str(_value(getattr(report, "overall_status", "REVIEW"))).upper()
    is_pass = overall == "PASS" and not _escalated_items(report, review_report)
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


def _render_flagship_break(report: Any, record: Any, document: Any = None) -> None:
    amount_item = next(
        (
            item
            for item in _report_items(report)
            if str(getattr(item, "field", "")) == "capital_call_amount"
            and _item_status(item) != "PASS"
        ),
        None,
    )
    if amount_item is None:
        return

    provenance = getattr(amount_item, "provenance", None)
    register_path = Path(st.session_state.get("register_path"))
    register_cell = st.session_state.get("register_cells", {}).get(
        "capital_call_amount", "LP Register!I2"
    )
    source = _display_source_name(
        getattr(provenance, "source", getattr(report, "source_document", "notice.pdf"))
    )
    location = _source_location(provenance)
    expected_currency, observed_currency = _item_currencies(
        amount_item,
        record,
        document or st.session_state.get("document"),
    )
    expected = _format_value(
        "capital_call_amount", getattr(amount_item, "expected", None), expected_currency
    )
    observed = _format_value(
        "capital_call_amount", getattr(amount_item, "observed", None), observed_currency
    )
    variance = _format_difference(
        amount_item,
        expected_currency,
        observed_currency,
    )
    st.markdown(
        "<div class=\"break-card\">"
        '<div class="break-head"><div>'
        '<div class="break-kicker">CONTROL BREAK · CAPITAL CALL AMOUNT</div>'
        f'<div class="break-title">{_escape(_item_severity(amount_item))} severity variance requires a human decision</div>'
        f'</div><span class="status status-mismatch">{_escape(_item_severity(amount_item))} SEVERITY</span></div>'
        '<div class="break-grid">'
        f'<div class="break-cell"><div class="break-label">Expected capital call</div><div class="break-value">{_escape(expected)}</div></div>'
        f'<div class="break-cell"><div class="break-label">Incoming notice</div><div class="break-value">{_escape(observed)}</div></div>'
        f'<div class="break-cell"><div class="break-label">Deterministic variance</div><div class="break-value red">{_escape(variance)}</div></div>'
        "</div>"
        f'<div class="break-sources"><strong>Expected evidence:</strong> {_escape(register_path.name)} · {_escape(register_cell)} &nbsp;|&nbsp; '
        f'<strong>Incoming evidence:</strong> {_escape(source)} · {_escape(location)} &nbsp;|&nbsp; '
        '<strong>Control:</strong> exact Decimal comparison, zero tolerance</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _render_summary(
    report: Any,
    review_by_field: Mapping,
    review_report: Any = None,
) -> None:
    items = _report_items(report)
    exceptions = _escalated_items(report, review_report)
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


def _table_items(report: Any, review_report: Any = None) -> list[Any]:
    return sorted(
        _report_items(report),
        key=lambda item: (
            not _requires_human_review(item, review_report),
            _item_severity(item) != "HIGH",
        ),
    )


def _review_status(
    item: Any,
    review_by_field: Mapping,
    review_report: Any = None,
) -> str:
    if not _requires_human_review(item, review_report):
        return "Not required"
    field = str(getattr(item, "field", ""))
    event = review_by_field.get(field)
    if event is None:
        return "Awaiting review"
    return DECISION_LABELS.get(event.decision.value, event.decision.value.replace("_", " ").title())


def _display_source_name(raw_source: Any) -> str:
    session_name = st.session_state.get("source_display_name")
    return str(session_name) if session_name else Path(str(raw_source)).name


def _table_frame(
    report: Any,
    record: Any,
    review_by_field: Mapping,
    review_report: Any = None,
    document: Any = None,
) -> tuple[pd.DataFrame, list[Any]]:
    items = _table_items(report, review_report)
    document = document or st.session_state.get("document")
    rows = []
    for item in items:
        provenance = getattr(item, "provenance", None)
        source = getattr(provenance, "source", None) or getattr(report, "source_document", "—")
        page = getattr(provenance, "page", None)
        source_label = _display_source_name(source)
        if page is not None:
            source_label = f"{source_label} · p.{page}"
        field = str(getattr(item, "field", ""))
        expected_currency, observed_currency = _item_currencies(
            item,
            record,
            document,
        )
        rows.append(
            {
                "Field": _field_label(field),
                "Expected": _format_value(
                    field,
                    getattr(item, "expected", None),
                    expected_currency,
                ),
                "Observed": _format_value(
                    field,
                    getattr(item, "observed", None),
                    observed_currency,
                ),
                "Status": _display_status(item),
                "Severity": _item_severity(item),
                "Source": source_label,
                "Review status": _review_status(item, review_by_field, review_report),
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


def _render_reconciliation_table(
    report: Any,
    record: Any,
    review_by_field: Mapping,
    review_report: Any = None,
    document: Any = None,
) -> None:
    frame, items = _table_frame(
        report,
        record,
        review_by_field,
        review_report,
        document,
    )
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


def _render_reconciliation_workspace(
    report: Any,
    record: Any,
    review_by_field: Mapping,
    review_report: Any = None,
    document: Any = None,
) -> None:
    st.caption("Select any row to inspect the document evidence. Exceptions are prioritized at the top.")
    table_col, evidence_col = st.columns([4.2, 1.45])
    with table_col:
        _render_reconciliation_table(
            report,
            record,
            review_by_field,
            review_report,
            document,
        )
    with evidence_col:
        _render_evidence_drawer(_selected_item(report), report)


def _audit_scalar(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = _value(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _new_audit_event(
    item: Any,
    decision: ReviewDecision,
    note: str,
    *,
    request_id: str,
) -> AuditEvent:
    report = st.session_state.report
    document = st.session_state.document
    case_id = str(
        getattr(report, "case_id", getattr(document, "document_id", getattr(document, "id", "case")))
    )
    provenance = getattr(item, "provenance", None)
    review_report = st.session_state.get("review_report")
    finding = (
        review_report.finding_for(str(getattr(item, "field", "")))
        if review_report is not None
        else None
    )
    reviewer_status = None
    if finding is not None:
        reviewer_status = (
            f"{finding.status.value} / "
            f"{str(_value(finding.review_method)).replace('_', ' ')}"
        )
    expected_currency, observed_currency = _item_currencies(
        item,
        st.session_state.record,
        document,
    )
    kwargs = {
        "case_id": case_id,
        "document_id": st.session_state.get("document_id", "unspecified"),
        "source_document": str(getattr(document, "source_document", "")) or None,
        "source_location": _source_location(provenance),
        "field": getattr(item, "field", "unknown"),
        "expected_value": _audit_scalar(getattr(item, "expected", None)),
        "observed_value": _audit_scalar(getattr(item, "observed", None)),
        "expected_currency": expected_currency or None,
        "observed_currency": observed_currency or None,
        "difference": _audit_scalar(getattr(item, "difference", None)),
        "reviewer_status": reviewer_status,
        "request_id": request_id,
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
    review_report: Any = None,
    document: Any = None,
) -> None:
    exceptions = _escalated_items(report, review_report)
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
    finding = _review_finding(review_report, selected_field)
    if finding is None:
        reviewer_finding = "Independent evidence check was unavailable."
        reviewer_method = "Unavailable"
    else:
        reviewer_finding = f"{finding.status.value}: {finding.review_reason}"
        reviewer_method = str(_value(finding.review_method)).replace("_", " ").title()
    expected_currency, observed_currency = _item_currencies(
        item,
        record,
        document or st.session_state.get("document"),
    )
    latest = review_by_field.get(selected_field)

    status_col, severity_col, review_col = st.columns([1, 1, 2])
    status_col.metric("Status", _display_status(item))
    severity_col.metric("Severity", _item_severity(item))
    review_col.metric(
        "Review status",
        _review_status(item, review_by_field, review_report),
    )
    st.markdown(
        "<div class=\"comparison-grid\">"
        f'<div class="comparison-cell"><div class="comparison-label">Expected</div><div class="comparison-value">{_escape(_format_value(selected_field, getattr(item, "expected", None), expected_currency))}</div></div>'
        f'<div class="comparison-cell"><div class="comparison-label">Observed</div><div class="comparison-value">{_escape(_format_value(selected_field, getattr(item, "observed", None), observed_currency))}</div></div>'
        f'<div class="comparison-cell"><div class="comparison-label">Difference</div><div class="comparison-value">{_escape(_format_difference(item, expected_currency, observed_currency))}</div></div>'
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
        st.caption(f"Reviewer: {reviewer_method}. Evidence support is not approval of the notice.")
    st.markdown("##### Evidence")
    st.markdown(f'<div class="evidence">{_escape(evidence)}</div>', unsafe_allow_html=True)
    st.caption(
        f"Exact locator: {_display_source_name(getattr(provenance, 'source', 'source'))} · "
        f"{_source_location(provenance)}"
    )
    source_bytes = st.session_state.get("source_bytes")
    source_name = st.session_state.get("source_download_name")
    if source_bytes and source_name:
        st.download_button(
            "Download exact source document",
            data=source_bytes,
            file_name=str(source_name),
            mime=("application/pdf" if str(source_name).casefold().endswith(".pdf") else "text/plain"),
            key=f"download_source_{_scope_token()}_{selected_field}",
        )
    st.info(
        "Fail-closed policy: detected missing, unparseable, low-confidence, conflicting, or "
        "unreviewed evidence is routed to a human. The reviewer can challenge evidence; "
        "it cannot clear a control or approve a payment."
    )

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
        investigate_col, approve_col, keep_col = st.columns(3)
        investigate = investigate_col.form_submit_button(
            "Needs investigation",
            type="primary",
            use_container_width=True,
        )
        approve = approve_col.form_submit_button("Approved", use_container_width=True)
        keep = keep_col.form_submit_button("Rejected", use_container_width=True)
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
                request_id = new_request_id()
                try:
                    decision = ACTION_TO_DECISION[action]
                    with observe_workflow_stage(
                        request_id,
                        WorkflowStage.AUDIT_APPEND,
                    ):
                        try:
                            store.append(
                                _new_audit_event(
                                    item,
                                    decision,
                                    clean_reason,
                                    request_id=request_id,
                                )
                            )
                        except Exception as exc:
                            raise WorkflowError(
                                WorkflowErrorCode.AUDIT_WRITE_FAILED,
                                request_id=request_id,
                                stage=WorkflowStage.AUDIT_APPEND,
                            ) from exc
                    st.session_state.decision_flash = f"{action} recorded for {_field_label(selected_field)}"
                    st.rerun()
                except WorkflowError as exc:
                    st.error(
                        f"{exc.public_message} Reference: {exc.request_id}"
                    )


def _event_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        data = event.model_dump(mode="json")
    elif isinstance(event, dict):
        data = dict(event)
    else:
        data = vars(event)
    return {key: _value(value) for key, value in data.items()}


def _render_audit_log(
    store: AuditStore,
    report: Any,
    record: Any,
    document: Any = None,
) -> None:
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
    items_by_field = {
        str(getattr(item, "field", "")): item for item in _report_items(report)
    }
    for event in events:
        data = _event_dict(event)
        timestamp = data.get("timestamp") or data.get("created_at")
        decision = str(data.get("decision", ""))
        field = str(data.get("field", ""))
        item = items_by_field.get(field)
        current_expected_currency, current_observed_currency = _item_currencies(
            item,
            record,
            document or st.session_state.get("document"),
        )
        expected_currency = str(
            data.get("expected_currency") or current_expected_currency or ""
        )
        observed_currency = str(
            data.get("observed_currency") or current_observed_currency or ""
        )
        document_id = str(data.get("document_id") or "unspecified")
        digest = (
            f"sha256:{document_id.removeprefix('sha256:')[:12]}…"
            if document_id.startswith("sha256:")
            else document_id
        )
        difference = data.get("difference")
        if field in AMOUNT_FIELDS and difference not in (None, ""):
            if observed_currency.casefold() != expected_currency.casefold():
                difference = "Not comparable across currencies"
            else:
                try:
                    amount = Decimal(str(difference))
                    sign = "+" if amount > 0 else "−" if amount < 0 else ""
                    difference = f"{expected_currency} {sign}{abs(amount):,.2f}"
                except InvalidOperation:
                    pass
        rows.append(
            {
                "Event": data.get("id") or "—",
                "Timestamp (UTC)": str(timestamp).replace("T", " ")[:19],
                "Field": _field_label(field),
                "Human action": DECISION_LABELS.get(decision, decision.replace("_", " ").title()),
                "Reason": data.get("note") or "—",
                "Expected": _format_value(
                    field,
                    data.get("expected_value"),
                    expected_currency,
                ),
                "Observed": _format_value(
                    field,
                    data.get("observed_value"),
                    observed_currency,
                ),
                "Variance": difference or "—",
                "Evidence review": data.get("reviewer_status") or "—",
                "Reviewer": data.get("actor") or data.get("reviewer") or "demo-user",
                "Source": data.get("source_document") or "—",
                "Location": data.get("source_location") or "—",
                "Package digest": digest,
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Event": st.column_config.NumberColumn(width="small"),
            "Timestamp (UTC)": st.column_config.TextColumn(width="medium"),
            "Field": st.column_config.TextColumn(width="medium"),
            "Human action": st.column_config.TextColumn(width="medium"),
            "Reason": st.column_config.TextColumn(width="large"),
            "Expected": st.column_config.TextColumn(width="medium"),
            "Observed": st.column_config.TextColumn(width="medium"),
            "Variance": st.column_config.TextColumn(width="medium"),
            "Evidence review": st.column_config.TextColumn(width="medium"),
            "Reviewer": st.column_config.TextColumn(width="medium"),
            "Source": st.column_config.TextColumn(width="medium"),
            "Location": st.column_config.TextColumn(width="medium"),
            "Package digest": st.column_config.TextColumn(width="medium"),
        },
    )


def _render_evaluation() -> None:
    try:
        payload = _run_fixture_evaluation()
    except Exception as exc:
        st.error(
            "The benchmark could not be run. No result values were substituted. "
            f"Error type: {type(exc).__name__}."
        )
        return
    view = _evaluation_view(payload)
    if not view["available"]:
        st.markdown(
            '<div class="empty-state"><strong>Evaluation result unavailable</strong>The artifact did not match the expected schema. No values were substituted or estimated.</div>',
            unsafe_allow_html=True,
        )
        return

    dataset = view["dataset"]
    operating = view["operating"]
    model_calls = _lookup(_lookup(operating, "model_calls", {}), "count", 0)
    selected_documents = _lookup(
        _lookup(payload, "summary", {}).get("sample_size", {}),
        "selected_documents",
        0,
    )
    generated_at = str(view.get("generated_at") or "").replace("T", " ")[:19]
    st.markdown(
        '<div class="eval-disclaimer"><strong>Actual executable benchmark · synthetic regression only.</strong> '
        f'This run processed {int(selected_documents):,} fictional documents through the current '
        f'deterministic fixture path and made {int(model_calls):,} model calls. It is not a claim about '
        'LLM accuracy or production performance.</div>',
        unsafe_allow_html=True,
    )

    gate_payload = view.get("gates") or {}
    gate_rows = list(_lookup(gate_payload, "gates", []) or [])
    gates_passed = sum(bool(_lookup(gate, "passed", False)) for gate in gate_rows)
    metrics = view["metrics"]
    metric_specs = [
        ("Exact extraction", "field_accuracy", _rate_fraction(metrics["field_accuracy"])),
        ("Field exception recall", "exception_recall", _rate_fraction(metrics["exception_recall"])),
        ("Isolated rule correctness", "rule_correctness", _rate_fraction(metrics["rule_correctness"])),
        ("Correct abstention", "abstention", _rate_fraction(metrics["abstention"])),
        ("Regression gates", "gates", f"{gates_passed}/{len(gate_rows)} transparent count gates"),
    ]
    for column, (label, key, note) in zip(st.columns(5), metric_specs):
        with column:
            value = (
                f"{gates_passed}/{len(gate_rows)}"
                if key == "gates"
                else _format_eval_metric(key, metrics.get(key))
            )
            tone = "green" if key == "gates" and bool(_lookup(gate_payload, "passed")) else ""
            _render_metric(label, value, note, tone)
    st.caption(
        "Gate scope is explicit and count-based. It does not validate controls that need context "
        "beyond the current field comparison—such as ambiguity, cross-page, batch, cross-field, "
        "or multi-document checks; reviewer recall below exposes that gap."
    )

    detail_left, detail_right = st.columns([1.35, 1])
    with detail_left:
        st.markdown("##### What this run measured")
        detail_rows = [
            {
                "Measure": "Exception precision",
                "Result": _format_eval_metric("exception_precision", metrics["exception_precision"]),
                "Support": _rate_fraction(metrics["exception_precision"]),
            },
            {
                "Measure": "Independent-review escalation recall",
                "Result": _format_eval_metric("reviewer_recall", metrics["reviewer_recall"]),
                "Support": _rate_fraction(metrics["reviewer_recall"]),
            },
            {
                "Measure": "Independent-review escalation precision",
                "Result": _format_eval_metric("reviewer_precision", metrics["reviewer_precision"]),
                "Support": _rate_fraction(metrics["reviewer_precision"]),
            },
            {
                "Measure": "Median end-to-end latency",
                "Result": _format_eval_metric("latency_ms", metrics["latency_ms"]),
                "Support": f"n={int(selected_documents):,}",
            },
        ]
        st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)
    with detail_right:
        st.markdown("##### Reproducibility envelope")
        artifact_provenance = view.get("artifact_provenance") or {}
        git_commit = str(_lookup(artifact_provenance, "git_commit", "") or "")
        dirty = _lookup(artifact_provenance, "git_worktree_dirty")
        worktree_state = (
            "dirty — includes uncommitted changes"
            if dirty is True
            else "clean"
            if dirty is False
            else "unavailable"
        )
        st.markdown(
            _drawer_row("Benchmark", view.get("label") or "—")
            + _drawer_row("Dataset", _lookup(dataset, "id", "—"))
            + _drawer_row("Dataset version", _lookup(dataset, "schema_version", "—"))
            + _drawer_row("Dataset SHA-256", str(_lookup(dataset, "sha256", "—"))[:16] + "…")
            + _drawer_row("Code commit", git_commit[:12] if git_commit else "unavailable")
            + _drawer_row("Worktree", worktree_state)
            + _drawer_row("Generated (UTC)", generated_at or "—")
            + _drawer_row("Model calls", model_calls),
            unsafe_allow_html=True,
        )
        download_payload = json.dumps(payload, indent=2, sort_keys=True)
        st.download_button(
            "Download full evaluation artifact",
            data=download_payload,
            file_name="fundops-synthetic-evaluation.json",
            mime="application/json",
            key="download_evaluation_artifact",
        )

    with st.expander("Regression gates", expanded=False):
        for gate in gate_rows:
            icon = "✅" if _lookup(gate, "passed", False) else "❌"
            name = str(_lookup(gate, "name", "gate")).replace("_", " ")
            observed = _lookup(gate, "observed", _lookup(gate, "observed_wrong", "—"))
            if _lookup(gate, "required", None) is not None:
                requirement = f"required {_lookup(gate, 'required')}"
            elif _lookup(gate, "required_minimum", None) is not None:
                requirement = f"minimum {_lookup(gate, 'required_minimum')}"
            elif _lookup(gate, "required_maximum", None) is not None:
                requirement = f"maximum {_lookup(gate, 'required_maximum')}"
            else:
                requirement = "threshold not supplied"
            st.write(f"{icon} {name}: observed {observed}; {requirement}")
        st.caption(_lookup(gate_payload, "policy", ""))

    st.markdown(
        f'<div class="section-heading"><h2>Known failure cases</h2><span>All {len(view["failure_cases"]):,} shown · visible, not hidden</span></div>',
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
                "Expected": _clean(_lookup(failure, "expected", "—")),
                "Observed": _clean(_lookup(failure, "observed", "—")),
                "Failure": _lookup(
                    failure,
                    "failure_category",
                    _lookup(failure, "root_cause", "—"),
                ),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "The remaining cases identify concrete next work: locale-aware amounts, OCR-like text, "
        "entity-name resolution, conflicting values, and cross-document controls."
    )


def _render_context(record: Any, document: Any) -> None:
    currency = _currency(record)
    register_path = Path(st.session_state.get("register_path"))
    register_cells = st.session_state.get("register_cells", {})
    left, right = st.columns(2)
    with left:
        rows = [
            ("Fund", getattr(record, "fund_name", None)),
            ("Investor", getattr(record, "investor_name", None)),
            ("Commitment", _format_value("commitment_amount", getattr(record, "commitment_amount", None), currency)),
            ("Expected call", _format_value("capital_call_amount", getattr(record, "capital_call_amount", None), currency)),
            ("Expected due", _format_value("due_date", getattr(record, "due_date", None), currency)),
            ("Excel amount cell", register_cells.get("capital_call_amount", "—")),
            ("Excel due-date cell", register_cells.get("due_date", "—")),
        ]
        body = "".join(
            f'<div class="kv"><span class="key">{_escape(key)}</span><span class="value">{_escape(value)}</span></div>'
            for key, value in rows
        )
        st.markdown(
            f'<div class="panel"><div class="panel-label">EXPECTED / EXCEL SOURCE</div><div class="panel-title">{_escape(register_path.name)}</div><div class="panel-subtitle">Checked-in workbook mirror · the MVP loads its matching canonical JSON snapshot</div>{body}</div>',
            unsafe_allow_html=True,
        )
    with right:
        fields = getattr(document, "fields", {})
        confidence_values = [
            float(field.confidence)
            for field in fields.values()
            if getattr(field, "confidence", None) is not None
        ]
        cited_pages = [
            int(field.page)
            for field in fields.values()
            if getattr(field, "page", None) is not None
        ]
        page_count: Any = max(cited_pages) if cited_pages else "—"
        source_bytes = st.session_state.get("source_bytes")
        source_name = st.session_state.get("source_download_name")
        if (
            isinstance(source_bytes, bytes)
            and source_bytes
            and str(source_name).casefold().endswith(".pdf")
        ):
            try:
                from pypdf import PdfReader

                page_count = len(PdfReader(io.BytesIO(source_bytes), strict=False).pages)
            except Exception:
                # Evidence citations remain a useful lower bound if page-count
                # inspection is unavailable; extraction itself has already run.
                pass
        source = getattr(document, "source_document", getattr(document, "filename", "Uploaded document"))
        rows = [
            ("Document type", _format_value("document_type", _field_value(document, "document_type"))),
            ("Fields extracted", len(fields)),
            ("Pages", page_count),
            ("Mean confidence", f"{sum(confidence_values) / len(confidence_values):.0%}" if confidence_values else "—"),
            ("Parser", str(_value(getattr(document, "extraction_method", "Deterministic"))).replace("_", " ").title()),
        ]
        body = "".join(
            f'<div class="kv"><span class="key">{_escape(key)}</span><span class="value">{_escape(value)}</span></div>'
            for key, value in rows
        )
        st.markdown(
            f'<div class="panel"><div class="panel-label">INCOMING / SOURCE DOCUMENT</div><div class="panel-title">{_escape(_display_source_name(source))}</div><div class="panel-subtitle">Structured extraction with field-level provenance</div>{body}</div>',
            unsafe_allow_html=True,
        )
    st.caption(
        "The workbook is a real synthetic XLSX artifact. This MVP does not connect to a live Excel tenant; "
        "the checked-in JSON record is its canonical demo snapshot."
    )
    workbook_col, source_col = st.columns(2)
    with workbook_col:
        if register_path.is_file():
            st.download_button(
                "Download expected Excel register",
                data=register_path.read_bytes(),
                file_name=register_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"download_register_{_scope_token()}",
                use_container_width=True,
            )
    with source_col:
        source_bytes = st.session_state.get("source_bytes")
        source_name = st.session_state.get("source_download_name")
        if source_bytes and source_name:
            st.download_button(
                "Download incoming source document",
                data=source_bytes,
                file_name=str(source_name),
                mime=("application/pdf" if str(source_name).casefold().endswith(".pdf") else "text/plain"),
                key=f"download_context_source_{_scope_token()}",
                use_container_width=True,
            )


def _render_extraction_ledger(document: Any) -> None:
    fields = getattr(document, "fields", {})
    if not fields:
        st.info("No fields were extracted from this document.")
        return
    run_method = str(
        _value(getattr(document, "extraction_method", "DETERMINISTIC"))
    ).replace("_", " ").title()
    observed_currency = _document_currency(document, "")
    st.markdown(
        '<div class="eval-disclaimer"><strong>Extraction contract.</strong> '
        f'Current document mode: {_escape(run_method)}. The optional AI adapter may structure '
        'page text, but every accepted model field must retain a typed value, page, confidence, '
        'and evidence found in the source. Field-level methods below disclose any hybrid fill-ins.'
        '</div>',
        unsafe_allow_html=True,
    )
    for name, extracted in fields.items():
        with st.expander(
            f"{_field_label(name)} · {_format_value(name, getattr(extracted, 'value', None), observed_currency)}"
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
    if "source_bytes" not in st.session_state:
        st.session_state.source_bytes = None
    if "source_download_name" not in st.session_state:
        st.session_state.source_download_name = None
    if "register_path" not in st.session_state:
        st.session_state.register_path = DEMO_REGISTER_FILES["matching"]
    if "register_cells" not in st.session_state:
        st.session_state.register_cells = DEMO_REGISTER_CELLS["matching"]


def main() -> None:
    _css()
    _init_state()
    _render_sidebar()
    _render_header()
    _render_control_flow()
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

    store = _audit_store(str(DB_PATH))
    case_id = str(getattr(report, "case_id", ""))
    document_id = st.session_state.get("document_id", "unspecified")
    review_by_field = _review_map(
        store,
        case_id,
        document_id,
        [str(getattr(item, "field", "")) for item in _report_items(report)],
    )
    review_report = st.session_state.get("review_report")
    _render_case_status(report, document, review_report)
    _render_flagship_break(report, record, document)
    _render_summary(report, review_by_field, review_report)

    if getattr(document, "warnings", None):
        with st.expander("Extraction notes", expanded=False):
            for warning in document.warnings:
                st.warning(str(warning))

    st.markdown(
        '<div class="section-heading"><h2>Reconciliation control</h2><span>Reconcile · evidence · decide</span></div>',
        unsafe_allow_html=True,
    )
    exception_count = len(_escalated_items(report, review_report))
    tabs = st.tabs(
        [
            "Reconciliation Results",
            f"Exception Queue ({exception_count})",
            "Audit Log",
            "Fund Record",
            "Extraction Ledger",
            "Evals",
        ]
    )
    with tabs[0]:
        _render_reconciliation_workspace(
            report,
            record,
            review_by_field,
            review_report,
            document,
        )
    with tabs[1]:
        _render_exception_detail(
            report,
            record,
            store,
            review_by_field,
            review_report,
            document,
        )
    with tabs[2]:
        st.caption(
            "Timestamped, append-only human decisions with key reconciliation and evidence-review context."
        )
        _render_audit_log(store, report, record, document)
    with tabs[3]:
        _render_context(record, document)
    with tabs[4]:
        st.caption("Field-level values, source locations, confidence, and evidence snippets.")
        _render_extraction_ledger(document)
    with tabs[5]:
        _render_evaluation()


if __name__ == "__main__":
    main()
