from __future__ import annotations

import json
import logging
from uuid import uuid4

import pytest

from app.errors import WorkflowError, WorkflowErrorCode, WorkflowStage
from app.observability import log_workflow_event, observe_workflow_stage


def _messages(caplog, logger_name):
    return [record.getMessage() for record in caplog.records if record.name == logger_name]


def test_observed_stage_emits_whitelisted_success_event(caplog, monkeypatch):
    logger_name = "test.fundops.success"
    logger = logging.getLogger(logger_name)
    caplog.set_level(logging.INFO, logger=logger_name)
    ticks = iter((10.0, 10.125))
    monkeypatch.setattr("app.observability.time.perf_counter", lambda: next(ticks))
    request_id = str(uuid4())

    with observe_workflow_stage(
        request_id,
        WorkflowStage.RECONCILIATION,
        logger=logger,
    ):
        pass

    payload = json.loads(_messages(caplog, logger_name)[-1])
    assert payload["request_id"] == request_id
    assert payload["workflow_stage"] == "reconciliation"
    assert payload["duration_ms"] == 125.0
    assert payload["success"] is True
    assert payload["outcome"] == "success"
    assert "error_code" not in payload
    assert set(payload) == {
        "timestamp",
        "event",
        "request_id",
        "workflow_stage",
        "duration_ms",
        "success",
        "outcome",
    }


def test_observed_stage_logs_failure_without_exception_or_secret(caplog, monkeypatch):
    logger_name = "test.fundops.failure"
    logger = logging.getLogger(logger_name)
    caplog.set_level(logging.ERROR, logger=logger_name)
    ticks = iter((20.0, 20.050))
    monkeypatch.setattr("app.observability.time.perf_counter", lambda: next(ticks))
    request_id = str(uuid4())
    secret = "OPENAI_API_KEY=sk-private full source document"

    with pytest.raises(RuntimeError, match="sk-private"):
        with observe_workflow_stage(
            request_id,
            WorkflowStage.AI_EXTRACTION,
            logger=logger,
        ):
            raise RuntimeError(secret)

    serialized = _messages(caplog, logger_name)[-1]
    payload = json.loads(serialized)
    assert payload["request_id"] == request_id
    assert payload["workflow_stage"] == "ai_extraction"
    assert payload["duration_ms"] == 50.0
    assert payload["success"] is False
    assert payload["outcome"] == "failure"
    assert payload["error_code"] == "internal_error"
    assert secret not in serialized
    assert "sk-private" not in serialized


def test_known_failure_logs_only_stable_error_code(caplog, monkeypatch):
    logger_name = "test.fundops.known_failure"
    logger = logging.getLogger(logger_name)
    caplog.set_level(logging.ERROR, logger=logger_name)
    ticks = iter((30.0, 30.001))
    monkeypatch.setattr("app.observability.time.perf_counter", lambda: next(ticks))
    request_id = str(uuid4())

    with pytest.raises(WorkflowError):
        with observe_workflow_stage(
            request_id,
            WorkflowStage.PDF_PARSING,
            logger=logger,
        ):
            raise WorkflowError(
                WorkflowErrorCode.MALFORMED_PDF,
                request_id=request_id,
                stage=WorkflowStage.PDF_PARSING,
            )

    payload = json.loads(_messages(caplog, logger_name)[-1])
    assert payload["error_code"] == "malformed_pdf"
    assert payload["duration_ms"] == 1.0


def test_direct_log_call_rejects_unstructured_values():
    request_id = str(uuid4())

    with pytest.raises(ValueError, match="known workflow stage"):
        log_workflow_event(
            request_id=request_id,
            workflow_stage="source document text",
            duration_ms=1,
            success=True,
        )

    with pytest.raises(ValueError, match="known workflow error code"):
        log_workflow_event(
            request_id=request_id,
            workflow_stage=WorkflowStage.REQUEST,
            duration_ms=1,
            success=False,
            error_code="provider said API key sk-secret",
        )


def test_independent_review_stage_uses_the_same_closed_log_contract(caplog):
    logger_name = "test.fundops.independent_review"
    logger = logging.getLogger(logger_name)
    caplog.set_level(logging.INFO, logger=logger_name)
    request_id = str(uuid4())

    with observe_workflow_stage(
        request_id,
        WorkflowStage.INDEPENDENT_REVIEW,
        logger=logger,
    ):
        pass

    payload = json.loads(_messages(caplog, logger_name)[-1])
    assert payload["request_id"] == request_id
    assert payload["workflow_stage"] == "independent_review"
    assert payload["success"] is True
    assert set(payload) == {
        "timestamp",
        "event",
        "request_id",
        "workflow_stage",
        "duration_ms",
        "success",
        "outcome",
    }
