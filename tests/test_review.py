from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from app.models import (
    DocumentType,
    ExtractedField,
    ExtractionMethod,
    ReconciliationItem,
    ReconciliationReport,
    ReconciliationStatus,
    Severity,
)
from app.review import (
    DeterministicEvidenceReviewer,
    OpenAICompatibleEvidenceReviewer,
    ReviewStatus,
    review_item,
    review_reconciliation,
)


def _item(
    *,
    field: str = "capital_call_amount",
    expected=Decimal("125000"),
    observed=Decimal("125000"),
    evidence="Capital Call Amount: GBP 125,000",
    status: ReconciliationStatus = ReconciliationStatus.PASS,
    source: str = "capital_call_notice.pdf",
    page: int = 2,
) -> ReconciliationItem:
    return ReconciliationItem(
        field=field,
        expected=expected,
        observed=observed,
        status=status,
        severity=(Severity.NONE if status is ReconciliationStatus.PASS else Severity.HIGH),
        difference=(
            observed - expected
            if isinstance(expected, Decimal) and isinstance(observed, Decimal)
            else None
        ),
        explanation="Deterministic control result for this field",
        provenance=ExtractedField(
            value=observed,
            source=source,
            page=page,
            confidence=0.96,
            evidence=evidence,
            method=ExtractionMethod.DETERMINISTIC,
        ),
    )


def _report(*items: ReconciliationItem) -> ReconciliationReport:
    counts = {status.value: 0 for status in ReconciliationStatus}
    for item in items:
        counts[item.status.value] += 1
    if counts[ReconciliationStatus.MISMATCH.value]:
        overall = ReconciliationStatus.MISMATCH
    elif counts[ReconciliationStatus.MISSING.value]:
        overall = ReconciliationStatus.MISSING
    elif counts[ReconciliationStatus.REVIEW.value]:
        overall = ReconciliationStatus.REVIEW
    else:
        overall = ReconciliationStatus.PASS
    return ReconciliationReport(
        case_id="case-review-1",
        source_document="capital_call_notice.pdf",
        document_type=DocumentType.CAPITAL_CALL,
        overall_status=overall,
        results=list(items),
        counts=counts,
    )


def test_correct_evidence_is_supported_without_overwriting_reconciliation():
    # The document genuinely says 125,000 even though the fund book says 100,000.
    # Evidence review must remain independent of deterministic reconciliation.
    item = _item(
        expected=Decimal("100000"),
        observed=Decimal("125000"),
        evidence="Capital Call Amount: GBP 125,000",
        status=ReconciliationStatus.MISMATCH,
    )
    report = _report(item)
    original = report.model_dump(mode="json")

    reviewed = review_reconciliation(report)

    finding = reviewed.finding_for("capital_call_amount")
    assert finding is not None
    assert finding.status is ReviewStatus.SUPPORTED
    assert finding.challenged_value is None
    assert finding.confidence == 1.0
    # The reconciliation break still needs a human even when evidence supports it.
    assert finding.requires_human_review is True
    assert finding.reconciliation_status is ReconciliationStatus.MISMATCH
    assert finding.source_references[0].source == "capital_call_notice.pdf"
    assert finding.source_references[0].page == 2
    assert report.model_dump(mode="json") == original


def test_optional_null_pass_does_not_require_source_evidence_or_human_review():
    item = ReconciliationItem(
        field="management_fee",
        expected=None,
        observed=None,
        status=ReconciliationStatus.PASS,
        severity=Severity.NONE,
        explanation="No value is expected and none is present in the document",
        provenance=None,
    )

    finding = review_reconciliation(_report(item)).findings[0]

    assert finding.status is ReviewStatus.SUPPORTED
    assert finding.confidence is None
    assert finding.requires_human_review is False
    assert "not applicable" in finding.review_reason.casefold()


def test_required_missing_value_still_requires_evidence_and_human_review():
    item = ReconciliationItem(
        field="management_fee",
        expected=Decimal("25000"),
        observed=None,
        status=ReconciliationStatus.MISSING,
        severity=Severity.HIGH,
        explanation="Expected value is missing from the document",
        provenance=None,
    )

    finding = review_reconciliation(_report(item)).findings[0]

    assert finding.status is ReviewStatus.INSUFFICIENT_EVIDENCE
    assert finding.requires_human_review is True


