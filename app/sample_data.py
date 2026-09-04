"""Load the bundled Northstar demo cases without network access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from app.extraction import extract_document
from app.models import ExtractedDocument, FundRecord, ReconciliationReport
from app.reconciliation import reconcile_document


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FUND_RECORD_PATH = DATA_DIR / "fund_record.json"
SAMPLE_DOCUMENTS_DIR = DATA_DIR / "sample_documents"

DEMO_FILES = {
    "matching": SAMPLE_DOCUMENTS_DIR / "matching_capital_call.pdf",
    "discrepancy": SAMPLE_DOCUMENTS_DIR / "discrepancy_capital_call.pdf",
}


def load_fund_record(path: Path = FUND_RECORD_PATH) -> FundRecord:
    with path.open("r", encoding="utf-8") as handle:
        return FundRecord.model_validate(json.load(handle))


def load_demo_case(
    case: str = "discrepancy",
) -> Tuple[FundRecord, ExtractedDocument, ReconciliationReport]:
    """Load, extract, and reconcile one complete offline demo case."""

    if case not in DEMO_FILES:
        choices = ", ".join(sorted(DEMO_FILES))
        raise ValueError("unknown demo case; choose one of: {}".format(choices))

    record = load_fund_record()
    document = extract_document(DEMO_FILES[case], case_id=record.case_id)
    report = reconcile_document(record, document)
    return record, document, report


__all__ = ["DEMO_FILES", "load_demo_case", "load_fund_record"]
