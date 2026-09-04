from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader

from app.extraction import DeterministicExtractor
from app.models import (
    DocumentType,
    ExtractedDocument,
    ExtractedField,
    FundRecord,
    ReconciliationStatus,
    Severity,
)
from app.reconciliation import reconcile_document


ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data/gold/capital_call_reconciliation.json"
FIELDS = (
    "fund_name",
    "investor_name",
    "commitment_amount",
    "capital_call_amount",
    "call_date",
    "due_date",
    "currency",
    "bank_account_reference",
    "management_fee",
    "document_type",
)
STATUSES = {"PASS", "MISMATCH", "MISSING", "REVIEW"}
SEVERITIES = {"NONE", "LOW", "MEDIUM", "HIGH"}
SEVERITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
NUMERIC_COLUMNS = {
    "commitment_amount",
    "drawn_to_date_before_call",
    "remaining_commitment_amount",
    "capital_call_amount",
    "capital_call_percentage",
    "call_number",
    "management_fee",
}
DATE_COLUMNS = {"call_date", "due_date"}


def _corpus() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def _csv_rows(relative_path: str) -> list[dict[str, str]]:
    with (ROOT / relative_path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference).group(0)
    result = 0
    for character in letters:
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _xlsx_matrix(relative_path: str) -> list[list[str | Decimal | date | None]]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    path = ROOT / relative_path
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("x:si", namespace):
                shared_strings.append("".join(node.text or "" for node in item.findall(".//x:t", namespace)))
        sheet_root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    sparse_rows: list[dict[int, str | None]] = []
    max_column = 0
    for row in sheet_root.findall(".//x:sheetData/x:row", namespace):
        values: dict[int, str | None] = {}
        for cell in row.findall("x:c", namespace):
            index = _column_index(cell.attrib["r"])
            max_column = max(max_column, index)
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//x:t", namespace))
            else:
                value_node = cell.find("x:v", namespace)
                raw = value_node.text if value_node is not None else None
                if raw is not None and cell_type == "s":
                    value = shared_strings[int(raw)]
                elif raw is not None and cell_type == "b":
                    value = "TRUE" if raw == "1" else "FALSE"
                else:
                    value = raw
            values[index] = value
        sparse_rows.append(values)

    matrix: list[list[str | Decimal | date | None]] = []
    for sparse in sparse_rows:
        matrix.append([sparse.get(index) for index in range(max_column + 1)])
    headers = [str(value) for value in matrix[0]]
    for row in matrix[1:]:
        for index, header in enumerate(headers):
            raw = row[index]
            if raw in (None, ""):
                row[index] = None
            elif header in DATE_COLUMNS:
                if "-" in str(raw):
                    row[index] = date.fromisoformat(str(raw)[:10])
                else:
                    row[index] = date(1899, 12, 30) + timedelta(days=int(Decimal(str(raw))))
            elif header in NUMERIC_COLUMNS:
                row[index] = Decimal(str(raw))
            else:
                row[index] = str(raw)
    return matrix


def _normalized_csv_matrix(relative_path: str) -> list[list[str | Decimal | date | None]]:
    with (ROOT / relative_path).open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        matrix = list(reader)
    headers = matrix[0]
    normalized: list[list[str | Decimal | date | None]] = [headers]
    for raw_row in matrix[1:]:
        row: list[str | Decimal | date | None] = []
        for header, raw in zip(headers, raw_row):
            if raw == "":
                row.append(None)
            elif header in DATE_COLUMNS:
                row.append(date.fromisoformat(raw))
            elif header in NUMERIC_COLUMNS:
                row.append(Decimal(raw))
            else:
                row.append(raw)
        normalized.append(row)
    return normalized


def _difference_string(value: object) -> str | int | None:
    if value is None or isinstance(value, int):
        return value
    return str(value)


