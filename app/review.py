"""Independent, evidence-first review of deterministic reconciliation results.

The reviewer is deliberately a separate workflow stage.  It reads one
``ReconciliationItem`` at a time and creates a ``ReviewFinding``; it never
changes extracted values, reconciliation results, or human audit decisions.

The default reviewer is a deterministic fixture so the complete demo works
offline.  ``OpenAICompatibleEvidenceReviewer`` uses the extraction module's
existing injectable ``LLMTransport`` contract and validates a strict structured
response.  Only the current field, its evidence/provenance, and its own
reconciliation result are included in a model request.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Union
from urllib import request as urllib_request

from pydantic import Field, field_validator, model_validator

from .extraction import LLMTransport
from .models import (
    DifferenceValue,
    DomainModel,
    ExtractionMethod,
    FieldValue,
    ReconciliationItem,
    ReconciliationReport,
    ReconciliationStatus,
    Severity,
)


class ReviewStatus(str, Enum):
    """Outcome of checking an extracted value against its cited evidence."""

    SUPPORTED = "SUPPORTED"
    CHALLENGE = "CHALLENGE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_REVIEWED = "NOT_REVIEWED"


class ReviewMethod(str, Enum):
    DETERMINISTIC_FIXTURE = "DETERMINISTIC_FIXTURE"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    LOCAL_POLICY = "LOCAL_POLICY"
    UNAVAILABLE = "UNAVAILABLE"


class ReviewSourceReference(DomainModel):
    """A UI-ready pointer to the evidence supplied to the reviewer."""

    source: str = Field(min_length=1)
    source_type: Optional[str] = Field(default=None, min_length=1)
    page: Optional[int] = Field(default=None, ge=1)
    sheet: Optional[str] = Field(default=None, min_length=1)
    cell: Optional[str] = Field(default=None, min_length=1)
    evidence: Optional[str] = None

    @model_validator(mode="after")
    def require_coherent_locator(self) -> "ReviewSourceReference":
        if self.cell is not None and self.sheet is None:
            raise ValueError("cell requires a workbook sheet")
        if self.page is not None and (self.sheet is not None or self.cell is not None):
            raise ValueError("page cannot be combined with a workbook sheet or cell")
        return self


class ReviewProvenance(DomainModel):
    """Allowlisted field provenance used as reviewer input."""

    source: str = Field(min_length=1)
    source_type: Optional[str] = Field(default=None, min_length=1)
    page: Optional[int] = Field(default=None, ge=1)
    sheet: Optional[str] = Field(default=None, min_length=1)
    cell: Optional[str] = Field(default=None, min_length=1)
    extraction_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    extraction_method: Optional[ExtractionMethod] = None
    extractor: Optional[str] = Field(default=None, min_length=1)
    extraction_timestamp: Optional[datetime] = None
    abstention_reason: Optional[str] = Field(default=None, min_length=1)

    @field_validator("extraction_timestamp")
    @classmethod
    def require_timestamp_timezone(
        cls, value: Optional[datetime]
    ) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("extraction_timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def require_coherent_locator(self) -> "ReviewProvenance":
        if self.cell is not None and self.sheet is None:
            raise ValueError("cell requires a workbook sheet")
        if self.page is not None and (self.sheet is not None or self.cell is not None):
            raise ValueError("page cannot be combined with a workbook sheet or cell")
        return self


class ReconciliationSnapshot(DomainModel):
    """Only the current field's deterministic reconciliation context."""

    status: ReconciliationStatus
    severity: Severity
    expected: FieldValue = None
    observed: FieldValue = None
    difference: DifferenceValue = None
    explanation: str = Field(min_length=1)


class EvidenceReviewRequest(DomainModel):
    """Complete local request for one field, never a whole document/record."""

    case_id: str = Field(min_length=1)
    source_document: str = Field(min_length=1)
    field: str = Field(min_length=1)
    extracted_value: FieldValue = None
    source_evidence: Optional[str] = None
    provenance: ReviewProvenance
    reconciliation: ReconciliationSnapshot

    def model_payload(self) -> Dict[str, Any]:
        """Return the strict data-minimized payload permitted for model mode.

        Case identifiers and the report/document containers are intentionally
        excluded.  The provenance source is retained because it is part of the
        supplied citation for this one field.
        """

        return {
            "field": self.field,
            "extracted_value": _json_value(self.extracted_value),
            "source_evidence": self.source_evidence,
            # Operational provenance is retained on the local request, while
            # only the locator and extraction facts needed for evidence review
            # cross the model boundary. In particular, free-form abstention
            # reasons are never promoted into model instructions.
            "provenance": self.provenance.model_dump(
                mode="json",
                include={
                    "source",
                    "source_type",
                    "page",
                    "sheet",
                    "cell",
                    "extraction_confidence",
                    "extraction_method",
                },
            ),
            "reconciliation": self.reconciliation.model_dump(mode="json"),
        }


