from __future__ import annotations

from uuid import uuid4

import pytest

from app.errors import (
    WorkflowError,
    WorkflowErrorCode,
    WorkflowStage,
    sanitize_exception,
)


def test_workflow_error_has_safe_structured_fields():
    request_id = str(uuid4())
    error = WorkflowError(
        WorkflowErrorCode.AI_TIMEOUT,
        request_id=request_id,
        stage=WorkflowStage.AI_EXTRACTION,
    )

    assert error.to_response() == {
        "error": {
            "code": "ai_timeout",
            "status": 504,
            "message": "AI extraction timed out. Try again or use deterministic extraction.",
            "request_id": request_id,
            "stage": "ai_extraction",
            "retryable": True,
        }
    }
    assert error.status_code == 504


def test_unexpected_exception_details_are_not_exposed():
    request_id = str(uuid4())
    secret = "sk-live-secret and full source document text"

    public_error = sanitize_exception(
        RuntimeError(secret),
        request_id=request_id,
        stage=WorkflowStage.REQUEST,
    )
    serialized = repr(public_error.to_response())

    assert public_error.code is WorkflowErrorCode.INTERNAL_ERROR
    assert secret not in serialized
    assert "sk-live-secret" not in str(public_error)


def test_error_contract_rejects_free_form_correlation_values():
    with pytest.raises(ValueError, match="valid UUID"):
        WorkflowError(
            WorkflowErrorCode.INTERNAL_ERROR,
            request_id="a secret masquerading as an id",
            stage=WorkflowStage.REQUEST,
        )

    with pytest.raises(ValueError, match="known workflow stage"):
        WorkflowError(
            WorkflowErrorCode.INTERNAL_ERROR,
            request_id=str(uuid4()),
            stage="uploaded document contents",
        )
