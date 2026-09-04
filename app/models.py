"""Typed contracts for extraction, reconciliation, and human review."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentType(str, Enum):
    CAPITAL_CALL = "CAPITAL_CALL"
    DISTRIBUTION_NOTICE = "DISTRIBUTION_NOTICE"
    UNKNOWN = "UNKNOWN"


class ExtractionMethod(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    FALLBACK = "FALLBACK"


class ReconciliationStatus(str, Enum):
    PASS = "PASS"
    MISMATCH = "MISMATCH"
    MISSING = "MISSING"
    REVIEW = "REVIEW"


class Severity(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReviewDecision(str, Enum):
    APPROVED = "APPROVED"
    NEEDS_INVESTIGATION = "NEEDS_INVESTIGATION"
    REJECTED = "REJECTED"


# Runtime aliases use typing.Union so the app remains compatible with Python 3.9.
FieldValue = Union[Decimal, date, str, None]
DifferenceValue = Union[Decimal, int, None]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class FundRecord(DomainModel):
    """Canonical values already held in the fund operations system."""

    case_id: str = Field(min_length=1)
    fund_name: str = Field(min_length=1)
    investor_name: str = Field(min_length=1)
    commitment_amount: Decimal = Field(ge=0)
    capital_call_amount: Decimal = Field(ge=0)
    call_date: date
    due_date: date
    currency: str = Field(min_length=3, max_length=3)
    bank_account_reference: Optional[str] = None
    management_fee: Optional[Decimal] = Field(default=None, ge=0)
    document_type: DocumentType = DocumentType.CAPITAL_CALL

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("currency must be a three-letter code")
        return normalized

    def reconciliation_values(self) -> Dict[str, FieldValue]:
        """Return canonical fields in a stable, table-friendly order."""

        return {
            "fund_name": self.fund_name,
            "investor_name": self.investor_name,
            "commitment_amount": self.commitment_amount,
            "capital_call_amount": self.capital_call_amount,
            "call_date": self.call_date,
            "due_date": self.due_date,
            "currency": self.currency,
            "bank_account_reference": self.bank_account_reference,
            "management_fee": self.management_fee,
            "document_type": self.document_type.value,
        }


class ExtractedField(DomainModel):
    """One extracted value and the evidence needed to audit it."""

    value: FieldValue = None
    source: str = Field(min_length=1)
    page: Optional[int] = Field(default=None, ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: Optional[str] = None
    method: ExtractionMethod = ExtractionMethod.DETERMINISTIC


class ExtractedDocument(DomainModel):
    """Normalized extraction result from one uploaded document."""

    case_id: str = Field(min_length=1)
    source_document: str = Field(min_length=1)
    document_type: DocumentType = DocumentType.UNKNOWN
    fields: Dict[str, ExtractedField] = Field(default_factory=dict)
    extraction_method: ExtractionMethod = ExtractionMethod.DETERMINISTIC
    warnings: List[str] = Field(default_factory=list)

    def value_for(self, field_name: str) -> FieldValue:
        extracted = self.fields.get(field_name)
        if extracted is not None:
            return extracted.value
        if field_name == "document_type":
            return self.document_type.value
        return None


class ReconciliationItem(DomainModel):
    """One deterministic expected-versus-observed comparison."""

    field: str
    expected: FieldValue = None
    observed: FieldValue = None
    status: ReconciliationStatus
    severity: Severity
    difference: DifferenceValue = None
    explanation: str
    provenance: Optional[ExtractedField] = None


ReconciliationResult = ReconciliationItem


class ReconciliationReport(DomainModel):
    case_id: str
    source_document: str
    document_type: DocumentType
    overall_status: ReconciliationStatus
    results: List[ReconciliationItem]
    counts: Dict[str, int]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def exceptions(self) -> List[ReconciliationItem]:
        return [
            result
            for result in self.results
            if result.status is not ReconciliationStatus.PASS
        ]


class AuditEvent(DomainModel):
    """One immutable human decision about a reconciliation exception."""

    id: Optional[int] = None
    case_id: str = Field(min_length=1)
    document_id: str = Field(default="unspecified", min_length=1)
    source_document: Optional[str] = None
    field: str = Field(min_length=1)
    decision: ReviewDecision
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: Optional[str] = None
    actor: str = Field(default="Reviewer", min_length=1)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value