class ReviewAssessment(DomainModel):
    """A reviewer's structured assessment before local metadata is attached."""

    status: ReviewStatus
    review_reason: str = Field(min_length=1)
    challenged_value: FieldValue = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def enforce_status_shape(self) -> "ReviewAssessment":
        if self.status is not ReviewStatus.CHALLENGE and self.challenged_value is not None:
            raise ValueError("challenged_value is only valid for CHALLENGE findings")
        if self.status is ReviewStatus.NOT_REVIEWED and self.confidence is not None:
            raise ValueError("NOT_REVIEWED cannot have review confidence")
        return self


class ReviewFinding(DomainModel):
    """Independent finding kept separate from deterministic and human records."""

    case_id: str = Field(min_length=1)
    source_document: str = Field(min_length=1)
    field: str = Field(min_length=1)
    reviewed_value: FieldValue = None
    status: ReviewStatus
    review_reason: str = Field(min_length=1)
    challenged_value: FieldValue = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    source_references: List[ReviewSourceReference] = Field(default_factory=list)
    reconciliation_status: ReconciliationStatus
    requires_human_review: bool
    review_method: ReviewMethod
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def enforce_safety_invariants(self) -> "ReviewFinding":
        if self.status is not ReviewStatus.CHALLENGE and self.challenged_value is not None:
            raise ValueError("challenged_value is only valid for CHALLENGE findings")
        if self.status is ReviewStatus.NOT_REVIEWED and self.confidence is not None:
            raise ValueError("NOT_REVIEWED cannot have review confidence")
        expected_escalation = (
            self.reconciliation_status is not ReconciliationStatus.PASS
            or self.status is not ReviewStatus.SUPPORTED
        )
        if self.requires_human_review != expected_escalation:
            raise ValueError(
                "requires_human_review must reflect reconciliation and review status"
            )
        return self


class ReviewReport(DomainModel):
    """UI-compatible result of reviewing every row in a reconciliation report."""

    case_id: str = Field(min_length=1)
    source_document: str = Field(min_length=1)
    findings: List[ReviewFinding]
    counts: Dict[str, int]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_counts(self) -> "ReviewReport":
        expected = {status.value: 0 for status in ReviewStatus}
        for finding in self.findings:
            expected[finding.status.value] += 1
        if self.counts != expected:
            raise ValueError("counts must match review findings")
        return self

    def finding_for(self, field: str) -> Optional[ReviewFinding]:
        return next((finding for finding in self.findings if finding.field == field), None)

    @property
    def escalations(self) -> List[ReviewFinding]:
        return [finding for finding in self.findings if finding.requires_human_review]


class EvidenceReviewer(Protocol):
    """Narrow seam shared by deterministic and model-backed reviewers."""

    review_method: ReviewMethod

    def assess(self, review_request: EvidenceReviewRequest) -> ReviewAssessment:
        ...