def test_unsupported_extraction_creates_grounded_challenge():
    # Reconciliation says PASS, but the independent evidence says 150,000.
    item = _item(
        expected=Decimal("125000"),
        observed=Decimal("125000"),
        evidence="Capital Call Amount: GBP 150,000",
        status=ReconciliationStatus.PASS,
    )

    finding = review_reconciliation(_report(item)).findings[0]

    assert finding.status is ReviewStatus.CHALLENGE
    assert finding.challenged_value == Decimal("150000")
    assert finding.requires_human_review is True
    assert finding.review_reason


def test_terminal_sentence_period_does_not_truncate_money_evidence():
    items = [
        _item(
            expected=Decimal("125000"),
            observed=Decimal("125000"),
            evidence="Capital Call Amount: GBP 125,000.",
        ),
        _item(
            field="management_fee",
            expected=Decimal("125000.50"),
            observed=Decimal("125000.50"),
            evidence="Management Fee: GBP 125,000.50.",
        ),
    ]

    reviewed = review_reconciliation(_report(*items))

    assert [finding.status for finding in reviewed.findings] == [
        ReviewStatus.SUPPORTED,
        ReviewStatus.SUPPORTED,
    ]


def test_dates_and_percentages_are_not_competing_money_values():
    item = _item(
        expected=Decimal("125000"),
        observed=Decimal("125000"),
        evidence=(
            "Capital Call Amount: GBP 125,000 payable by 20 September 2026 "
            "(12.5% of commitment)."
        ),
    )

    finding = review_reconciliation(_report(item)).findings[0]

    assert finding.status is ReviewStatus.SUPPORTED
    assert finding.challenged_value is None


def test_locale_ambiguous_numeric_date_is_challenged():
    item = _item(
        field="due_date",
        expected=date(2026, 10, 9),
        observed=date(2026, 10, 9),
        evidence="Due Date: 09/10/2026",
    )

    finding = review_reconciliation(_report(item)).findings[0]

    assert finding.status is ReviewStatus.CHALLENGE
    assert finding.challenged_value == date(2026, 9, 10)
    assert finding.requires_human_review is True


def test_ambiguous_evidence_challenges_with_direct_competing_value():
    item = _item(
        expected=Decimal("100000"),
        observed=Decimal("100000"),
        evidence=(
            "Capital Call Amount: GBP 100,000 or GBP 98,500 depending on "
            "the applicable share class"
        ),
    )

    finding = review_reconciliation(_report(item)).findings[0]

    assert finding.status is ReviewStatus.CHALLENGE
    assert finding.challenged_value == Decimal("98500")
    assert finding.confidence is None
    assert finding.requires_human_review is True
    assert "multiple" in finding.review_reason.casefold()


def test_explicit_negation_never_supports_the_extracted_value():
    cases = [
        _item(evidence="Capital Call Amount: not GBP 125,000"),
        _item(
            field="currency",
            expected="GBP",
            observed="GBP",
            evidence="Currency: not GBP",
        ),
        _item(
            field="document_type",
            expected="CAPITAL_CALL",
            observed="CAPITAL_CALL",
            evidence="Document Type: This is not a capital call",
        ),
    ]

    reviewed = review_reconciliation(_report(*cases))

    assert [finding.status for finding in reviewed.findings] == [
        ReviewStatus.CHALLENGE,
        ReviewStatus.CHALLENGE,
        ReviewStatus.CHALLENGE,
    ]
    assert all(finding.requires_human_review for finding in reviewed.findings)


def test_missing_evidence_is_insufficient_and_preserves_source_pointer():
    item = _item(evidence=None)

    finding = review_reconciliation(_report(item)).findings[0]

    assert finding.status is ReviewStatus.INSUFFICIENT_EVIDENCE
    assert finding.challenged_value is None
    assert finding.confidence is None
    assert finding.requires_human_review is True
    assert finding.source_references[0].model_dump() == {
        "source": "capital_call_notice.pdf",
        "page": 2,
        "evidence": None,
    }


def test_model_unavailable_fails_closed_as_not_reviewed():
    def unavailable_transport(_prompt: str):
        raise ConnectionError("provider offline")

    reviewer = OpenAICompatibleEvidenceReviewer(transport=unavailable_transport)
    finding = review_reconciliation(_report(_item()), reviewer=reviewer).findings[0]

    assert finding.status is ReviewStatus.NOT_REVIEWED
    assert finding.challenged_value is None
    assert finding.confidence is None
    assert finding.requires_human_review is True
    assert "no independent model review" in finding.review_reason.casefold()
    assert finding.source_references[0].source == "capital_call_notice.pdf"


