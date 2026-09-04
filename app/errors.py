"""Public, sanitized failure contracts for the document workflow.

The application should preserve the original exception for debugging through
exception chaining, but it must never return that exception's message to a user
or write it to a structured workflow log.  ``WorkflowError`` therefore derives
all public fields from a fixed catalogue rather than accepting free-form text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus
from typing import Dict, Mapping, Union
from uuid import UUID


class WorkflowStage(str, Enum):
    """Known workflow boundaries used in errors and telemetry."""

    REQUEST = "request"
    FILE_VALIDATION = "file_validation"
    TEMP_FILE_WRITE = "temp_file_write"
    PDF_PARSING = "pdf_parsing"
    XLSX_PARSING = "xlsx_parsing"
    DETERMINISTIC_EXTRACTION = "deterministic_extraction"
    AI_EXTRACTION = "ai_extraction"
    RECONCILIATION = "reconciliation"
    AUDIT_APPEND = "audit_append"
    HEALTH_CHECK = "health_check"
    COMPLETE = "complete"


class WorkflowErrorCode(str, Enum):
    """Stable machine-readable codes for expected failure modes."""

    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    FILE_TOO_LARGE = "file_too_large"
    EMPTY_FILE = "empty_file"
    MALFORMED_TEXT = "malformed_text"
    MALFORMED_PDF = "malformed_pdf"
    MALFORMED_XLSX = "malformed_xlsx"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    AI_UNAVAILABLE = "ai_unavailable"
    AI_INVALID_RESPONSE = "ai_invalid_response"
    AI_TIMEOUT = "ai_timeout"
    DUPLICATE_RECORD = "duplicate_record"
    AUDIT_WRITE_FAILED = "audit_write_failed"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class _ErrorSpec:
    status: HTTPStatus
    message: str
    retryable: bool


_ERROR_SPECS: Mapping[WorkflowErrorCode, _ErrorSpec] = {
    WorkflowErrorCode.UNSUPPORTED_FILE_TYPE: _ErrorSpec(
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        "This file type is not supported. Upload one of the allowed document types.",
        False,
    ),
    WorkflowErrorCode.FILE_TOO_LARGE: _ErrorSpec(
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        "This file is larger than the supported upload limit.",
        False,
    ),
    WorkflowErrorCode.EMPTY_FILE: _ErrorSpec(
        HTTPStatus.BAD_REQUEST,
        "The uploaded file is empty.",
        False,
    ),
    WorkflowErrorCode.MALFORMED_TEXT: _ErrorSpec(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "The text file could not be read as UTF-8. Check the file and try another copy.",
        False,
    ),
    WorkflowErrorCode.MALFORMED_PDF: _ErrorSpec(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "The PDF could not be read. Check the file and try another copy.",
        False,
    ),
    WorkflowErrorCode.MALFORMED_XLSX: _ErrorSpec(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "The spreadsheet could not be read. Check the file and try another copy.",
        False,
    ),
    WorkflowErrorCode.MISSING_REQUIRED_FIELD: _ErrorSpec(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "A required field is missing. Review the highlighted fields before continuing.",
        False,
    ),
    WorkflowErrorCode.AI_UNAVAILABLE: _ErrorSpec(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "AI extraction is temporarily unavailable. Deterministic extraction can still be used.",
        True,
    ),
    WorkflowErrorCode.AI_INVALID_RESPONSE: _ErrorSpec(
        HTTPStatus.BAD_GATEWAY,
        "AI extraction returned an invalid result. Deterministic extraction can still be used.",
        True,
    ),
    WorkflowErrorCode.AI_TIMEOUT: _ErrorSpec(
        HTTPStatus.GATEWAY_TIMEOUT,
        "AI extraction timed out. Try again or use deterministic extraction.",
        True,
    ),
    WorkflowErrorCode.DUPLICATE_RECORD: _ErrorSpec(
        HTTPStatus.CONFLICT,
        "More than one matching record was found. Select or resolve the record before continuing.",
        False,
    ),
    WorkflowErrorCode.AUDIT_WRITE_FAILED: _ErrorSpec(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "The review decision could not be recorded. No change was saved; please try again.",
        True,
    ),
    WorkflowErrorCode.INTERNAL_ERROR: _ErrorSpec(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "The workflow could not be completed. Try again and use the reference ID if the problem continues.",
        True,
    ),
}


def validate_request_id(value: str) -> str:
    """Return a canonical UUID, rejecting arbitrary text in correlation fields."""

    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("request_id must be a valid UUID") from exc
    return str(parsed)


def validate_stage(value: Union[WorkflowStage, str]) -> WorkflowStage:
    """Return a known workflow stage, never user- or document-derived text."""

    try:
        return value if isinstance(value, WorkflowStage) else WorkflowStage(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("stage must be a known workflow stage") from exc


class WorkflowError(Exception):
    """An expected workflow failure safe to display or serialize.

    Free-form exception messages are deliberately not accepted.  Callers may
    chain the original exception (``raise WorkflowError(...) from exc``) while
    presenting only :meth:`to_response` to the user.
    """

    def __init__(
        self,
        code: WorkflowErrorCode,
        *,
        request_id: str,
        stage: Union[WorkflowStage, str],
    ) -> None:
        if not isinstance(code, WorkflowErrorCode):
            try:
                code = WorkflowErrorCode(code)
            except (TypeError, ValueError) as exc:
                raise ValueError("code must be a known workflow error code") from exc

        spec = _ERROR_SPECS[code]
        self.code = code
        self.status = int(spec.status)
        self.request_id = validate_request_id(request_id)
        self.stage = validate_stage(stage)
        self.retryable = spec.retryable
        self.public_message = spec.message
        super().__init__(self.public_message)

    @property
    def status_code(self) -> int:
        """HTTP-compatible status for a future API boundary."""

        return self.status

    def to_dict(self) -> Dict[str, object]:
        """Return the flat, public error body."""

        return {
            "code": self.code.value,
            "status": self.status,
            "message": self.public_message,
            "request_id": self.request_id,
            "stage": self.stage.value,
            "retryable": self.retryable,
        }

    def to_response(self) -> Dict[str, Dict[str, object]]:
        """Return a conventional structured error response envelope."""

        return {"error": self.to_dict()}


def sanitize_exception(
    error: BaseException,
    *,
    request_id: str,
    stage: Union[WorkflowStage, str],
) -> WorkflowError:
    """Convert an unexpected exception without copying any sensitive detail."""

    if isinstance(error, WorkflowError):
        return error
    return WorkflowError(
        WorkflowErrorCode.INTERNAL_ERROR,
        request_id=request_id,
        stage=stage,
    )