_MONEY_FIELDS = frozenset(
    {"commitment_amount", "capital_call_amount", "management_fee"}
)
_DATE_FIELDS = frozenset({"call_date", "due_date"})
_FIELD_ALIASES: Dict[str, Sequence[str]] = {
    "fund_name": ("fund name", "fund"),
    "investor_name": ("investor name", "limited partner", "investor"),
    "commitment_amount": (
        "total commitment",
        "commitment amount",
        "commitment",
    ),
    "capital_call_amount": (
        "capital call amount",
        "capital call",
        "call amount",
        "amount due",
    ),
    "call_date": ("call date", "notice date"),
    "due_date": ("payment due date", "payment due", "due date"),
    "currency": ("currency",),
    "bank_account_reference": (
        "bank account reference",
        "account reference",
        "payment reference",
        "bank reference",
    ),
    "management_fee": ("management fee",),
    "document_type": ("document type",),
}
_MISSING_MARKER = re.compile(
    r"\b(?:n\s*/?\s*a|not\s+applicable|not\s+provided|none)\b", re.I
)
_MONEY_PATTERN = re.compile(
    r"(?<![\w.])"
    r"(?:(?:GBP|USD|EUR|£|\$|€)\s*)?"
    r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
    # Permit sentence punctuation, but forbid stopping before a numeric comma
    # group or decimal suffix (which would turn 125,000. into 125).
    r"(?!\w|[.,]\d)",
    re.I,
)
_CURRENCY_PATTERN = re.compile(r"\b(?:GBP|USD|EUR)\b|[£$€]", re.I)
_PERCENT_PATTERN = re.compile(
    r"(?<!\w)[-+]?\d+(?:\.\d+)?\s*(?:%|\bpercent\b|\bper\s+cent\b)",
    re.I,
)
_COMPETING_AMOUNT_CUE = re.compile(
    r"(?:\bor\b|\bversus\b|\bvs\.?\b|\bgross\b|\bnet\b|\brevised\b|/)",
    re.I,
)
_NEGATION_PATTERN = re.compile(
    r"\b(?:not|no|never|incorrect|wrong|excluded?|superseded|do\s+not\s+use)\b",
    re.I,
)
_BENIGN_NEGATION_PATTERN = re.compile(
    r"\b(?:not|no)\s+(?:later|earlier|before|after|more|less)\s+than\b",
    re.I,
)
_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b"),
    re.compile(
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
        r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b",
        re.I,
    ),
    re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
        re.I,
    ),
)


@dataclass(frozen=True)
class _CandidateSet:
    values: List[FieldValue]
    relevant: bool


def _json_value(value: FieldValue) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _normalize_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    return " ".join(normalized.split()).strip(" \t\r\n.:;,\"'").casefold()


def _normalize_reference(value: object) -> str:
    return "".join(character for character in _normalize_text(value) if character.isalnum())


def _parse_amount(raw: object) -> Decimal:
    match = _MONEY_PATTERN.search(str(raw))
    if match is None:
        raise InvalidOperation("no monetary value found")
    cleaned = match.group(0).strip().replace(",", "")
    for token in ("GBP", "USD", "EUR", "£", "$", "€"):
        cleaned = re.sub(re.escape(token), "", cleaned, flags=re.I)
    return Decimal(cleaned.strip())


def _parse_date(raw: object) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    cleaned = " ".join(str(raw).strip().replace(",", "").split())
    for date_format in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
    ):
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    raise ValueError("unrecognized date")


def _normalize_document_type(value: object) -> str:
    normalized = re.sub(r"[^a-z]+", " ", str(value).casefold()).strip()
    if "capital call" in normalized:
        return "CAPITAL_CALL"
    if "distribution" in normalized:
        return "DISTRIBUTION_NOTICE"
    if normalized == "unknown":
        return "UNKNOWN"
    return normalized.upper().replace(" ", "_")


def _normal_value(field: str, value: FieldValue) -> object:
    if value is None:
        return None
    if field in _MONEY_FIELDS:
        try:
            return _parse_amount(value)
        except (InvalidOperation, ValueError):
            return ("invalid-money", _normalize_text(value))
    if field in _DATE_FIELDS:
        try:
            return _parse_date(value)
        except ValueError:
            return ("invalid-date", _normalize_text(value))
    if field == "currency":
        symbols = {"£": "GBP", "$": "USD", "€": "EUR"}
        raw = str(value).strip()
        return symbols.get(raw, raw.upper())
    if field == "document_type":
        return _normalize_document_type(value)
    if field == "bank_account_reference":
        return _normalize_reference(value)
    return _normalize_text(value)


def _values_equal(field: str, left: FieldValue, right: FieldValue) -> bool:
    return _normal_value(field, left) == _normal_value(field, right)


def _labelled_fragments(field: str, evidence: str) -> List[str]:
    aliases = _FIELD_ALIASES.get(field, (field.replace("_", " "),))
    alias_expression = "|".join(
        re.escape(alias).replace(r"\ ", r"\s+")
        for alias in sorted(aliases, key=len, reverse=True)
    )
    label_pattern = re.compile(
        rf"(?<!\w)(?:{alias_expression})(?!\w)",
        re.I,
    )
    segments = re.split(r"(?:\r?\n|;|\||(?<!\d)\.\s+)", evidence)
    fragments: List[str] = []
    for segment in segments:
        match = label_pattern.search(segment)
        if match is None:
            continue
        tail = segment[match.end() :]
        tail = re.sub(
            r"^\s*(?:(?:is)\b|[:=\-–—])\s*",
            "",
            tail,
            flags=re.I,
        )
        if tail.strip():
            fragments.append(tail.strip())
    return fragments