def test_corpus_envelope_and_requested_scenario_coverage():
    corpus = _corpus()

    assert corpus["schema_version"] == "1.0.0"
    assert corpus["dataset_id"] == "fundops-private-markets-capital-call-v1"
    assert corpus["synthetic"] is True
    assert tuple(corpus["reconciliation_fields"]) == FIELDS
    assert set(corpus["status_values"]) == STATUSES
    assert set(corpus["severity_values"]) == SEVERITIES
    assert 20 <= len(corpus["cases"]) <= 30

    case_ids = [case["case_id"] for case in corpus["cases"]]
    assert len(case_ids) == len(set(case_ids))
    assert case_ids == [f"CC-{index:03d}" for index in range(1, len(case_ids) + 1)]

    covered = {scenario for case in corpus["cases"] for scenario in case["scenario_types"]}
    required = {
        "exact_match",
        "amount_mismatch",
        "currency_mismatch",
        "date_mismatch",
        "investor_naming_variation",
        "missing_commitment_amount",
        "duplicate_investor",
        "percentage_instead_of_amount",
        "comma_decimal_formatting",
        "parenthetical_negative_notation",
        "ocr_corruption",
        "conflicting_numbers",
        "stale_previous_call",
        "ambiguous_fund_name",
        "different_date_formats",
        "total_vs_remaining_commitment",
        "wrong_bank_reference",
        "document_missing_required_field",
        "wrong_investor_document",
        "duplicated_capital_call_notice",
    }
    assert required <= covered


def test_gold_labels_have_complete_fields_and_consistent_rollups():
    for case in _corpus()["cases"]:
        canonical = case["canonical_record"]
        extracted = case["expected_extraction"]["canonical_values"]
        expected = case["expected_reconciliation"]
        field_results = expected["field_results"]

        assert set(canonical) == {"case_id", *FIELDS}
        assert set(extracted) == set(FIELDS)
        assert set(field_results) == set(FIELDS)
        FundRecord.model_validate(canonical)

        for field, result in field_results.items():
            assert result["status"] in STATUSES
            assert result["severity"] in SEVERITIES
            assert result["expected"] == canonical[field]
            assert result["observed"] == extracted[field]
            if result["status"] == "PASS":
                assert result["severity"] == "NONE"
                assert result["exception_code"] is None
            else:
                assert result["severity"] != "NONE"
                assert result["exception_code"]

        statuses = [result["status"] for result in field_results.values()]
        assert expected["counts"] == {status: statuses.count(status) for status in ("PASS", "MISMATCH", "MISSING", "REVIEW")}
        exception_fields = [field for field in FIELDS if field_results[field]["status"] != "PASS"]
        exception_fields += [field for field, result in expected["additional_results"].items() if result["status"] != "PASS"]
        assert expected["exception_fields"] == exception_fields

        severities = [field_results[field]["severity"] for field in FIELDS if field_results[field]["status"] != "PASS"]
        severities += [result["severity"] for result in expected["additional_results"].values() if result["status"] != "PASS"]
        rolled_up = max(severities, key=SEVERITY_RANK.get) if severities else "NONE"
        assert expected["case_severity"] == rolled_up
        assert case["reviewer_label"]["requires_human_review"] is (expected["overall_status"] != "PASS")
        assert case["reviewer_label"]["recommended_action"] in {"AUTO_CLEAR", "INVESTIGATE", "HOLD_AND_INVESTIGATE"}
        assert case["reviewer_label"]["rationale"]

        null_reasons = case["expected_extraction"]["null_reasons"]
        assert set(null_reasons) == {field for field, value in extracted.items() if value is None}
        for field, reason in null_reasons.items():
            assert field in FIELDS
            assert extracted[field] is None
            assert reason in {"missing", "ambiguous", "conflicting", "not_applicable"}


