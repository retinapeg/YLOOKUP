from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

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
    assert document.fields["due_date"].source_type == "text/plain"
    assert document.fields["due_date"].extractor == "deterministic-label-parser-v1"
    assert document.fields["due_date"].timestamp is None
    assert len(document.fields) == 10


def test_money_extraction_preserves_accounting_and_explicit_negative_signs():
    accounting = extract_text(
        "Capital Call Amount: (GBP 125,000.00)",
        source="accounting-negative.txt",
    )
    explicit = extract_text(
        "Capital Call Amount: -GBP 125,000.00",
        source="explicit-negative.txt",
    )

    assert accounting.fields["capital_call_amount"].value == Decimal("-125000.00")
    assert explicit.fields["capital_call_amount"].value == Decimal("-125000.00")
    assert (
        accounting.fields["capital_call_amount"].evidence
        == "Capital Call Amount: (GBP 125,000.00)"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EUR 10.000.000,00", Decimal("10000000.00")),
        ("EUR 625,00", Decimal("625.00")),
        ("(EUR 10.000.000,00)", Decimal("-10000000.00")),
    ],
)
def test_money_extraction_uses_canonical_eu_and_accounting_normalization(
    raw,
    expected,
):
    document = extract_text(
        f"Capital Call Amount: {raw}",
        source="eu-notice.txt",
    )

    assert document.fields["capital_call_amount"].value == expected
    assert document.fields["currency"].value == "EUR"


def test_ambiguous_labelled_money_is_an_explicit_abstention():
    document = extract_text(
        "Capital Call Amount: GBP 0,125",
        source="ambiguous-notice.txt",
    )

    field = document.fields["capital_call_amount"]
    assert field.value is None
    assert field.confidence == 0.0
    assert field.abstention_reason is not None
    assert "ambiguous" in field.abstention_reason
    assert any(
        "abstained from capital_call_amount" in item
        for item in document.warnings
    )


def test_unsupported_labelled_currency_is_an_explicit_abstention():
    document = extract_text(
        "Currency: pounds sterling",
        source="unsupported-currency.txt",
    )

    field = document.fields["currency"]
    assert field.value is None
    assert field.abstention_reason is not None
    assert "three-letter code" in field.abstention_reason
    assert field.evidence == "Currency: pounds sterling"


def test_conflicting_repeated_labels_across_pages_abstain():
    document = extract_text(
        "Capital Call Amount: GBP 625,000.00"
        "\fCapital Call Amount: GBP 650,000.00",
        source="conflicting-notice.txt",
    )

    field = document.fields["capital_call_amount"]
    assert field.value is None
    assert field.page == 1
    assert field.evidence == "Capital Call Amount: GBP 625,000.00"
    assert (
        field.abstention_reason
        == "conflicting labelled values across pages 1 and 2"
    )
    assert field.method is ExtractionMethod.DETERMINISTIC


def test_repeated_labels_with_the_same_normalized_value_are_accepted():
    document = extract_text(
        "Commitment Amount: EUR 10.000.000,00"
        "\fCommitment Amount: EUR 10,000,000.00",
        source="consistent-notice.txt",
    )

    field = document.fields["commitment_amount"]
    assert field.value == Decimal("10000000.00")
    assert field.abstention_reason is None
    assert field.page == 1


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
    assert document.fields["capital_call_amount"].source_type == "text/plain"
    assert (
        document.fields["capital_call_amount"].extractor
        == "deterministic-label-parser-v1"
    )
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
    assert document.fields["fund_name"].source_type == "text/plain"
    assert (
        document.fields["fund_name"].extractor
        == "openai-compatible-structured-extractor:gpt-4.1-mini"
    )
    assert document.fields["due_date"].method is ExtractionMethod.DETERMINISTIC
    assert any("partial" in warning.casefold() for warning in document.warnings)


def test_partial_ai_extraction_from_pdf_sidecar_keeps_field_level_truth(tmp_path):
    pdf_path = tmp_path / "notice.pdf"
    pdf_path.write_bytes(b"not a valid PDF")
    pdf_path.with_suffix(".txt").write_text(NOTICE, encoding="utf-8")
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

    document = extractor.extract(pdf_path, case_id="northstar-call-04")

    model_field = document.fields["fund_name"]
    fallback_field = document.fields["due_date"]
    assert document.source_document == "notice.pdf"
    assert model_field.source == "notice.txt"
    assert model_field.source_type == "text/plain"
    assert model_field.method is ExtractionMethod.OPENAI_COMPATIBLE
    assert fallback_field.source == "notice.txt"
    assert fallback_field.source_type == "text/plain"
    assert fallback_field.method is ExtractionMethod.FALLBACK