def _unique_values(field: str, values: Sequence[FieldValue]) -> List[FieldValue]:
    unique: List[FieldValue] = []
    for value in values:
        if not any(_values_equal(field, value, existing) for existing in unique):
            unique.append(value)
    return unique


def _money_candidates(fragments: Sequence[str]) -> List[FieldValue]:
    values: List[FieldValue] = []
    for fragment in fragments:
        candidate_text = fragment
        for date_pattern in _DATE_PATTERNS:
            candidate_text = date_pattern.sub(
                lambda match: " " * len(match.group(0)), candidate_text
            )
        candidate_text = _PERCENT_PATTERN.sub(
            lambda match: " " * len(match.group(0)), candidate_text
        )
        matches = list(_MONEY_PATTERN.finditer(candidate_text))
        previous = None
        for match in matches:
            # The first amount following a field label is attributable to that
            # field. Later numbers are alternatives only when the language says
            # they compete; dates, percentages, fees, and payment terms are not.
            if previous is not None:
                separator = candidate_text[previous.end() : match.start()]
                if _COMPETING_AMOUNT_CUE.search(separator) is None:
                    previous = match
                    continue
            try:
                values.append(_parse_amount(match.group(0)))
            except (InvalidOperation, ValueError):
                pass
            previous = match
    return values


def _date_candidates(fragments: Sequence[str]) -> List[FieldValue]:
    values: List[FieldValue] = []
    for fragment in fragments:
        matches = [match for pattern in _DATE_PATTERNS for match in pattern.finditer(fragment)]
        for match in sorted(matches, key=lambda item: item.start()):
            try:
                values.append(_parse_date(match.group(0)))
            except ValueError:
                continue
            numeric = re.fullmatch(
                r"(\d{1,2})([/-])(\d{1,2})\2(\d{4})", match.group(0)
            )
            if numeric is not None:
                first, second, year = (
                    int(numeric.group(1)),
                    int(numeric.group(3)),
                    int(numeric.group(4)),
                )
                if first <= 12 and second <= 12 and first != second:
                    # With no locale in field provenance, both D/M/Y and M/D/Y
                    # are directly plausible and must be surfaced to a human.
                    values.append(date(year, first, second))
    return values


def _currency_candidates(evidence: str) -> List[FieldValue]:
    symbol_map = {"£": "GBP", "$": "USD", "€": "EUR"}
    return [
        symbol_map.get(match.group(0), match.group(0).upper())
        for match in _CURRENCY_PATTERN.finditer(evidence)
    ]


def _document_type_candidates(evidence: str) -> List[FieldValue]:
    values: List[FieldValue] = []
    if re.search(r"\bcapital\s+call\b", evidence, re.I):
        values.append("CAPITAL_CALL")
    if re.search(r"\bdistribution(?:\s+notice)?\b", evidence, re.I):
        values.append("DISTRIBUTION_NOTICE")
    if re.search(r"\bunknown\b", evidence, re.I):
        values.append("UNKNOWN")
    return values


def _text_candidates(fragments: Sequence[str]) -> List[FieldValue]:
    values: List[FieldValue] = []
    for fragment in fragments:
        for candidate in re.split(r"\s+or\s+", fragment, flags=re.I):
            cleaned = candidate.strip(" \t\r\n.:;,\"'")
            if cleaned:
                values.append(cleaned)
    return values


def _evidence_candidates(field: str, evidence: str) -> _CandidateSet:
    labelled = _labelled_fragments(field, evidence)
    fragments = labelled or [evidence]
    relevant = bool(labelled)

    if field in _MONEY_FIELDS:
        values = _money_candidates(fragments)
    elif field in _DATE_FIELDS:
        values = _date_candidates(fragments)
    elif field == "currency":
        values = _currency_candidates(evidence)
        relevant = bool(values)
    elif field == "document_type":
        values = _document_type_candidates(evidence)
        relevant = bool(values)
    else:
        values = _text_candidates(fragments) if labelled else []

    return _CandidateSet(values=_unique_values(field, values), relevant=relevant)


