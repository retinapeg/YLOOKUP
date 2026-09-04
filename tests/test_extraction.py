from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extraction import (
    DeterministicExtractor,
    OpenAICompatibleExtractor,
    extract_text,
)
from app.models import DocumentType, ExtractionMethod


NOTICE = """CAPITAL CALL NOTICE
Document Type: Capital Call
Fund Name: Northstar Growth Fund II
Investor Name: Albion Capital Partners
Commitment Amount: GBP 5,000,000.00
Capital Call Amount: GBP 625,000.00
Call Date: 04 September 2026
Currency: GBP
Bank Account Reference: NSGF2-ALBION-4471
Management Fee: GBP 75,000.00
\fDue Date: 18 September 2026
"""


def test_deterministic_text_extraction_preserves_types_and_provenance():
    document = extract_text(
        NOTICE,
        source="capital_call.txt",
        case_id="northstar-call-04",
    )

    assert document.document_type is DocumentType.CAPITAL_CALL
    assert document.fields["commitment_amount"].value == Decimal("5000000.00")
    assert document.fields["due_date"].value == date(2026, 9, 18)
    assert document.fields["due_date"].page == 2
    assert document.fields["due_date"].evidence == "Due Date: 18 September 2026"
    assert len(document.fields) == 10


def test_pdf_sidecar_fallback_is_disclosed_and_truthful(tmp_path):
    pdf_path = tmp_path / "notice.pdf"
    pdf_path.write_bytes(b"not a valid PDF")
    pdf_path.with_suffix(".txt").write_text(NOTICE, encoding="utf-8")

    document = DeterministicExtractor().extract(
        pdf_path, case_id="northstar-call-04"
    )

    assert document.source_document == "notice.pdf"
    assert document.extraction_method is ExtractionMethod.FALLBACK
    assert document.fields["capital_call_amount"].source == "notice.txt"
    assert document.fields["capital_call_amount"].method is ExtractionMethod.FALLBACK
    assert any("fallback" in warning.casefold() for warning in document.warnings)


def test_unknown_file_type_is_rejected(tmp_path):
    path = tmp_path / "notice.docx"
    path.write_bytes(b"unused")

    try:
        DeterministicExtractor().extract(path)
    except ValueError as exc:
        assert ".txt and .pdf" in str(exc)
    else:
        raise AssertionError("unsupported document should fail")


def test_partial_ai_extraction_keeps_grounded_value_and_deterministic_baseline(
    tmp_path,
):
    notice = tmp_path / "notice.txt"
    notice.write_text(NOTICE, encoding="utf-8")
    extractor = OpenAICompatibleExtractor(
        transport=lambda _prompt: {
            "fields": {
                "fund_name": {
                    "value": "Northstar Growth Fund II",
                    "page": 1,
                    "confidence": 0.91,
                    "evidence": "Fund Name: Northstar Growth Fund II",
                }
            }
        }
    )

    document = extractor.extract(notice, case_id="northstar-call-04")

    assert len(document.fields) == 10
    assert document.extraction_method is ExtractionMethod.OPENAI_COMPATIBLE
    assert document.fields["fund_name"].method is ExtractionMethod.OPENAI_COMPATIBLE
    assert document.fields["due_date"].method is ExtractionMethod.DETERMINISTIC
    assert any("partial" in warning.casefold() for warning in document.warnings)


def test_ungrounded_ai_provenance_is_rejected_in_favor_of_offline_result(tmp_path):
    notice = tmp_path / "notice.txt"
    notice.write_text(NOTICE, encoding="utf-8")
    extractor = OpenAICompatibleExtractor(
        transport=lambda _prompt: {
            "fields": {
                "capital_call_amount": {
                    "value": "GBP 999,999",
                    "page": 999,
                    "confidence": 1.0,
                    "evidence": "Capital Call Amount: GBP 999,999",
                }
            }
        }
    )

    document = extractor.extract(notice, case_id="northstar-call-04")

    assert document.extraction_method is ExtractionMethod.FALLBACK
    assert document.fields["capital_call_amount"].value == Decimal("625000.00")
    assert document.fields["capital_call_amount"].method is ExtractionMethod.DETERMINISTIC
    assert any("AI extraction failed" in warning for warning in document.warnings)
