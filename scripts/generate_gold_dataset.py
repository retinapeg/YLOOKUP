"""Generate the synthetic capital-call reconciliation corpus.

The generator owns fixture content only. It deliberately does not import or
reimplement the application's extraction or reconciliation engine.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data/gold/capital_call_reconciliation.json"
EVAL_REGISTER_CSV = ROOT / "data/evals/investor_register.csv"
DEMO_DIR = ROOT / "data/demo/northstar_growth_fund_ii"
DEMO_REGISTER_CSV = DEMO_DIR / "investor_register.csv"

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
NUMERIC_FIELDS = {"commitment_amount", "capital_call_amount", "management_fee"}
DATE_FIELDS = {"call_date", "due_date"}
SEVERITY_BY_FIELD = {
    "fund_name": "HIGH",
    "investor_name": "HIGH",
    "commitment_amount": "HIGH",
    "capital_call_amount": "HIGH",
    "call_date": "MEDIUM",
    "due_date": "HIGH",
    "currency": "HIGH",
    "bank_account_reference": "MEDIUM",
    "management_fee": "MEDIUM",
    "document_type": "MEDIUM",
}
SEVERITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
MARKER = "FICTIONAL - DEMO ONLY - DO NOT PAY"


def _canonical(
    case_id: str,
    fund: str,
    investor: str,
    commitment: str,
    call_amount: str,
    due_date: str,
    *,
    call_date: str = "2026-09-04",
    currency: str = "GBP",
    bank_reference: str | None = None,
    management_fee: str | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "fund_name": fund,
        "investor_name": investor,
        "commitment_amount": commitment,
        "capital_call_amount": call_amount,
        "call_date": call_date,
        "due_date": due_date,
        "currency": currency,
        "bank_account_reference": bank_reference or f"FOP-{case_id}-CURRENT",
        "management_fee": management_fee,
        "document_type": "CAPITAL_CALL",
    }


def _spec(
    case_id: str,
    title: str,
    description: str,
    scenario_types: list[str],
    canonical: dict[str, Any],
    *,
    difficulty: str = "STANDARD",
    observed_overrides: dict[str, Any] | None = None,
    missing_fields: list[str] | None = None,
    raw_overrides: dict[str, Any] | None = None,
    status_overrides: dict[str, dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    distractors: list[dict[str, Any]] | None = None,
    candidates: dict[str, list[dict[str, Any]]] | None = None,
    null_reasons: dict[str, str] | None = None,
    exception_codes: list[str] | None = None,
    replayable: bool = True,
    register_overrides: dict[str, Any] | None = None,
    extra_register_rows: list[dict[str, Any]] | None = None,
    additional_results: dict[str, dict[str, Any]] | None = None,
    overall_override: str | None = None,
    document_mode: str = "STANDARD",
    extra_lines: dict[int, list[str]] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "title": title,
        "description": description,
        "scenario_types": scenario_types,
        "difficulty": difficulty,
        "canonical": canonical,
        "observed_overrides": observed_overrides or {},
        "missing_fields": missing_fields or [],
        "raw_overrides": raw_overrides or {},
        "status_overrides": status_overrides or {},
        "warnings": warnings or [],
        "distractors": distractors or [],
        "candidates": candidates or {},
        "null_reasons": null_reasons or {},
        "exception_codes": exception_codes or [],
        "replayable": replayable,
        "register_overrides": register_overrides or {},
        "extra_register_rows": extra_register_rows or [],
        "additional_results": additional_results or {},
        "overall_override": overall_override,
        "document_mode": document_mode,
        "extra_lines": extra_lines or {},
    }


def _cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    cases.append(
        _spec(
            "CC-001",
            "Exact match control",
            "The notice matches the Northstar administrator record across all ten canonical fields.",
            ["exact_match", "clean_control"],
            _canonical(
                "CC-001",
                "Northstar Growth Fund II",
                "Albion Capital Partners",
                "5000000.00",
                "625000.00",
                "2026-09-18",
                bank_reference="NSGFII-ALB-CC04",
                management_fee="25000.00",
            ),
            difficulty="CONTROL",
            document_mode="SAMPLE_MATCHING",
        )
    )
    cases.append(
        _spec(
            "CC-002",
            "Northstar flagship multi-field exception",
            "A polished two-page notice overstates Call 04 by GBP 25,000 and advances the due date by two days; a settled prior-call amount is retained as stale context.",
            ["amount_mismatch", "date_mismatch", "stale_previous_call", "polished_demo"],
            _canonical(
                "CC-002",
                "Northstar Growth Fund II",
                "Alderstone Civic Pension Partnership",
                "25000000.00",
                "625000.00",
                "2026-09-30",
                bank_reference="NSGFII-ALD-CC04",
                management_fee="25000.00",
            ),
            observed_overrides={"capital_call_amount": "650000.00", "due_date": "2026-09-28"},
            raw_overrides={
                "commitment_amount": "Commitment Amount: GBP 25,000,000.00",
                "capital_call_amount": "Capital Call Amount: GBP 650,000.00",
                "management_fee": "Management Fee: GBP 25,000.00",
                "due_date": "Due Date: 2026-09-28",
            },
            distractors=[
                {
                    "field": "capital_call_amount",
                    "raw_text": "Call 03 - 2026-04-17 - GBP 500,000.00 - settled",
                    "normalized_value": "500000.00",
                    "page": 2,
                    "reason": "Settled prior-call amount is historical context, not the current amount due.",
                    "authority": "STALE",
                }
            ],
            difficulty="ADVERSARIAL",
            document_mode="DEMO",
            register_overrides={"record_id": "NSG2-REC-001", "lp_id": "NSG2-LP-014"},
        )
    )
    cases.append(
        _spec(
            "CC-003",
            "Currency mismatch",
            "The numeric amount matches, but the notice denominates the call in USD while the fund record requires GBP.",
            ["currency_mismatch"],
            _canonical("CC-003", "Juniper Ridge Private Credit III", "Wrenford Mutual Assurance Pool", "10000000.00", "625000.00", "2026-09-25", bank_reference="JRPC3-WRE-CC06"),
            observed_overrides={"currency": "USD"},
            raw_overrides={"commitment_amount": "Commitment Amount: USD 10,000,000.00", "capital_call_amount": "Capital Call Amount: USD 625,000.00", "currency": "Currency: USD"},
        )
    )
    cases.append(
        _spec(
            "CC-004",
            "Due-date mismatch",
            "The notice date is correct, but settlement is requested seven days later than the administrator record.",
            ["date_mismatch"],
            _canonical("CC-004", "Harbourlight Secondaries IV", "Calder Fen Borough Retirement Scheme", "18000000.00", "450000.00", "2026-10-15", call_date="2026-09-30", bank_reference="HS4-CFB-CC09"),
            observed_overrides={"due_date": "2026-10-22"},
            raw_overrides={"call_date": "Notice Date: 30 September 2026", "due_date": "Payment Due: 22 October 2026"},
        )
    )
    cases.append(
        _spec(
            "CC-005",
            "Investor naming variation",
            "A shortened investor name omits punctuation and expands to the same legal entity after entity-name normalization.",
            ["investor_naming_variation", "normalization"],
            _canonical("CC-005", "Merevale Infrastructure Partners II", "Merevale Teachers' Pension Trust Limited", "22000000.00", "550000.00", "2026-09-29", bank_reference="MIP2-MTP-CC03"),
            raw_overrides={"investor_name": "Limited Partner: Merevale Teachers Pension Tr. Ltd"},
            warnings=["Investor name required legal-entity alias normalization."],
        )
    )
    cases.append(
        _spec(
            "CC-006",
            "Commitment omitted from notice",
            "The administrator register contains the commitment, but the capital-call notice never states it.",
            ["missing_commitment_amount", "missing_required_field"],
            _canonical("CC-006", "Redwood Arc Ventures III", "Ashcombe University Endowment", "18000000.00", "360000.00", "2026-09-24", bank_reference="RAV3-AUE-CC05"),
            missing_fields=["commitment_amount"],
        )
    )
    cases.append(
        _spec(
            "CC-007",
            "Duplicate investor register match",
            "Two active LP rows share the same legal name but carry different LP IDs and commitments; the notice omits the LP ID needed to choose one.",
            ["duplicate_investor", "ambiguous_register_match"],
            _canonical("CC-007", "Peregrine Transition Fund I", "Briarwick Heritage Endowment", "18000000.00", "450000.00", "2026-09-30", bank_reference="PTF1-BHE-CC02"),
            status_overrides={"investor_name": {"status": "REVIEW", "severity": "HIGH", "exception_code": "DUPLICATE_REGISTER_MATCH"}},
            candidates={
                "investor_name": [
                    {"record_id": "REC-CC-007-A", "lp_id": "PTF1-LP-041", "commitment_amount": "18000000.00"},
                    {"record_id": "REC-CC-007-B", "lp_id": "PTF1-LP-088", "commitment_amount": "12000000.00"},
                ]
            },
            exception_codes=["DUPLICATE_REGISTER_MATCH"],
            replayable=False,
            difficulty="ADVERSARIAL",
            extra_register_rows=[{"record_id": "REC-CC-007-B", "lp_id": "PTF1-LP-088", "commitment_amount": "12000000.00", "capital_call_amount": "300000.00"}],
        )
    )
    cases.append(
        _spec(
            "CC-008",
            "Percentage stated instead of amount",
            "The notice states only 2.50% of commitment; GBP 300,000 is derivable but no explicit call amount appears.",
            ["percentage_instead_of_amount", "missing_required_field"],
            _canonical("CC-008", "Silver Quay Buyout V", "Larkspur County Pension Partnership", "12000000.00", "300000.00", "2026-09-23", bank_reference="SQB5-LCP-CC08"),
            missing_fields=["capital_call_amount"],
            extra_lines={1: ["Capital Call Percentage: 2.50% of total commitment", "Derived amount for review only: GBP 300,000.00"]},
            candidates={"capital_call_amount": [{"derivation": "12000000.00 * 0.025", "normalized_value": "300000.00", "authority": "DERIVED"}]},
        )
    )
    cases.append(
        _spec(
            "CC-009",
            "European comma-decimal formatting",
            "Amounts use full stops as thousands separators and commas as decimal marks, testing locale-aware normalization.",
            ["comma_decimal_formatting", "normalization"],
            _canonical("CC-009", "Kestrel Ridge Opportunities II", "Morrowgate Insurance Reserve", "10000000.00", "625000.00", "2026-09-21", bank_reference="KRO2-MIR-CC06"),
            raw_overrides={"commitment_amount": "Total Commitment: GBP 10.000.000,00", "capital_call_amount": "Amount Due: GBP 625.000,00"},
            warnings=["European numeric separators normalized to invariant decimals."],
        )
    )
    cases.append(
        _spec(
            "CC-010",
            "Parenthetical negative amount",
            "The notice presents the amount in accounting parentheses, which denotes a negative value rather than a positive capital call.",
            ["parenthetical_negative_notation", "sign_mismatch"],
            _canonical("CC-010", "Beacon Strand Co-Investment I", "Ellington Science Foundation", "5000000.00", "125000.00", "2026-09-22", bank_reference="BSC1-ESF-CC02"),
            observed_overrides={"capital_call_amount": "-125000.00"},
            raw_overrides={"capital_call_amount": "Capital Call Amount: (GBP 125,000.00)"},
        )
    )
    cases.append(
        _spec(
            "CC-011",
            "OCR-like corrupted amount",
            "The digit 2 and zeros are corrupted as letters; an amount-in-words line corroborates the normalized value.",
            ["ocr_corruption", "normalization"],
            _canonical("CC-011", "Summit Vale Growth III", "Northbridge Municipal Retirement Fund", "25000000.00", "625000.00", "2026-09-30", bank_reference="SVG3-NMR-CC04"),
            raw_overrides={"capital_call_amount": "Capital Call Amount: GBP 6Z5,OOO.OO"},
            extra_lines={1: ["Amount in words: six hundred twenty-five thousand pounds sterling"]},
            warnings=["OCR substitutions Z->2 and O->0 required corroborated normalization."],
            difficulty="ADVERSARIAL",
        )
    )
    cases.append(
        _spec(
            "CC-012",
            "Conflicting amounts across pages",
            "Two pages independently label different values as the current capital-call amount, so no single canonical amount is selected.",
            ["conflicting_numbers", "multi_page_conflict"],
            _canonical("CC-012", "Mariner Square Real Assets II", "Halcyon Medical Research Foundation", "20000000.00", "625000.00", "2026-09-30", bank_reference="MSR2-HMR-CC05"),
            missing_fields=["capital_call_amount"],
            raw_overrides={
                "capital_call_amount": [
                    {"page": 1, "raw_text": "Capital Call Amount: GBP 625,000.00", "normalized_value": "625000.00", "authority": "CONFLICTING"},
                    {"page": 2, "raw_text": "Capital Call Amount: GBP 650,000.00", "normalized_value": "650000.00", "authority": "CONFLICTING"},
                ]
            },
            status_overrides={"capital_call_amount": {"status": "REVIEW", "severity": "HIGH", "exception_code": "CONFLICTING_SOURCE_VALUES"}},
            candidates={"capital_call_amount": [{"normalized_value": "625000.00", "page": 1}, {"normalized_value": "650000.00", "page": 2}]},
            null_reasons={"capital_call_amount": "conflicting"},
            exception_codes=["CONFLICTING_SOURCE_VALUES"],
            replayable=False,
            difficulty="ADVERSARIAL",
            extra_lines={2: ["Schedule status: CURRENT - independently approved by the fund controller"]},
        )
    )
    cases.append(
        _spec(
            "CC-013",
            "Stale previous-call amount",
            "A history schedule contains Call 04 at GBP 500,000, while the clearly labelled current Call 05 amount is GBP 625,000.",
            ["stale_previous_call", "distractor_amount"],
            _canonical("CC-013", "Northstar Growth Fund II", "Rosemere College Endowment", "25000000.00", "625000.00", "2026-09-30", bank_reference="NSGFII-RCE-CC05"),
            distractors=[{"field": "capital_call_amount", "raw_text": "Previous Call 04: GBP 500,000.00 - settled 2026-04-30", "normalized_value": "500000.00", "page": 2, "reason": "Historical and explicitly settled.", "authority": "STALE"}],
            extra_lines={2: ["Previous Call 04: GBP 500,000.00 - settled 2026-04-30"]},
            difficulty="ADVERSARIAL",
        )
    )
    cases.append(
        _spec(
            "CC-014",
            "Ambiguous fund name",
            "The notice says only Northstar Growth Fund, and the LP appears in both Fund I and Fund II without a fund identifier.",
            ["ambiguous_fund_name", "ambiguous_register_match"],
            _canonical("CC-014", "Northstar Growth Fund II", "Blackthorn Heritage Foundation", "16000000.00", "400000.00", "2026-09-30", bank_reference="NSGFII-BHF-CC04"),
            missing_fields=["fund_name"],
            raw_overrides={"fund_name": [{"page": 1, "raw_text": "Fund Name: Northstar Growth Fund", "normalized_value": None, "authority": "AMBIGUOUS"}]},
            status_overrides={"fund_name": {"status": "REVIEW", "severity": "HIGH", "exception_code": "AMBIGUOUS_FUND"}},
            candidates={"fund_name": [{"record_id": "REC-CC-014-A", "value": "Northstar Growth Fund II"}, {"record_id": "REC-CC-014-B", "value": "Northstar Growth Fund I"}]},
            null_reasons={"fund_name": "ambiguous"},
            exception_codes=["AMBIGUOUS_FUND"],
            replayable=False,
            difficulty="ADVERSARIAL",
            extra_register_rows=[{"record_id": "REC-CC-014-B", "lp_id": "NSG1-LP-063", "fund_name": "Northstar Growth Fund I", "commitment_amount": "9000000.00", "capital_call_amount": "225000.00"}],
        )
    )
    cases.append(
        _spec(
            "CC-015",
            "Mixed date formats",
            "The notice uses written and slash-form dates that normalize to the ISO dates held by the administrator.",
            ["different_date_formats", "normalization"],
            _canonical("CC-015", "Oak Meridian Continuation Fund I", "Dunmarsh Public Service Pension Pool", "14000000.00", "350000.00", "2026-09-30", bank_reference="OMC1-DPS-CC03"),
            raw_overrides={"call_date": "Notice Date: 04 Sep 2026", "due_date": "Payment Due Date: 30/09/2026"},
        )
    )
    cases.append(
        _spec(
            "CC-016",
            "Total versus remaining commitment confusion",
            "The notice labels the GBP 12.5m remaining commitment as total commitment; the legal commitment is GBP 20m.",
            ["total_vs_remaining_commitment", "commitment_basis_confusion"],
            _canonical("CC-016", "Ironwood Mid-Market Fund IV", "Westhaven Educational Trust", "20000000.00", "500000.00", "2026-09-29", bank_reference="IMMF4-WET-CC07"),
            observed_overrides={"commitment_amount": "12500000.00"},
            raw_overrides={"commitment_amount": "Total Commitment: GBP 12,500,000.00"},
            exception_codes=["COMMITMENT_BASIS_CONFUSION"],
            register_overrides={"remaining_commitment_amount": "12500000.00"},
            distractors=[{"field": "commitment_amount", "raw_text": "Uncalled amount before Call 07: GBP 12,500,000.00", "normalized_value": "12500000.00", "page": 1, "reason": "Remaining commitment was copied into the total-commitment label.", "authority": "CORROBORATING"}],
            extra_lines={1: ["Uncalled amount before Call 07: GBP 12,500,000.00"]},
        )
    )
    cases.append(
        _spec(
            "CC-017",
            "Wrong payment reference",
            "The notice carries the prior call's payment reference even though the amount and dates match.",
            ["wrong_bank_reference", "stale_reference"],
            _canonical("CC-017", "Meadowgate Special Situations III", "Orchard Crown Charitable Reserve", "11000000.00", "275000.00", "2026-09-26", bank_reference="MSS3-OCR-CC07"),
            observed_overrides={"bank_account_reference": "MSS3-OCR-CC06"},
            raw_overrides={"bank_account_reference": "Bank Reference: MSS3-OCR-CC06"},
        )
    )
    cases.append(
        _spec(
            "CC-018",
            "Due date missing",
            "The notice asks the LP to pay promptly but contains no calendar due date.",
            ["document_missing_required_field", "missing_due_date"],
            _canonical("CC-018", "Crown Mere Technology Fund II", "Penford Civic Retirement Trust", "8000000.00", "200000.00", "2026-09-24", bank_reference="CMT2-PCR-CC04"),
            missing_fields=["due_date"],
            extra_lines={1: ["Settlement instruction: Please remit promptly after internal approval."]},
        )
    )
    cases.append(
        _spec(
            "CC-019",
            "Notice belongs to the wrong investor",
            "Fund and amount happen to agree, but the notice is addressed to a different institutional LP.",
            ["wrong_investor_document"],
            _canonical("CC-019", "Ravenscourt European Buyout VI", "Stonebridge Cultural Foundation", "15000000.00", "375000.00", "2026-09-28", bank_reference="REB6-SCF-CC02"),
            observed_overrides={"investor_name": "Brackenmere Staff Pension Trust"},
        )
    )
    cases.append(
        _spec(
            "CC-020",
            "Duplicated capital-call notice",
            "Two files carry the same notice ID and identical capital-call content; each document passes alone, but the batch must be held as a duplicate.",
            ["duplicated_capital_call_notice", "batch_duplicate"],
            _canonical("CC-020", "Granite Reach Infrastructure III", "Pinecross Community Endowment", "9000000.00", "225000.00", "2026-09-27", bank_reference="GRI3-PCE-CC05"),
            exception_codes=["DUPLICATE_NOTICE"],
            replayable=False,
            difficulty="ADVERSARIAL",
            document_mode="DUPLICATE",
            additional_results={"document_id": {"expected": "one unique notice", "observed": "two copies of CC-020-NOTICE-A", "status": "REVIEW", "severity": "HIGH", "difference": None, "exception_code": "DUPLICATE_NOTICE"}},
            overall_override="REVIEW",
        )
    )
    cases.append(
        _spec(
            "CC-021",
            "Fractional-cent precision variance",
            "The administrator amount differs by half a penny before presentation rounding; the current zero-tolerance control retains the variance rather than silently clearing it.",
            ["fractional_cent_variance", "zero_tolerance", "boundary_case"],
            _canonical("CC-021", "Willow Forge Growth Opportunities I", "Eastmere Social Impact Trust", "12500000.00", "624999.995", "2026-09-30", bank_reference="WFGO1-ESI-CC03"),
            observed_overrides={"capital_call_amount": "625000.00"},
            raw_overrides={"capital_call_amount": "Capital Call Amount: GBP 625,000.00"},
        )
    )
    cases.append(
        _spec(
            "CC-022",
            "Capital component versus total due",
            "The notice separates a GBP 600,000 capital contribution from a GBP 25,000 management fee and a GBP 625,000 total due.",
            ["component_total", "management_fee", "distractor_amount"],
            _canonical("CC-022", "Foxglove Life Sciences Fund II", "Kingswell Research Endowment", "24000000.00", "600000.00", "2026-09-30", bank_reference="FLS2-KRE-CC06", management_fee="25000.00"),
            distractors=[{"field": "capital_call_amount", "raw_text": "Total cash due including fee: GBP 625,000.00", "normalized_value": "625000.00", "page": 1, "reason": "Total due includes the separately labelled management fee.", "authority": "CORROBORATING"}],
            extra_lines={1: ["Total cash due including fee: GBP 625,000.00"]},
        )
    )
    cases.append(
        _spec(
            "CC-023",
            "Dual-currency settlement equivalent",
            "The legal call is USD 800,000; GBP 625,000 is clearly labelled as a settlement equivalent at a locked rate.",
            ["dual_currency", "settlement_equivalent", "distractor_amount"],
            _canonical("CC-023", "Bluehaven Digital Infrastructure II", "Fairwater Municipal Superannuation Pool", "32000000.00", "800000.00", "2026-09-30", currency="USD", bank_reference="BDI2-FMS-CC05"),
            raw_overrides={"commitment_amount": "Commitment Amount: USD 32,000,000.00", "capital_call_amount": "Capital Call Amount: USD 800,000.00", "currency": "Currency: USD"},
            distractors=[{"field": "capital_call_amount", "raw_text": "GBP settlement equivalent at locked rate: GBP 625,000.00", "normalized_value": "625000.00", "page": 1, "reason": "Settlement equivalent is not the legal USD call amount.", "authority": "CORROBORATING"}],
            extra_lines={1: ["GBP settlement equivalent at locked rate: GBP 625,000.00"]},
        )
    )
    cases.append(
        _spec(
            "CC-024",
            "Same investor across two sleeves",
            "Two register rows share the same legal entity, but the notice includes the LP account ID that selects Sleeve B.",
            ["multiple_investor_sleeves", "disambiguated_match"],
            _canonical("CC-024", "Cedar Arc Climate Partners I", "Glenhaven Public Pension Partnership", "10000000.00", "250000.00", "2026-09-25", bank_reference="CACP1-GPP-B-CC02"),
            extra_lines={1: ["LP Account ID: CACP1-LP-072-B", "Investment sleeve: Institutional Sleeve B"]},
            candidates={"investor_name": [{"record_id": "REC-CC-024-A", "lp_id": "CACP1-LP-072-A"}, {"record_id": "REC-CC-024-B", "lp_id": "CACP1-LP-072-B", "selected": True}]},
            register_overrides={"record_id": "REC-CC-024-B", "lp_id": "CACP1-LP-072-B"},
            extra_register_rows=[{"record_id": "REC-CC-024-A", "lp_id": "CACP1-LP-072-A", "commitment_amount": "7000000.00", "capital_call_amount": "175000.00", "bank_account_reference": "CACP1-GPP-A-CC02"}],
        )
    )
    cases.append(
        _spec(
            "CC-025",
            "Call exceeds remaining commitment",
            "The notice matches the expected call record, but GBP 700,000 exceeds the GBP 625,000 uncalled balance before the drawdown.",
            ["call_exceeds_remaining_commitment", "cross_field_control"],
            _canonical("CC-025", "Longfield Lower Mid-Market IV", "Amberwick Pension Reserve", "5000000.00", "700000.00", "2026-09-28", bank_reference="LLMM4-APR-CC09"),
            status_overrides={"capital_call_amount": {"status": "REVIEW", "severity": "HIGH", "exception_code": "CALL_EXCEEDS_REMAINING_COMMITMENT"}},
            exception_codes=["CALL_EXCEEDS_REMAINING_COMMITMENT"],
            replayable=False,
            register_overrides={"remaining_commitment_amount": "625000.00"},
            extra_lines={1: ["Remaining uncalled commitment before this notice: GBP 625,000.00"]},
        )
    )
    cases.append(
        _spec(
            "CC-026",
            "Amended notice supersedes original",
            "A later notice explicitly supersedes the GBP 600,000 original and correctly states the current GBP 625,000 call.",
            ["amended_notice", "superseded_document"],
            _canonical("CC-026", "Thornfield Private Equity VI", "Clearbrook Academic Endowment", "25000000.00", "625000.00", "2026-09-30", bank_reference="TPE6-CAE-CC04"),
            document_mode="AMENDMENT",
            distractors=[{"field": "capital_call_amount", "raw_text": "Capital Call Amount: GBP 600,000.00", "normalized_value": "600000.00", "page": 1, "reason": "Original notice was explicitly superseded by the amendment.", "authority": "SUPERSEDED", "document_role": "SUPERSEDED"}],
        )
    )
    cases.append(
        _spec(
            "CC-027",
            "Payment receipt shortfall",
            "The notice and register agree at GBP 625,000, but the associated receipt records GBP 624,980, leaving GBP 20 unresolved.",
            ["payment_shortfall", "multi_document_reconciliation"],
            _canonical("CC-027", "Windermere Strategic Credit II", "Fallowmere County Retirement Fund", "10000000.00", "625000.00", "2026-09-30", bank_reference="WSC2-FCR-CC06"),
            document_mode="RECEIPT",
            exception_codes=["PAYMENT_SHORTFALL"],
            replayable=False,
            additional_results={"payment_amount": {"expected": "625000.00", "observed": "624980.00", "status": "MISMATCH", "severity": "HIGH", "difference": "-20.00", "exception_code": "PAYMENT_SHORTFALL"}},
            overall_override="MISMATCH",
        )
    )
    return cases


def _format_money(value: str) -> str:
    number = Decimal(value)
    return f"{number:,.2f}"


def _default_raw(field: str, value: Any, currency: str) -> str:
    labels = {
        "fund_name": "Fund Name",
        "investor_name": "Investor Name",
        "commitment_amount": "Commitment Amount",
        "capital_call_amount": "Capital Call Amount",
        "call_date": "Call Date",
        "due_date": "Due Date",
        "currency": "Currency",
        "bank_account_reference": "Payment Reference",
        "management_fee": "Management Fee",
        "document_type": "Document Type",
    }
    if field in NUMERIC_FIELDS:
        rendered = f"{currency} {_format_money(str(value))}"
    elif field == "document_type":
        rendered = "Capital Call"
    else:
        rendered = str(value)
    return f"{labels[field]}: {rendered}"


def _raw_entries(spec: dict[str, Any], observed: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for field in FIELDS:
        override = spec["raw_overrides"].get(field)
        if override is not None:
            items = override if isinstance(override, list) else [override]
            entries: list[dict[str, Any]] = []
            for item in items:
                if isinstance(item, str):
                    entries.append({"page": 1, "raw_text": item, "normalized_value": observed.get(field), "authority": "CURRENT"})
                else:
                    entry = dict(item)
                    entry.setdefault("page", 1)
                    entry.setdefault("normalized_value", observed.get(field))
                    entry.setdefault("authority", "CURRENT")
                    entries.append(entry)
            result[field] = entries
        elif observed.get(field) is not None:
            result[field] = [{"page": 1, "raw_text": _default_raw(field, observed[field], observed.get("currency") or spec["canonical"]["currency"]), "normalized_value": observed[field], "authority": "CURRENT"}]
    return result


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _standard_pages(spec: dict[str, Any], entries: dict[str, list[dict[str, Any]]]) -> list[list[str]]:
    max_page = max(
        [1]
        + [entry["page"] for field_entries in entries.values() for entry in field_entries]
        + list(spec["extra_lines"].keys())
        + [item.get("page", 1) for item in spec["distractors"]]
    )
    pages: list[list[str]] = []
    for page_number in range(1, max_page + 1):
        heading = "CAPITAL CALL NOTICE" if page_number == 1 else "CAPITAL CALL SUPPORTING SCHEDULE"
        page = [MARKER, "", spec["canonical"]["fund_name"], heading, f"Notice ID: {spec['case_id']}-NOTICE-A", ""]
        for field in FIELDS:
            for entry in entries.get(field, []):
                if entry["page"] == page_number:
                    page.append(entry["raw_text"])
        page.extend(["", "Operational context", spec["description"]])
        page.extend(spec["extra_lines"].get(page_number, []))
        page.extend(["", "Payment control", "No bank account or routing coordinates are present in this synthetic fixture.", MARKER])
        pages.append(page)
    return pages


def _write_text(path: Path, pages: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\f".join("\n".join(page).rstrip() + "\n" for page in pages), encoding="utf-8")


def _locate(path: Path, quote: str) -> dict[str, int]:
    for page_number, page in enumerate(path.read_text(encoding="utf-8").split("\f"), start=1):
        for line_number, line in enumerate(page.splitlines(), start=1):
            if line == quote:
                return {"page": page_number, "line_start": line_number, "line_end": line_number}
    raise ValueError(f"Evidence quote not found in {path}: {quote!r}")


def _document_paths(spec: dict[str, Any], pages: list[list[str]]) -> list[dict[str, Any]]:
    case_id = spec["case_id"]
    slug = _slug(spec["title"])
    mode = spec["document_mode"]
    if mode == "SAMPLE_MATCHING":
        documents = [{"document_id": f"DOC-{case_id}-A", "path": "data/sample_documents/matching_capital_call.txt", "format": "txt", "notice_id": "NORTHSTAR-ALBION-CALL-04", "disposition": "CURRENT", "duplicate_of": None}]
    elif mode == "DEMO":
        documents = [{"document_id": f"DOC-{case_id}-A", "path": "data/demo/northstar_growth_fund_ii/capital_call_notice.txt", "format": "txt", "notice_id": "NORTHSTAR-ALDERSTONE-CALL-04", "disposition": "CURRENT", "duplicate_of": None}]
    else:
        primary_rel = f"data/evals/notices/{case_id.lower()}_{slug}.txt"
        primary = ROOT / primary_rel
        _write_text(primary, pages)
        documents = [{"document_id": f"DOC-{case_id}-A", "path": primary_rel, "format": "txt", "notice_id": f"{case_id}-NOTICE-A", "disposition": "CURRENT", "duplicate_of": None}]
        if mode == "DUPLICATE":
            duplicate_rel = f"data/evals/notices/{case_id.lower()}_{slug}_copy.txt"
            duplicate = ROOT / duplicate_rel
            duplicate.write_bytes(primary.read_bytes())
            documents.append({"document_id": f"DOC-{case_id}-B", "path": duplicate_rel, "format": "txt", "notice_id": f"{case_id}-NOTICE-A", "disposition": "DUPLICATE", "duplicate_of": f"DOC-{case_id}-A"})
        elif mode == "AMENDMENT":
            amended_rel = primary_rel.replace(".txt", "_amended.txt")
            amended = ROOT / amended_rel
            primary_text = primary.read_text(encoding="utf-8")
            amended.write_text(primary_text.replace("CAPITAL CALL NOTICE", "AMENDED CAPITAL CALL NOTICE", 1).replace(f"Notice ID: {case_id}-NOTICE-A", f"Notice ID: {case_id}-NOTICE-B\nAmendment Status: CURRENT - supersedes {case_id}-NOTICE-A", 1), encoding="utf-8")
            primary.write_text(primary_text.replace("Capital Call Amount: GBP 625,000.00", "Capital Call Amount: GBP 600,000.00", 1).replace("Operational context", "Amendment Status: SUPERSEDED\n\nOperational context", 1), encoding="utf-8")
            documents[0]["disposition"] = "SUPERSEDED"
            documents.append({"document_id": f"DOC-{case_id}-B", "path": amended_rel, "format": "txt", "notice_id": f"{case_id}-NOTICE-B", "disposition": "CURRENT", "duplicate_of": None, "supersedes": f"DOC-{case_id}-A"})
        elif mode == "RECEIPT":
            receipt_rel = primary_rel.replace(".txt", "_receipt.txt")
            receipt = ROOT / receipt_rel
            receipt.write_text(
                f"{MARKER}\n\nPAYMENT RECEIPT - CONTROL COPY\nReceipt ID: {case_id}-RECEIPT-A\nInvestor Name: {spec['canonical']['investor_name']}\nPayment Receipt Amount: GBP 624,980.00\nPayment Reference: {spec['canonical']['bank_account_reference']}\nReceipt Date: 2026-09-30\n\nNo bank account or routing coordinates are present.\n{MARKER}\n",
                encoding="utf-8",
            )
            documents.append({"document_id": f"DOC-{case_id}-B", "path": receipt_rel, "format": "txt", "notice_id": f"{case_id}-RECEIPT-A", "disposition": "CORROBORATING", "duplicate_of": None})

    for document in documents:
        path = ROOT / document["path"]
        document["pages"] = len(path.read_text(encoding="utf-8").split("\f"))
        document["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return documents


def _difference(field: str, expected: Any, observed: Any) -> str | int | None:
    if expected is None or observed is None:
        return None
    if field in NUMERIC_FIELDS:
        return str(Decimal(str(observed)) - Decimal(str(expected)))
    if field in DATE_FIELDS:
        return (date.fromisoformat(str(observed)) - date.fromisoformat(str(expected))).days
    return None


def _build_case(spec: dict[str, Any]) -> dict[str, Any]:
    canonical = deepcopy(spec["canonical"])
    observed = {field: canonical[field] for field in FIELDS}
    observed.update(spec["observed_overrides"])
    for field in spec["missing_fields"]:
        observed[field] = None
    null_reasons = deepcopy(spec["null_reasons"])
    for field, value in observed.items():
        if value is None and field not in null_reasons:
            null_reasons[field] = "not_applicable" if canonical[field] is None else "missing"

    entries = _raw_entries(spec, observed)
    pages = _standard_pages(spec, entries)
    documents = _document_paths(spec, pages)
    current_document = next(document for document in documents if document["disposition"] == "CURRENT")
    current_path = ROOT / current_document["path"]

    field_evidence: dict[str, list[dict[str, Any]]] = {}
    for field, raw_entries in entries.items():
        evidence_items = []
        for index, entry in enumerate(raw_entries, start=1):
            evidence_items.append(
                {
                    "evidence_id": f"{spec['case_id']}-{field}-{index}",
                    "field": field,
                    "source_kind": "document",
                    "source_file": current_document["path"],
                    "document_id": current_document["document_id"],
                    "locator": _locate(current_path, entry["raw_text"]),
                    "raw_text": entry["raw_text"],
                    "normalized_value": entry.get("normalized_value"),
                    "authority": entry.get("authority", "CURRENT"),
                }
            )
        field_evidence[field] = evidence_items

    distractors: list[dict[str, Any]] = []
    for index, item in enumerate(spec["distractors"], start=1):
        source_document = current_document
        if item.get("document_role") == "SUPERSEDED":
            source_document = next(document for document in documents if document["disposition"] == "SUPERSEDED")
        source_path = ROOT / source_document["path"]
        distractors.append(
            {
                "evidence_id": f"{spec['case_id']}-DISTRACTOR-{index}",
                "field": item["field"],
                "source_kind": "document",
                "source_file": source_document["path"],
                "document_id": source_document["document_id"],
                "locator": _locate(source_path, item["raw_text"]),
                "raw_text": item["raw_text"],
                "normalized_value": item.get("normalized_value"),
                "authority": item.get("authority", "STALE"),
                "reason": item["reason"],
            }
        )

    field_results: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        expected = canonical[field]
        actual = observed[field]
        override = spec["status_overrides"].get(field)
        if override:
            status = override["status"]
            severity = override["severity"]
            exception_code = override.get("exception_code")
        elif expected is None and actual is None:
            status, severity, exception_code = "PASS", "NONE", None
        elif actual is None:
            status, severity, exception_code = "MISSING", SEVERITY_BY_FIELD[field], "REQUIRED_FIELD_MISSING"
        elif field in NUMERIC_FIELDS and Decimal(str(actual)) == Decimal(str(expected)):
            status, severity, exception_code = "PASS", "NONE", None
        elif actual == expected:
            status, severity, exception_code = "PASS", "NONE", None
        else:
            status, severity, exception_code = "MISMATCH", SEVERITY_BY_FIELD[field], f"{field.upper()}_MISMATCH"
        field_results[field] = {
            "expected": expected,
            "observed": actual,
            "status": status,
            "severity": severity,
            "difference": _difference(field, expected, actual),
            "exception_code": exception_code,
            "evidence_ids": [item["evidence_id"] for item in field_evidence.get(field, [])],
        }

    additional_results = deepcopy(spec["additional_results"])
    statuses = [result["status"] for result in field_results.values()]
    counts = {status: statuses.count(status) for status in ("PASS", "MISMATCH", "MISSING", "REVIEW")}
    if spec["overall_override"]:
        overall = spec["overall_override"]
    elif counts["MISMATCH"]:
        overall = "MISMATCH"
    elif counts["MISSING"]:
        overall = "MISSING"
    elif counts["REVIEW"]:
        overall = "REVIEW"
    else:
        overall = "PASS"

    exception_fields = [field for field in FIELDS if field_results[field]["status"] != "PASS"]
    exception_fields.extend(field for field, result in additional_results.items() if result["status"] != "PASS")
    severities = [result["severity"] for result in field_results.values() if result["status"] != "PASS"]
    severities.extend(result["severity"] for result in additional_results.values() if result["status"] != "PASS")
    case_severity = max(severities, key=SEVERITY_RANK.get) if severities else "NONE"
    requires_review = overall != "PASS"
    if overall == "PASS":
        action = "AUTO_CLEAR"
        rationale = "All labelled controls pass; no reviewer intervention is expected."
    elif case_severity == "HIGH":
        action = "HOLD_AND_INVESTIGATE"
        rationale = "At least one high-severity control requires evidence-backed human review."
    else:
        action = "INVESTIGATE"
        rationale = "A reconciliation exception requires human review before release."

    register_path = "data/demo/northstar_growth_fund_ii/investor_register.csv" if spec["document_mode"] == "DEMO" else "data/evals/investor_register.csv"
    register_xlsx = register_path.replace(".csv", ".xlsx")
    register_id = spec["register_overrides"].get("record_id", f"REC-{spec['case_id']}-A")
    return {
        "case_id": spec["case_id"],
        "title": spec["title"],
        "description": spec["description"],
        "difficulty": spec["difficulty"],
        "scenario_types": spec["scenario_types"],
        "replayable_against_current_reconciler": spec["replayable"],
        "register_ref": {"csv": register_path, "xlsx": register_xlsx, "record_id": register_id, "sheet": "LP Register"},
        "documents": documents,
        "canonical_record": canonical,
        "expected_extraction": {
            "canonical_values": observed,
            "null_reasons": null_reasons,
            "field_evidence": field_evidence,
            "distractors": distractors,
            "candidates": spec["candidates"],
            "warnings": spec["warnings"],
        },
        "expected_reconciliation": {
            "overall_status": overall,
            "case_severity": case_severity,
            "exception_fields": exception_fields,
            "exception_codes": sorted(set(spec["exception_codes"] + [result["exception_code"] for result in field_results.values() if result["exception_code"]])),
            "counts": counts,
            "field_results": field_results,
            "additional_results": additional_results,
        },
        "reviewer_label": {
            "requires_human_review": requires_review,
            "recommended_action": action,
            "rationale": rationale,
        },
    }


REGISTER_COLUMNS = (
    "record_id",
    "case_id",
    "lp_id",
    "fund_name",
    "investor_name",
    "commitment_amount",
    "drawn_to_date_before_call",
    "remaining_commitment_amount",
    "capital_call_amount",
    "capital_call_percentage",
    "call_number",
    "call_date",
    "due_date",
    "currency",
    "bank_account_reference",
    "management_fee",
    "document_type",
    "record_status",
)


def _register_row(spec: dict[str, Any]) -> dict[str, Any]:
    canonical = spec["canonical"]
    commitment = Decimal(canonical["commitment_amount"])
    call_amount = Decimal(canonical["capital_call_amount"])
    remaining = Decimal(spec["register_overrides"].get("remaining_commitment_amount", str(commitment * Decimal("0.60"))))
    drawn = commitment - remaining
    default = {
        "record_id": f"REC-{spec['case_id']}-A",
        "case_id": spec["case_id"],
        "lp_id": f"LP-{spec['case_id'].replace('-', '')}",
        "fund_name": canonical["fund_name"],
        "investor_name": canonical["investor_name"],
        "commitment_amount": canonical["commitment_amount"],
        "drawn_to_date_before_call": str(drawn.quantize(Decimal("0.01"))),
        "remaining_commitment_amount": str(remaining.quantize(Decimal("0.01"))),
        "capital_call_amount": canonical["capital_call_amount"],
        "capital_call_percentage": str((call_amount / commitment).quantize(Decimal("0.000001"))),
        "call_number": int(spec["case_id"].split("-")[1]) % 9 + 1,
        "call_date": canonical["call_date"],
        "due_date": canonical["due_date"],
        "currency": canonical["currency"],
        "bank_account_reference": canonical["bank_account_reference"],
        "management_fee": canonical["management_fee"] or "",
        "document_type": canonical["document_type"],
        "record_status": "ACTIVE",
    }
    default.update(spec["register_overrides"])
    return default


def _eval_register_rows(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in specs:
        if spec["document_mode"] == "DEMO":
            continue
        primary = _register_row(spec)
        rows.append(primary)
        for overrides in spec["extra_register_rows"]:
            duplicate = dict(primary)
            duplicate.update(overrides)
            commitment = Decimal(str(duplicate["commitment_amount"]))
            amount = Decimal(str(duplicate["capital_call_amount"]))
            duplicate["capital_call_percentage"] = str((amount / commitment).quantize(Decimal("0.000001")))
            rows.append(duplicate)
    return rows


def _demo_register_rows() -> list[dict[str, Any]]:
    seeds = [
        ("NSG2-LP-014", "Alderstone Civic Pension Partnership", "25000000.00", "5000000.00", "20000000.00", "625000.00", "0.025000", "25000.00", "NSGFII-ALD-CC04"),
        ("NSG2-LP-021", "Eldermere Arts Endowment", "12000000.00", "2400000.00", "9600000.00", "300000.00", "0.025000", "12000.00", "NSGFII-EAE-CC04"),
        ("NSG2-LP-033", "Wrenfold Municipal Pension Board", "40000000.00", "8000000.00", "32000000.00", "1000000.00", "0.025000", "40000.00", "NSGFII-WMP-CC04"),
        ("NSG2-LP-046", "Stonecross University Investment Pool", "18000000.00", "3600000.00", "14400000.00", "450000.00", "0.025000", "18000.00", "NSGFII-SUI-CC04"),
        ("NSG2-LP-052", "Merebrook Insurance General Account", "30000000.00", "6000000.00", "24000000.00", "750000.00", "0.025000", "30000.00", "NSGFII-MIG-CC04"),
        ("NSG2-LP-067", "Redcliff Regional Retirement Partnership", "22000000.00", "4400000.00", "17600000.00", "550000.00", "0.025000", "22000.00", "NSGFII-RRR-CC04"),
        ("NSG2-LP-074", "Kingsmere Medical Research Foundation", "8000000.00", "1600000.00", "6400000.00", "200000.00", "0.025000", "8000.00", "NSGFII-KMR-CC04"),
        ("NSG2-LP-081", "Harbour Fen Public Service Trust", "15000000.00", "3000000.00", "12000000.00", "375000.00", "0.025000", "15000.00", "NSGFII-HFP-CC04"),
    ]
    rows = []
    for index, seed in enumerate(seeds, start=1):
        lp_id, investor, commitment, drawn, remaining, call_amount, percentage, fee, reference = seed
        rows.append(
            {
                "record_id": f"NSG2-REC-{index:03d}",
                "case_id": "CC-002" if index == 1 else "",
                "lp_id": lp_id,
                "fund_name": "Northstar Growth Fund II",
                "investor_name": investor,
                "commitment_amount": commitment,
                "drawn_to_date_before_call": drawn,
                "remaining_commitment_amount": remaining,
                "capital_call_amount": call_amount,
                "capital_call_percentage": percentage,
                "call_number": 4,
                "call_date": "2026-09-04",
                "due_date": "2026-09-30",
                "currency": "GBP",
                "bank_account_reference": reference,
                "management_fee": fee,
                "document_type": "CAPITAL_CALL",
                "record_status": "ACTIVE",
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_schema() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://fundops.invalid/schemas/capital-call-reconciliation-gold-v1.json",
        "title": "FundOps capital-call reconciliation gold corpus",
        "type": "object",
        "required": ["schema_version", "dataset_id", "synthetic", "reconciliation_fields", "cases"],
        "properties": {
            "schema_version": {"const": "1.0.0"},
            "dataset_id": {"const": "fundops-private-markets-capital-call-v1"},
            "synthetic": {"const": True},
            "reconciliation_fields": {"type": "array", "prefixItems": [{"const": field} for field in FIELDS], "items": False},
            "cases": {"type": "array", "minItems": 20, "maxItems": 30, "items": {"$ref": "#/$defs/case"}},
        },
        "$defs": {
            "case": {
                "type": "object",
                "required": ["case_id", "scenario_types", "canonical_record", "expected_extraction", "expected_reconciliation", "reviewer_label"],
                "properties": {
                    "case_id": {"type": "string", "pattern": "^CC-[0-9]{3}$"},
                    "expected_reconciliation": {
                        "type": "object",
                        "required": ["overall_status", "case_severity", "exception_fields", "field_results"],
                        "properties": {
                            "overall_status": {"enum": ["PASS", "MISMATCH", "MISSING", "REVIEW"]},
                            "case_severity": {"enum": ["NONE", "LOW", "MEDIUM", "HIGH"]},
                        },
                    },
                },
            }
        },
    }
    path = ROOT / "data/gold/capital_call_reconciliation.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def _write_docs(cases: list[dict[str, Any]]) -> None:
    rows = [
        "# Synthetic private-markets reconciliation dataset",
        "",
        "This corpus contains entirely fictional institutional investors, funds, notices, references, and amounts. It includes no personal data and no usable payment coordinates. Every source document is marked `FICTIONAL - DEMO ONLY - DO NOT PAY`.",
        "",
        "## Dataset layout",
        "",
        "- `data/demo/northstar_growth_fund_ii/` is the one-click flagship: a polished two-page Northstar package with its LP register, canonical record, notice, and case-level gold label.",
        "- `data/fund_record.json` and `data/sample_documents/` provide the compact Albion clean-match and parser regression fixtures.",
        "- `data/evals/investor_register.csv` and `.xlsx` hold canonical register rows for the evaluation cases.",
        "- `data/evals/notices/` contains text fixtures. Form-feed characters delimit pages.",
        "- `data/gold/capital_call_reconciliation.json` is the authoritative machine-readable manifest; its companion schema documents the stable envelope.",
        "",
        "## Gold-label contract",
        "",
        "Money values are lossless decimal strings, dates are ISO `YYYY-MM-DD`, currencies are ISO 4217 codes, and missing or unresolved extracted values are explicit JSON `null`. An omitted key is unlabelled; a present key with `null` is labelled missing, ambiguous, or conflicting, with the reason in `expected_extraction.null_reasons`.",
        "",
        "Each case contains all ten canonical reconciliation fields, per-field expected/observed values, status, severity, signed difference, exception code, and evidence IDs. Evidence records point to an exact file, page, and line. `additional_results` carries batch or cross-document controls such as duplicate notice IDs and payment receipts without changing the application's core record model.",
        "",
        "`replayable_against_current_reconciler` means the current deterministic reconciler can reproduce the labelled field results from the canonical extracted values. Ambiguity, duplicate detection, cross-field capacity controls, and payment matching remain explicit evaluation targets even when the current application does not yet implement them.",
        "",
        "## Scenario rationale",
        "",
        "| Case | Scenario | Why it exists | Expected outcome |",
        "| --- | --- | --- | --- |",
    ]
    for case in cases:
        expected = case["expected_reconciliation"]
        fields = ", ".join(expected["exception_fields"]) or "none"
        rows.append(f"| {case['case_id']} | {case['title']} | {case['description']} | {expected['overall_status']} / {expected['case_severity']}; exceptions: {fields} |")
    rows.extend(
        [
            "",
            "## Northstar demo package",
            "",
            "The flagship package reconciles Alderstone Civic Pension Partnership against Northstar Growth Fund II Call 04 and is also gold case `CC-002`. The register expects GBP 625,000 due 30 September 2026. The notice states GBP 650,000 due 28 September 2026, while all other canonical fields agree. Page 2 includes a settled GBP 500,000 prior call as a deliberately stale distractor.",
            "",
            "The compact Albion fixtures retain a GBP 5,000,000 commitment, GBP 625,000 expected call, and 18 September 2026 due date. They provide a clean matching notice plus a minimal discrepancy regression notice.",
            "",
            "## Regeneration and validation",
            "",
            "Run `python scripts/generate_gold_dataset.py`, then `node scripts/generate_investor_workbooks.mjs`, then `python scripts/generate_fixture_pdfs.py` using environments that provide the documented artifact dependencies. Run `python -m pytest -q` to validate schema shape, cross-file references, evidence locators, register parity, duplicate declarations, financial invariants, and replayable reconciliation labels.",
            "",
            "Do not use these fixtures for payment, onboarding, sanctions screening, tax reporting, or any real investor workflow.",
        ]
    )
    path = ROOT / "docs/DATASET.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_demo_readme() -> None:
    text = f"""# Northstar Growth Fund II demo package