def _evidence_mentions_value(
    field: str,
    evidence: str,
    value: FieldValue,
) -> bool:
    """Check the minimum grounding required for a model's SUPPORTED result."""

    if value is None:
        return bool(_MISSING_MARKER.search(evidence))
    candidates = _evidence_candidates(field, evidence)
    if not candidates.relevant:
        return False
    if any(_values_equal(field, candidate, value) for candidate in candidates.values):
        return True
    fragments = _labelled_fragments(field, evidence)
    if field == "bank_account_reference":
        expected = _normalize_reference(value)
        return bool(expected) and any(
            expected in _normalize_reference(fragment) for fragment in fragments
        )
    if field not in _MONEY_FIELDS | _DATE_FIELDS | {"currency", "document_type"}:
        expected = _normalize_text(value)
        return bool(expected) and any(
            re.search(
                rf"(?<!\w){re.escape(expected)}(?!\w)",
                _normalize_text(fragment),
            )
            is not None
            for fragment in fragments
        )
    return False


def _field_evidence_has_negation(field: str, evidence: str) -> bool:
    """Detect explicit rejection language in the target field's clause."""

    aliases = _FIELD_ALIASES.get(field, (field.replace("_", " "),))
    alias_expression = "|".join(
        re.escape(alias).replace(r"\ ", r"\s+")
        for alias in sorted(aliases, key=len, reverse=True)
    )
    label_pattern = re.compile(
        rf"(?<!\w)(?:{alias_expression})(?!\w)",
        re.I,
    )
    segments = re.split(r"(?:\r?\n|;|\|)", evidence)
    relevant_segments = [segment for segment in segments if label_pattern.search(segment)]
    if not relevant_segments and field in {"currency", "document_type"}:
        relevant_segments = [evidence]
    return any(
        _NEGATION_PATTERN.search(_BENIGN_NEGATION_PATTERN.sub("", segment))
        is not None
        for segment in relevant_segments
    )


class DeterministicEvidenceReviewer:
    """Offline fixture that independently re-parses the supplied evidence.

    The fixture never consults the reconciliation status when deciding whether
    evidence supports the extraction.  Reconciliation affects only the later,
    deterministic human-escalation flag.
    """

    review_method = ReviewMethod.DETERMINISTIC_FIXTURE

    def assess(self, review_request: EvidenceReviewRequest) -> ReviewAssessment:
        evidence = (review_request.source_evidence or "").strip()
        if not evidence:
            return ReviewAssessment(
                status=ReviewStatus.INSUFFICIENT_EVIDENCE,
                review_reason="No source evidence was supplied for this extracted field.",
            )

        candidates = _evidence_candidates(review_request.field, evidence)
        if not candidates.relevant:
            return ReviewAssessment(
                status=ReviewStatus.INSUFFICIENT_EVIDENCE,
                review_reason=(
                    "The supplied snippet does not identify this field clearly enough "
                    "to verify the extracted value."
                ),
            )

        if review_request.extracted_value is None:
            if not candidates.values and _MISSING_MARKER.search(evidence):
                return ReviewAssessment(
                    status=ReviewStatus.SUPPORTED,
                    review_reason=(
                        "The field-specific evidence explicitly states that no value applies."
                    ),
                    confidence=1.0,
                )
            return ReviewAssessment(
                status=ReviewStatus.INSUFFICIENT_EVIDENCE,
                review_reason=(
                    "A missing extracted value cannot be verified from the supplied evidence."
                ),
            )

        if _field_evidence_has_negation(review_request.field, evidence):
            return ReviewAssessment(
                status=ReviewStatus.CHALLENGE,
                review_reason=(
                    "The field-specific evidence explicitly negates or rejects "
                    "the extracted value."
                ),
            )

        if not candidates.values:
            return ReviewAssessment(
                status=ReviewStatus.INSUFFICIENT_EVIDENCE,
                review_reason=(
                    "The evidence is field-specific but contains no verifiable candidate value."
                ),
            )

        matching = [
            value
            for value in candidates.values
            if _values_equal(review_request.field, value, review_request.extracted_value)
        ]
        alternatives = [
            value
            for value in candidates.values
            if not _values_equal(review_request.field, value, review_request.extracted_value)
        ]

        if matching and not alternatives:
            return ReviewAssessment(
                status=ReviewStatus.SUPPORTED,
                review_reason=(
                    "The field-specific source evidence directly supports the extracted value."
                ),
                confidence=1.0,
            )

        if len(candidates.values) == 1 and not matching:
            return ReviewAssessment(
                status=ReviewStatus.CHALLENGE,
                review_reason=(
                    "The field-specific source evidence directly supports a different value."
                ),
                challenged_value=candidates.values[0],
                confidence=1.0,
            )

        challenged_value = alternatives[0] if len(alternatives) == 1 else None
        return ReviewAssessment(
            status=ReviewStatus.CHALLENGE,
            review_reason=(
                "The field-specific evidence contains multiple materially plausible values "
                "and does not unambiguously support the extraction."
            ),
            challenged_value=challenged_value,
        )


