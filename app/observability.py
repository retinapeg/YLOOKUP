"""Small, structured workflow logging built on the Python standard library.

Only a fixed schema is accepted.  In particular there is no free-form metadata
or exception-message field, which prevents uploaded document text, prompts,
credentials, and provider response bodies from being logged accidentally.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterator, Optional, Union
from uuid import uuid4

from .errors import (
    WorkflowError,
    WorkflowErrorCode,
    WorkflowStage,
    validate_request_id,
    validate_stage,
)


LOGGER_NAME = "fundops.workflow"
LOGGER = logging.getLogger(LOGGER_NAME)


def _configure_default_logger() -> None:
    """Install one message-only stdout handler without touching root logging."""

    if any(getattr(handler, "_fundops_json_handler", False) for handler in LOGGER.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._fundops_json_handler = True  # type: ignore[attr-defined]
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


_configure_default_logger()


def new_request_id() -> str:
    """Create an opaque correlation ID for one user-triggered workflow."""

    return str(uuid4())


def _duration(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("duration_ms must be a non-negative finite number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration_ms must be a non-negative finite number") from exc
    if numeric < 0 or not math.isfinite(numeric):
        raise ValueError("duration_ms must be a non-negative finite number")
    return round(numeric, 3)


def _error_code(
    value: Optional[Union[WorkflowErrorCode, str]],
) -> Optional[WorkflowErrorCode]:
    if value is None:
        return None
    try:
        return value if isinstance(value, WorkflowErrorCode) else WorkflowErrorCode(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("error_code must be a known workflow error code") from exc


def log_workflow_event(
    *,
    request_id: str,
    workflow_stage: Union[WorkflowStage, str],
    duration_ms: float,
    success: bool,
    error_code: Optional[Union[WorkflowErrorCode, str]] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, object]:
    """Emit one JSON event and return the exact whitelisted payload.

    The intentionally narrow signature is the redaction boundary: document
    content, filenames, prompts, exceptions, environment variables, and secrets
    cannot be supplied as log metadata.
    """

    if not isinstance(success, bool):
        raise ValueError("success must be a boolean")
    safe_request_id = validate_request_id(request_id)
    safe_stage = validate_stage(workflow_stage)
    safe_error_code = _error_code(error_code)
    if success and safe_error_code is not None:
        raise ValueError("successful events cannot include an error_code")
    if not success and safe_error_code is None:
        safe_error_code = WorkflowErrorCode.INTERNAL_ERROR

    payload: Dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": "workflow_stage_completed",
        "request_id": safe_request_id,
        "workflow_stage": safe_stage.value,
        "duration_ms": _duration(duration_ms),
        "success": success,
        "outcome": "success" if success else "failure",
    }
    if safe_error_code is not None:
        payload["error_code"] = safe_error_code.value

    target = logger or LOGGER
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if success:
        target.info(serialized)
    else:
        target.error(serialized)
    return payload


@contextmanager
def observe_workflow_stage(
    request_id: str,
    workflow_stage: Union[WorkflowStage, str],
    *,
    logger: Optional[logging.Logger] = None,
) -> Iterator[None]:
    """Measure a stage and emit a success or sanitized failure event."""

    safe_request_id = validate_request_id(request_id)
    safe_stage = validate_stage(workflow_stage)
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, WorkflowError)
            else WorkflowErrorCode.INTERNAL_ERROR
        )
        log_workflow_event(
            request_id=safe_request_id,
            workflow_stage=safe_stage,
            duration_ms=(time.perf_counter() - started) * 1000,
            success=False,
            error_code=code,
            logger=logger,
        )
        raise
    else:
        log_workflow_event(
            request_id=safe_request_id,
            workflow_stage=safe_stage,
            duration_ms=(time.perf_counter() - started) * 1000,
            success=True,
            logger=logger,
        )