{MARKER}

This entirely fictional package contains an eight-LP register, a two-page Call 04 notice for Alderstone Civic Pension Partnership, the expected canonical record, and the case-level gold label. The administrator expects GBP 625,000 due 2026-09-30; the notice states GBP 650,000 due 2026-09-28. Page 2 includes a settled prior-call amount as stale evidence.

No personal data, account numbers, routing codes, or usable payment instructions are included.
"""
    (DEMO_DIR / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    notices_dir = ROOT / "data/evals/notices"
    notices_dir.mkdir(parents=True, exist_ok=True)
    for stale_fixture in notices_dir.glob("*.txt"):
        stale_fixture.unlink()
    specs = _cases()
    built_cases = [_build_case(spec) for spec in specs]
    corpus = {
        "schema_version": "1.0.0",
        "dataset_id": "fundops-private-markets-capital-call-v1",
        "dataset_name": "FundOps private-markets capital-call gold corpus",
        "synthetic": True,
        "generated_for": "FundOps Control Room",
        "as_of_date": "2026-09-04",
        "null_semantics": "Present null means labelled missing, ambiguous, or conflicting; omitted means unlabelled.",
        "reconciliation_fields": list(FIELDS),
        "status_values": ["PASS", "MISMATCH", "MISSING", "REVIEW"],
        "severity_values": ["NONE", "LOW", "MEDIUM", "HIGH"],
        "cases": built_cases,
    }
    GOLD_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLD_PATH.write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_schema()
    _write_csv(EVAL_REGISTER_CSV, _eval_register_rows(specs))
    _write_csv(DEMO_REGISTER_CSV, _demo_register_rows())

    demo_case = next(case for case in built_cases if case["case_id"] == "CC-002")
    (DEMO_DIR / "expected_canonical_record.json").write_text(json.dumps(demo_case["canonical_record"], indent=2) + "\n", encoding="utf-8")
    (DEMO_DIR / "gold_label.json").write_text(json.dumps(demo_case, indent=2) + "\n", encoding="utf-8")
    _write_demo_readme()
    _write_docs(built_cases)

    print(f"Generated {len(built_cases)} gold cases")
    print(GOLD_PATH.relative_to(ROOT))
    print(EVAL_REGISTER_CSV.relative_to(ROOT))
    print(DEMO_REGISTER_CSV.relative_to(ROOT))


if __name__ == "__main__":
    main()