class _ModelReviewStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    CHALLENGE = "CHALLENGE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class _ModelReviewOutput(DomainModel):
    status: _ModelReviewStatus
    review_reason: str = Field(min_length=1)
    # OpenAI strict structured output requires every property to be required.
    # These fields remain nullable, but the model must explicitly return null.
    challenged_value: FieldValue
    confidence: Optional[float] = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def enforce_status_shape(self) -> "_ModelReviewOutput":
        if self.status is not _ModelReviewStatus.CHALLENGE and self.challenged_value is not None:
            raise ValueError("challenged_value is only valid for CHALLENGE")
        return self


_MODEL_SYSTEM_PROMPT = """You are an independent evidence verifier in a fund-operations control workflow.
Review exactly one extracted field using only the supplied evidence and provenance.

Your defined job:
1. Decide whether the evidence directly supports the extracted value.
2. Identify a materially plausible alternative interpretation.
3. Mark absent, irrelevant, or too-weak evidence as insufficient.

Use SUPPORTED only when the cited evidence unambiguously supports the extracted value.
Use CHALLENGE when the evidence supports a different or competing interpretation. Include
challenged_value only when that exact value appears in the supplied evidence.
Use INSUFFICIENT_EVIDENCE when the evidence cannot establish a value.

The deterministic reconciliation result is context, not a suggested answer. Never change or
approve the extracted value or canonical record. Do not infer missing document content. Return
only the requested structured object. NOT_REVIEWED is reserved for application/provider failure.
"""