def test_evidence_paths_hashes_and_line_locators_are_exact():
    document_ids: set[str] = set()
    evidence_ids: set[str] = set()
    documents_by_id: dict[str, dict] = {}

    for case in _corpus()["cases"]:
        for document in case["documents"]:
            assert document["document_id"] not in document_ids
            document_ids.add(document["document_id"])
            documents_by_id[document["document_id"]] = document
            path = ROOT / document["path"]
            assert path.is_file()
            assert path.resolve().is_relative_to(ROOT.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest() == document["sha256"]
            text = path.read_text(encoding="utf-8")
            assert "FICTIONAL - DEMO ONLY - DO NOT PAY" in text
            assert len(text.split("\f")) == document["pages"]

        evidence_groups = list(case["expected_extraction"]["field_evidence"].values())
        evidence_groups.append(case["expected_extraction"]["distractors"])
        for group in evidence_groups:
            for evidence in group:
                assert evidence["evidence_id"] not in evidence_ids
                evidence_ids.add(evidence["evidence_id"])
                assert evidence["document_id"] in documents_by_id
                source = ROOT / evidence["source_file"]
                pages = source.read_text(encoding="utf-8").split("\f")
                locator = evidence["locator"]
                line = pages[locator["page"] - 1].splitlines()[locator["line_start"] - 1]
                assert locator["line_start"] == locator["line_end"]
                assert line == evidence["raw_text"]

    for document in documents_by_id.values():
        duplicate_of = document.get("duplicate_of")
        if duplicate_of:
            assert duplicate_of in documents_by_id
            assert document["sha256"] == documents_by_id[duplicate_of]["sha256"]

    referenced_eval_notices = {
        document["path"]
        for document in documents_by_id.values()
        if document["path"].startswith("data/evals/notices/")
    }
    actual_eval_notices = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "data/evals/notices").glob("*.txt")
    }
    assert actual_eval_notices == referenced_eval_notices


def test_replayable_labels_match_the_current_deterministic_reconciler():
    for case in _corpus()["cases"]:
        if not case["replayable_against_current_reconciler"]:
            continue
        canonical = FundRecord.model_validate(case["canonical_record"])
        values = case["expected_extraction"]["canonical_values"]
        evidence = case["expected_extraction"]["field_evidence"]
        fields = {}
        for field, value in values.items():
            if value is None:
                continue
            evidence_item = evidence[field][0]
            fields[field] = ExtractedField(
                value=value,
                source=Path(evidence_item["source_file"]).name,
                page=evidence_item["locator"]["page"],
                confidence=1.0,
                evidence=evidence_item["raw_text"],
            )
        document = ExtractedDocument(
            case_id=case["case_id"],
            source_document=case["documents"][0]["path"],
            document_type=DocumentType.CAPITAL_CALL,
            fields=fields,
        )

        report = reconcile_document(canonical, document)
        gold = case["expected_reconciliation"]
        assert report.overall_status.value == gold["overall_status"], case["case_id"]
        assert report.counts == gold["counts"], case["case_id"]
        by_field = {result.field: result for result in report.results}
        for field, expected in gold["field_results"].items():
            actual = by_field[field]
            assert actual.status.value == expected["status"], (case["case_id"], field)
            assert actual.severity.value == expected["severity"], (case["case_id"], field)
            assert _difference_string(actual.difference) == expected["difference"], (case["case_id"], field)


def test_register_csv_and_xlsx_files_are_cell_equivalent():
    for csv_path in (
        "data/evals/investor_register.csv",
        "data/demo/northstar_growth_fund_ii/investor_register.csv",
    ):
        xlsx_path = csv_path.replace(".csv", ".xlsx")
        assert (ROOT / xlsx_path).is_file()
        assert _xlsx_matrix(xlsx_path) == _normalized_csv_matrix(csv_path)


def test_every_canonical_record_resolves_to_its_register_row():
    register_cache: dict[str, dict[str, dict[str, str]]] = {}
    for case in _corpus()["cases"]:
        reference = case["register_ref"]
        rows = register_cache.setdefault(
            reference["csv"],
            {row["record_id"]: row for row in _csv_rows(reference["csv"])},
        )
        assert reference["record_id"] in rows
        row = rows[reference["record_id"]]
        canonical = case["canonical_record"]
        for field in (
            "fund_name",
            "investor_name",
            "commitment_amount",
            "capital_call_amount",
            "call_date",
            "due_date",
            "currency",
            "bank_account_reference",
            "document_type",
        ):
            assert row[field] == canonical[field], (case["case_id"], field)
        assert (row["management_fee"] or None) == canonical["management_fee"]
        assert date.fromisoformat(row["call_date"]) <= date.fromisoformat(row["due_date"])
        assert Decimal(row["capital_call_amount"]) <= Decimal(row["commitment_amount"])
        if "call_exceeds_remaining_commitment" in case["scenario_types"]:
            assert Decimal(row["capital_call_amount"]) > Decimal(row["remaining_commitment_amount"])
        else:
            assert Decimal(row["capital_call_amount"]) <= Decimal(row["remaining_commitment_amount"])