def test_model_cannot_support_missing_evidence():
    calls = []

    def misleading_transport(prompt: str):
        calls.append(prompt)
        return {
            "status": "SUPPORTED",
            "review_reason": "Unsupported assertion",
            "challenged_value": None,
            "confidence": 1.0,
        }

    reviewer = OpenAICompatibleEvidenceReviewer(transport=misleading_transport)
    finding = review_reconciliation(
        _report(_item(evidence=None)), reviewer=reviewer
    ).findings[0]

    assert finding.status is ReviewStatus.INSUFFICIENT_EVIDENCE
    assert finding.requires_human_review is True
    assert calls == []


def test_model_cannot_support_irrelevant_evidence():
    reviewer = OpenAICompatibleEvidenceReviewer(
        transport=lambda _prompt: {
            "status": "SUPPORTED",
            "review_reason": "The extraction is supported.",
            "challenged_value": None,
            "confidence": 0.99,
        }
    )
    item = _item(evidence="Investor: A different limited partner")

    finding = review_reconciliation(_report(item), reviewer=reviewer).findings[0]

    assert finding.status is ReviewStatus.INSUFFICIENT_EVIDENCE
    assert finding.confidence is None
    assert finding.requires_human_review is True
    assert "not accepted" in finding.review_reason.casefold()


def test_model_cannot_ground_same_value_from_the_wrong_field():
    reviewer = OpenAICompatibleEvidenceReviewer(
        transport=lambda _prompt: {
            "status": "SUPPORTED",
            "review_reason": "The value appears in the snippet.",
            "challenged_value": None,
            "confidence": 0.99,
        }
    )
    item = _item(evidence="Total Commitment: GBP 125,000")

    finding = review_reconciliation(_report(item), reviewer=reviewer).findings[0]

    assert finding.status is ReviewStatus.INSUFFICIENT_EVIDENCE
    assert finding.requires_human_review is True


def test_model_cannot_suppress_ambiguous_or_negated_evidence():
    reviewer = OpenAICompatibleEvidenceReviewer(
        transport=lambda _prompt: {
            "status": "SUPPORTED",
            "review_reason": "The extraction is supported.",
            "challenged_value": None,
            "confidence": 0.99,
        }
    )
    ambiguous = _item(
        expected=Decimal("100000"),
        observed=Decimal("100000"),
        evidence="Capital Call Amount: GBP 100,000 or GBP 200,000",
    )
    negated = _item(evidence="Capital Call Amount: not GBP 125,000")

    reviewed = review_reconciliation(
        _report(ambiguous, negated), reviewer=reviewer
    )

    assert [finding.status for finding in reviewed.findings] == [
        ReviewStatus.CHALLENGE,
        ReviewStatus.CHALLENGE,
    ]
    assert reviewed.findings[0].challenged_value == Decimal("200000")
    assert all(finding.requires_human_review for finding in reviewed.findings)


def test_model_response_schema_is_strictly_required_but_nullable():
    schema = OpenAICompatibleEvidenceReviewer.response_schema()

    assert set(schema["required"]) == set(schema["properties"])
    assert {"type": "null"} in schema["properties"]["confidence"]["anyOf"]
    assert {"type": "null"} in schema["properties"]["challenged_value"]["anyOf"]


def test_model_receives_only_one_field_at_a_time_as_structured_json():
    captured = []

    def transport(prompt: str):
        marker = "REVIEW_INPUT_JSON:\n"
        assert marker in prompt
        captured.append((prompt, json.loads(prompt.split(marker, 1)[1])))
        return {
            "status": "SUPPORTED",
            "review_reason": "The supplied field evidence supports the value.",
            "challenged_value": None,
            "confidence": 0.91,
        }

    amount = _item(evidence="Capital Call Amount: GBP 125,000")
    investor = _item(
        field="investor_name",
        expected="PRIVATE SIBLING SENTINEL",
        observed="PRIVATE SIBLING SENTINEL",
        evidence="Investor: PRIVATE SIBLING SENTINEL",
        page=3,
    )
    reviewer = OpenAICompatibleEvidenceReviewer(transport=transport)

    reviewed = review_reconciliation(_report(amount, investor), reviewer=reviewer)

    assert [finding.status for finding in reviewed.findings] == [
        ReviewStatus.SUPPORTED,
        ReviewStatus.SUPPORTED,
    ]
    assert len(captured) == 2
    first_prompt, first_payload = captured[0]
    assert set(first_payload) == {
        "field",
        "extracted_value",
        "source_evidence",
        "provenance",
        "reconciliation",
    }
    assert first_payload["field"] == "capital_call_amount"
    assert "case-review-1" not in first_prompt
    assert "PRIVATE SIBLING SENTINEL" not in first_prompt
    assert "results" not in first_payload
    assert "fields" not in first_payload