class OpenAICompatibleEvidenceReviewer:
    """Strict structured model reviewer using the project's transport convention."""

    review_method = ReviewMethod.OPENAI_COMPATIBLE

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 12.0,
        transport: Optional[LLMTransport] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = (
            base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        self.timeout = timeout
        self.transport = transport

    @property
    def available(self) -> bool:
        return self.transport is not None or bool(self.api_key)

    @classmethod
    def response_schema(cls) -> Dict[str, Any]:
        """Expose the exact strict schema sent to compatible providers."""

        return _ModelReviewOutput.model_json_schema()

    def _request_model(self, payload_json: str) -> Union[str, Mapping[str, Any]]:
        if self.transport is not None:
            return self.transport(
                f"{_MODEL_SYSTEM_PROMPT}\n\nREVIEW_INPUT_JSON:\n{payload_json}"
            )
        if not self.api_key:
            raise RuntimeError("model reviewer is unavailable")

        request_body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "independent_evidence_review",
                        "strict": True,
                        "schema": self.response_schema(),
                    },
                },
                "messages": [
                    {"role": "system", "content": _MODEL_SYSTEM_PROMPT},
                    {"role": "user", "content": payload_json},
                ],
            }
        ).encode("utf-8")
        http_request = urllib_request.Request(
            f"{self.base_url}/chat/completions",
            data=request_body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib_request.urlopen(http_request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]

    @staticmethod
    def _decode_payload(payload: Union[str, Mapping[str, Any]]) -> Mapping[str, Any]:
        if isinstance(payload, Mapping):
            return payload
        cleaned = payload.strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
        decoded = json.loads(cleaned)
        if not isinstance(decoded, Mapping):
            raise ValueError("structured review must return a JSON object")
        return decoded

    def assess(self, review_request: EvidenceReviewRequest) -> ReviewAssessment:
        if not (review_request.source_evidence or "").strip():
            return ReviewAssessment(
                status=ReviewStatus.INSUFFICIENT_EVIDENCE,
                review_reason=(
                    "No source evidence was supplied for this extracted field."
                ),
            )
        if not self.available:
            return ReviewAssessment(
                status=ReviewStatus.NOT_REVIEWED,
                review_reason=(
                    "Model reviewer unavailable; no independent model review was completed."
                ),
            )

        payload_json = json.dumps(
            review_request.model_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            raw_response = self._request_model(payload_json)
            decoded = dict(self._decode_payload(raw_response))
            # Injected transports may omit semantically optional values. The
            # real strict wire schema still requires explicit nulls.
            decoded.setdefault("challenged_value", None)
            decoded.setdefault("confidence", None)
            structured = _ModelReviewOutput.model_validate(decoded)
        except Exception:
            return ReviewAssessment(
                status=ReviewStatus.NOT_REVIEWED,
                review_reason=(
                    "Model reviewer failed or returned invalid structured output; "
                    "no independent model review was completed."
                ),
            )

        if (
            structured.status is _ModelReviewStatus.SUPPORTED
            and _field_evidence_has_negation(
                review_request.field, review_request.source_evidence
            )
        ):
            return ReviewAssessment(
                status=ReviewStatus.CHALLENGE,
                review_reason=(
                    "The supplied evidence explicitly negates the extracted value, "
                    "so the model's support assessment was not accepted."
                ),
            )

        if (
            structured.status is _ModelReviewStatus.SUPPORTED
            and not _evidence_mentions_value(
                review_request.field,
                review_request.source_evidence,
                review_request.extracted_value,
            )
        ):
            return ReviewAssessment(
                status=ReviewStatus.INSUFFICIENT_EVIDENCE,
                review_reason=(
                    "The supplied evidence does not contain the extracted value, "
                    "so the model's support assessment was not accepted."
                ),
            )

        if structured.status is _ModelReviewStatus.SUPPORTED:
            candidates = _evidence_candidates(
                review_request.field, review_request.source_evidence
            )
            alternatives = [
                value
                for value in candidates.values
                if not _values_equal(
                    review_request.field,
                    value,
                    review_request.extracted_value,
                )
            ]
            if alternatives:
                return ReviewAssessment(
                    status=ReviewStatus.CHALLENGE,
                    review_reason=(
                        "The supplied evidence contains a materially competing value, "
                        "so the model's unqualified support assessment was not accepted."
                    ),
                    challenged_value=(
                        alternatives[0] if len(alternatives) == 1 else None
                    ),
                )

        challenged_value: FieldValue = None
        if (
            structured.status is _ModelReviewStatus.CHALLENGE
            and structured.challenged_value is not None
            and review_request.source_evidence
        ):
            candidates = _evidence_candidates(
                review_request.field, review_request.source_evidence
            )
            if candidates.relevant:
                challenged_value = next(
                    (
                        value
                        for value in candidates.values
                        if _values_equal(
                            review_request.field,
                            value,
                            structured.challenged_value,
                        )
                        and not _values_equal(
                            review_request.field,
                            value,
                            review_request.extracted_value,
                        )
                    ),
                    None,
                )

        return ReviewAssessment(
            status=ReviewStatus(structured.status.value),
            review_reason=structured.review_reason,
            challenged_value=challenged_value,
            confidence=structured.confidence,
        )


def build_review_request(
    report: ReconciliationReport,
    item: ReconciliationItem,
) -> EvidenceReviewRequest:
    """Create one allowlisted review request from deterministic output."""

    report = ReconciliationReport.model_validate(report)
    item = ReconciliationItem.model_validate(item)
    provenance = item.provenance
    source = provenance.source if provenance is not None else report.source_document
    return EvidenceReviewRequest(
        case_id=report.case_id,
        source_document=report.source_document,
        field=item.field,
        extracted_value=item.observed,
        source_evidence=provenance.evidence if provenance is not None else None,
        provenance=ReviewProvenance(
            source=source,
            source_type=provenance.source_type if provenance is not None else None,
            page=provenance.page if provenance is not None else None,
            sheet=provenance.sheet if provenance is not None else None,
            cell=provenance.cell if provenance is not None else None,
            extraction_confidence=(
                provenance.confidence if provenance is not None else None
            ),
            extraction_method=provenance.method if provenance is not None else None,
            extractor=provenance.extractor if provenance is not None else None,
            extraction_timestamp=(
                provenance.timestamp if provenance is not None else None
            ),
            abstention_reason=(
                provenance.abstention_reason if provenance is not None else None
            ),
        ),
        reconciliation=ReconciliationSnapshot(
            status=item.status,
            severity=item.severity,
            expected=item.expected,
            observed=item.observed,
            difference=item.difference,
            explanation=item.explanation,
        ),
    )


def review_item(
    report: ReconciliationReport,
    item: ReconciliationItem,
    reviewer: EvidenceReviewer,
) -> ReviewFinding:
    """Review one item and create a finding without mutating either input."""

    report = ReconciliationReport.model_validate(report)
    item = ReconciliationItem.model_validate(item)
    review_request = build_review_request(report, item)
    if (
        item.status is ReconciliationStatus.PASS
        and item.expected is None
        and item.observed is None
    ):
        # This is a local workflow rule, not evidence-reviewer output. Keeping
        # the provenance distinct prevents model mode from claiming a provider
        # reviewed a field when no provider call was made.
        review_method = ReviewMethod.LOCAL_POLICY
        assessment = ReviewAssessment(
            status=ReviewStatus.SUPPORTED,
            review_reason=(
                "No value is expected or observed, so this optional field is not "
                "applicable and needs no source evidence."
            ),
        )
    else:
        try:
            review_method = ReviewMethod(reviewer.review_method)
            assessment = reviewer.assess(review_request)
            assessment = ReviewAssessment.model_validate(assessment)
        except Exception:
            try:
                review_method = ReviewMethod(reviewer.review_method)
            except Exception:
                review_method = ReviewMethod.UNAVAILABLE
            assessment = ReviewAssessment(
                status=ReviewStatus.NOT_REVIEWED,
                review_reason=(
                    "Independent reviewer failed; no evidence review was completed."
                ),
            )

    references = [
        ReviewSourceReference(
            source=review_request.provenance.source,
            source_type=review_request.provenance.source_type,
            page=review_request.provenance.page,
            sheet=review_request.provenance.sheet,
            cell=review_request.provenance.cell,
            evidence=review_request.source_evidence,
        )
    ]
    requires_human_review = (
        item.status is not ReconciliationStatus.PASS
        or assessment.status is not ReviewStatus.SUPPORTED
    )
    return ReviewFinding(
        case_id=report.case_id,
        source_document=report.source_document,
        field=item.field,
        reviewed_value=item.observed,
        status=assessment.status,
        review_reason=assessment.review_reason,
        challenged_value=assessment.challenged_value,
        confidence=assessment.confidence,
        source_references=references,
        reconciliation_status=item.status,
        requires_human_review=requires_human_review,
        review_method=review_method,
    )


def review_reconciliation(
    report: ReconciliationReport,
    reviewer: Optional[EvidenceReviewer] = None,
) -> ReviewReport:
    """Run independent review after reconciliation and before human approval.

    Every reconciliation row is reviewed, including ``PASS`` rows.  This is
    necessary for the reviewer to catch a mutually consistent fund record and
    extraction that is nevertheless unsupported by the cited evidence.
    """

    report = ReconciliationReport.model_validate(report)
    selected_reviewer = reviewer or DeterministicEvidenceReviewer()
    findings = [
        review_item(report, item, selected_reviewer) for item in report.results
    ]
    counts = {status.value: 0 for status in ReviewStatus}
    for finding in findings:
        counts[finding.status.value] += 1
    return ReviewReport(
        case_id=report.case_id,
        source_document=report.source_document,
        findings=findings,
        counts=counts,
    )


# Demo-friendly aliases that make the offline and model modes easy to discover.
OfflineEvidenceReviewer = DeterministicEvidenceReviewer
ModelEvidenceReviewer = OpenAICompatibleEvidenceReviewer


__all__ = [
    "DeterministicEvidenceReviewer",
    "EvidenceReviewRequest",
    "EvidenceReviewer",
    "ModelEvidenceReviewer",
    "OfflineEvidenceReviewer",
    "OpenAICompatibleEvidenceReviewer",
    "ReconciliationSnapshot",
    "ReviewAssessment",
    "ReviewFinding",
    "ReviewMethod",
    "ReviewProvenance",
    "ReviewReport",
    "ReviewSourceReference",
    "ReviewStatus",
    "build_review_request",
    "review_item",
    "review_reconciliation",
]