@pytest.mark.parametrize(
    "invalid_details",
    [
        {
            "value": "GBP 625,000",
            "confidence": 0.91,
            "evidence": "Capital Call Amount: GBP 625,000.00",
        },
        {
            "value": "GBP 625,000",
            "page": 1,
            "evidence": "Capital Call Amount: GBP 625,000.00",
        },
        {
            "value": "GBP 625,000",
            "page": 0,
            "confidence": 0.91,
            "evidence": "Capital Call Amount: GBP 625,000.00",
        },
        {
            "value": "GBP 625,000",
            "page": True,
            "confidence": 0.91,
            "evidence": "Capital Call Amount: GBP 625,000.00",
        },
        {
            "value": "GBP 625,000",
            "page": 1,
            "confidence": float("nan"),
            "evidence": "Capital Call Amount: GBP 625,000.00",
        },
        {
            "value": "GBP 625,000",
            "page": 1,
            "confidence": 1.01,
            "evidence": "Capital Call Amount: GBP 625,000.00",
        },
    ],
    ids=(
        "missing-page",
        "missing-confidence",
        "zero-page",
        "boolean-page",
        "non-finite-confidence",
        "out-of-range-confidence",
    ),
)
def test_ai_fields_require_explicit_valid_page_and_confidence(
    tmp_path,
    invalid_details,
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
                },
                "capital_call_amount": invalid_details,
            }
        }
    )

    document = extractor.extract(notice, case_id="northstar-call-04")

    assert document.extraction_method is ExtractionMethod.OPENAI_COMPATIBLE
    assert document.fields["fund_name"].method is ExtractionMethod.OPENAI_COMPATIBLE
    assert document.fields["capital_call_amount"].method is ExtractionMethod.DETERMINISTIC
    assert any(
        "invalid or ungrounded AI value for capital_call_amount" in warning
        for warning in document.warnings
    )


def test_ai_evidence_grounding_normalizes_case_and_whitespace(tmp_path):
    notice = tmp_path / "notice.txt"
    notice.write_text(NOTICE, encoding="utf-8")
    model_evidence = "fund name:  NORTHSTAR   GROWTH FUND II"
    extractor = OpenAICompatibleExtractor(
        transport=lambda _prompt: {
            "fields": {
                "fund_name": {
                    "value": "Northstar Growth Fund II",
                    "page": 1,
                    "confidence": 0.91,
                    "evidence": model_evidence,
                }
            }
        }
    )

    document = extractor.extract(notice, case_id="northstar-call-04")

    assert document.fields["fund_name"].method is ExtractionMethod.OPENAI_COMPATIBLE
    assert document.fields["fund_name"].page == 1
    assert document.fields["fund_name"].evidence == model_evidence


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


def test_ai_cannot_override_a_deterministic_cross_page_conflict(tmp_path):
    notice = tmp_path / "conflict.txt"
    notice.write_text(
        "Fund Name: Northstar Growth Fund II\n"
        "Capital Call Amount: GBP 625,000.00\f"
        "Capital Call Amount: GBP 650,000.00",
        encoding="utf-8",
    )
    extractor = OpenAICompatibleExtractor(
        transport=lambda _prompt: {
            "fields": {
                "fund_name": {
                    "value": "Northstar Growth Fund II",
                    "page": 1,
                    "confidence": 0.91,
                    "evidence": "Fund Name: Northstar Growth Fund II",
                },
                "capital_call_amount": {
                    "value": "GBP 650,000.00",
                    "page": 2,
                    "confidence": 0.99,
                    "evidence": "Capital Call Amount: GBP 650,000.00",
                },
            }
        }
    )

    document = extractor.extract(notice)

    amount = document.fields["capital_call_amount"]
    assert document.extraction_method is ExtractionMethod.OPENAI_COMPATIBLE
    assert document.fields["fund_name"].method is ExtractionMethod.OPENAI_COMPATIBLE
    assert amount.value is None
    assert amount.method is ExtractionMethod.DETERMINISTIC
    assert amount.abstention_reason == "conflicting labelled values across pages 1 and 2"
    assert any(
        "retained deterministic extraction abstention" in warning
        for warning in document.warnings
    )


def test_ambiguous_ai_amount_keeps_the_deterministic_value(tmp_path):
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
                },
                "capital_call_amount": {
                    "value": "GBP 0,125",
                    "page": 1,
                    "confidence": 0.99,
                    "evidence": "Capital Call Amount: GBP 625,000.00",
                },
            }
        }
    )

    document = extractor.extract(notice)

    amount = document.fields["capital_call_amount"]
    assert amount.value == Decimal("625000.00")
    assert amount.method is ExtractionMethod.DETERMINISTIC
    assert any(
        "invalid or ungrounded AI value for capital_call_amount" in warning
        for warning in document.warnings
    )


def test_model_mode_without_a_key_returns_a_disclosed_offline_fallback(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    notice = tmp_path / "notice.txt"
    notice.write_text(NOTICE, encoding="utf-8")

    document = OpenAICompatibleExtractor().extract(notice)

    amount = document.fields["capital_call_amount"]
    assert document.extraction_method is ExtractionMethod.FALLBACK
    assert amount.value == Decimal("625000.00")
    assert amount.method is ExtractionMethod.DETERMINISTIC
    assert amount.extractor == "deterministic-label-parser-v1"
    assert any("unavailable" in warning for warning in document.warnings)
