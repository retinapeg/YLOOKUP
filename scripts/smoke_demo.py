#!/usr/bin/env python3
"""Exercise the complete FundOps demo workflow without network access."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from app.extraction import OpenAICompatibleExtractor
from app.models import ExtractionMethod, ReconciliationStatus
from app.review import review_reconciliation
from app.sample_data import DEMO_FILES, load_demo_case
from app.storage import AuditStore


def _result(report, field_name: str):
    return next(item for item in report.results if item.field == field_name)


def main() -> int:
    matching_record, matching_document, matching_report = load_demo_case("matching")
    if matching_report.overall_status is not ReconciliationStatus.PASS:
        raise AssertionError("the clean control case did not pass")
    if matching_report.exceptions:
        raise AssertionError("the clean control case unexpectedly created exceptions")

    record, document, report = load_demo_case("discrepancy")
    review = review_reconciliation(report)
    amount = _result(report, "capital_call_amount")
    due_date = _result(report, "due_date")
    if report.overall_status is not ReconciliationStatus.MISMATCH:
        raise AssertionError("the Northstar exception case did not produce a mismatch")
    if amount.difference != Decimal("25000.00"):
        raise AssertionError("the Northstar amount variance is not GBP 25,000")
    if due_date.difference != -2:
        raise AssertionError("the Northstar due-date variance is not two days early")
    if not all(
        finding.requires_human_review
        for finding in review.findings
        if finding.field in {"capital_call_amount", "due_date"}
    ):
        raise AssertionError("a deterministic mismatch was cleared by evidence review")

    source_bytes = DEMO_FILES["discrepancy"].read_bytes()
    document_id = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    with tempfile.TemporaryDirectory(prefix="fundops-smoke-") as temporary_directory:
        store = AuditStore(Path(temporary_directory) / "audit.db")
        event = store.record_decision(
            report.case_id,
            "capital_call_amount",
            "NEEDS_INVESTIGATION",
            document_id=document_id,
            source_document=document.source_document,
            source_location="PDF page 1",
            expected_value=str(amount.expected),
            observed_value=str(amount.observed),
            difference=str(amount.difference),
            reviewer_status="SUPPORTED / DETERMINISTIC FIXTURE",
            note="Offline smoke test requires administrator confirmation",
            actor="smoke-test",
        )
        if store.latest_decision(
            report.case_id,
            "capital_call_amount",
            document_id=document_id,
        ) != event:
            raise AssertionError("the human decision was not persisted in audit history")

    previous_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        _, fallback_document, fallback_report = load_demo_case(
            "discrepancy",
            extractor=OpenAICompatibleExtractor(),
        )
    finally:
        if previous_key is not None:
            os.environ["OPENAI_API_KEY"] = previous_key
    if fallback_document.extraction_method is not ExtractionMethod.FALLBACK:
        raise AssertionError("no-key model mode did not disclose deterministic fallback")
    if fallback_report.overall_status is not ReconciliationStatus.MISMATCH:
        raise AssertionError("no-key fallback changed the deterministic control result")

    print(
        json.dumps(
            {
                "clean_case": {
                    "method": matching_document.extraction_method.value,
                    "status": matching_report.overall_status.value,
                    "exceptions": len(matching_report.exceptions),
                },
                "northstar_case": {
                    "method": document.extraction_method.value,
                    "status": report.overall_status.value,
                    "exceptions": len(report.exceptions),
                    "amount_difference": str(amount.difference),
                    "due_date_difference_days": due_date.difference,
                    "review_findings": len(review.findings),
                    "audit_append": "PASS",
                },
                "no_key_model_mode": {
                    "method": fallback_document.extraction_method.value,
                    "status": fallback_report.overall_status.value,
                    "warnings": len(fallback_document.warnings),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