def test_invalid_model_output_is_not_trusted():
    reviewer = OpenAICompatibleEvidenceReviewer(
        transport=lambda _prompt: {
            "status": "SUPPORTED",
            "review_reason": "Looks fine",
            "challenged_value": "GBP 999,999",
            "confidence": 4.2,
        }
    )

    finding = review_reconciliation(_report(_item()), reviewer=reviewer).findings[0]

    assert finding.status is ReviewStatus.NOT_REVIEWED
    assert finding.confidence is None
    assert finding.requires_human_review is True


def test_injected_model_transport_may_omit_unavailable_optional_values():
    reviewer = OpenAICompatibleEvidenceReviewer(
        transport=lambda _prompt: {
            "status": "SUPPORTED",
            "review_reason": "The evidence directly supports the value.",
        }
    )

    finding = review_reconciliation(_report(_item()), reviewer=reviewer).findings[0]

    assert finding.status is ReviewStatus.SUPPORTED
    assert finding.challenged_value is None
    assert finding.confidence is None


def test_model_challenged_value_must_be_present_in_supplied_evidence():
    reviewer = OpenAICompatibleEvidenceReviewer(
        transport=lambda _prompt: {
            "status": "CHALLENGE",
            "review_reason": "A different amount may apply.",
            "challenged_value": "999999",
            "confidence": 0.7,
        }
    )
    item = _item(evidence="Capital Call Amount: GBP 150,000")

    finding = review_reconciliation(_report(item), reviewer=reviewer).findings[0]

    assert finding.status is ReviewStatus.CHALLENGE
    assert finding.challenged_value is None
    assert finding.source_references[0].evidence == "Capital Call Amount: GBP 150,000"


def test_model_challenge_cannot_attach_value_from_the_wrong_field():
    reviewer = OpenAICompatibleEvidenceReviewer(
        transport=lambda _prompt: {
            "status": "CHALLENGE",
            "review_reason": "A different amount appears.",
            "challenged_value": "200000",
            "confidence": 0.8,
        }
    )
    item = _item(evidence="Total Commitment: GBP 200,000")

    finding = review_reconciliation(_report(item), reviewer=reviewer).findings[0]

    assert finding.status is ReviewStatus.CHALLENGE
    assert finding.challenged_value is None
    assert finding.requires_human_review is True


def test_offline_reviewer_supports_text_date_currency_and_document_type():
    cases = [
        _item(
            field="fund_name",
            expected="Northstar Growth Fund II",
            observed="Northstar Growth Fund II",
            evidence="Fund Name: Northstar Growth Fund II",
        ),
        _item(
            field="due_date",
            expected="2026-09-20",
            observed="2026-09-20",
            evidence="Payment Due Date: 20 September 2026",
        ),
        _item(
            field="currency",
            expected="GBP",
            observed="GBP",
            evidence="Currency: GBP",
        ),
        _item(
            field="document_type",
            expected="CAPITAL_CALL",
            observed="CAPITAL_CALL",
            evidence="CAPITAL CALL NOTICE",
        ),
    ]

    reviewed = review_reconciliation(
        _report(*cases), reviewer=DeterministicEvidenceReviewer()
    )

    assert {finding.status for finding in reviewed.findings} == {
        ReviewStatus.SUPPORTED
    }
    assert reviewed.escalations == []
    assert reviewed.counts == {
        "SUPPORTED": 4,
        "CHALLENGE": 0,
        "INSUFFICIENT_EVIDENCE": 0,
        "NOT_REVIEWED": 0,
    }
    assert {status.value for status in ReviewStatus} == {
        "SUPPORTED",
        "CHALLENGE",
        "INSUFFICIENT_EVIDENCE",
        "NOT_REVIEWED",
    }


def test_review_item_accepts_serialized_contracts_without_mutation():
    item = _item()
    report = _report(item)
    report_data = report.model_dump(mode="json")
    item_data = item.model_dump(mode="json")

    finding = review_item(
        report_data,
        item_data,
        DeterministicEvidenceReviewer(),
    )

    assert finding.status is ReviewStatus.SUPPORTED
    assert report.model_dump(mode="json") == report_data
    assert item.model_dump(mode="json") == item_data