def test_minimal_northstar_mvp_fixtures_have_exactly_two_intended_exceptions():
    canonical = FundRecord.model_validate_json((ROOT / "data/fund_record.json").read_text(encoding="utf-8"))
    assert canonical.investor_name == "Albion Capital Partners"
    assert canonical.commitment_amount == Decimal("5000000.00")
    assert canonical.capital_call_amount == Decimal("625000.00")
    assert canonical.due_date.isoformat() == "2026-09-18"

    extractor = DeterministicExtractor()
    for extension in ("txt", "pdf"):
        matching = extractor.extract(ROOT / f"data/sample_documents/matching_capital_call.{extension}", case_id=canonical.case_id)
        discrepancy = extractor.extract(ROOT / f"data/sample_documents/discrepancy_capital_call.{extension}", case_id=canonical.case_id)
        matching_report = reconcile_document(canonical, matching)
        discrepancy_report = reconcile_document(canonical, discrepancy)

        assert matching_report.overall_status is ReconciliationStatus.PASS
        assert matching_report.exceptions == []
        assert [(result.field, result.status, result.severity, result.difference) for result in discrepancy_report.exceptions] == [
            ("capital_call_amount", ReconciliationStatus.MISMATCH, Severity.HIGH, Decimal("25000.00")),
            ("due_date", ReconciliationStatus.MISMATCH, Severity.HIGH, 2),
        ]


def test_northstar_demo_package_is_complete_and_pdfs_are_text_searchable():
    demo_dir = ROOT / "data/demo/northstar_growth_fund_ii"
    required = {
        "README.md",
        "investor_register.csv",
        "investor_register.xlsx",
        "capital_call_notice.txt",
        "capital_call_notice.pdf",
        "expected_canonical_record.json",
        "gold_label.json",
    }
    assert required <= {path.name for path in demo_dir.iterdir() if path.is_file()}

    expected = json.loads((demo_dir / "expected_canonical_record.json").read_text(encoding="utf-8"))
    label = json.loads((demo_dir / "gold_label.json").read_text(encoding="utf-8"))
    corpus_case = next(case for case in _corpus()["cases"] if case["case_id"] == "CC-002")
    assert expected == corpus_case["canonical_record"]
    assert label == corpus_case
    assert len(_csv_rows("data/demo/northstar_growth_fund_ii/investor_register.csv")) == 8

    pdf_expectations = {
        "data/sample_documents/matching_capital_call.pdf": (1, "Albion Capital Partners", "GBP 625,000.00"),
        "data/sample_documents/discrepancy_capital_call.pdf": (1, "Albion Capital Partners", "GBP 650,000.00"),
        "data/demo/northstar_growth_fund_ii/capital_call_notice.pdf": (2, "Alderstone Civic Pension Partnership", "GBP 650,000.00"),
    }
    for relative_path, (page_count, investor, amount) in pdf_expectations.items():
        reader = PdfReader(ROOT / relative_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert len(reader.pages) == page_count
        assert "FICTIONAL - DEMO ONLY - DO NOT PAY" in text
        assert investor in text
        assert amount in text


def test_text_fixtures_exclude_payment_coordinates_and_personal_contact_data():
    forbidden = ("IBAN", "SWIFT", "ACCOUNT NUMBER", "ROUTING NUMBER", "SORT CODE", "@")
    for path in (ROOT / "data").rglob("*.txt"):
        text = path.read_text(encoding="utf-8")
        assert "FICTIONAL - DEMO ONLY - DO NOT PAY" in text
        upper = text.upper()
        assert all(token not in upper for token in forbidden), path
