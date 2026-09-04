"""Eval-only instrumentation for the existing OpenAI-compatible extractor."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Mapping, Optional, Union
from urllib import request

from app.extraction import OpenAICompatibleExtractor


def _token_count(usage: Mapping[str, Any], *names: str) -> Optional[int]:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


class InstrumentedOpenAIExtractor(OpenAICompatibleExtractor):
    """Preserve provider usage/failure data without changing app contracts.

    Production extraction intentionally returns only a domain document.  The
    evaluator subclasses its request seam so the evaluated extraction behavior
    remains the same while operational measurements are retained.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.events = []

    def snapshot(self) -> Dict[str, int]:
        return {
            "model_calls": len(self.events),
            "successful_model_calls": sum(event["success"] for event in self.events),
            "api_failures": sum(not event["success"] for event in self.events),
            "input_tokens": sum(event.get("input_tokens") or 0 for event in self.events),
            "output_tokens": sum(event.get("output_tokens") or 0 for event in self.events),
            "total_tokens": sum(event.get("total_tokens") or 0 for event in self.events),
            "usage_events": sum(event.get("usage_available", False) for event in self.events),
        }

    def _request(self, prompt: str) -> Union[str, Mapping[str, Any]]:
        started = time.perf_counter()
        event: Dict[str, Any] = {
            "success": False,
            "error_type": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "usage_available": False,
        }
        try:
            if self.transport is not None:
                result = self.transport(prompt)
                if isinstance(result, Mapping):
                    self._capture_usage(event, result.get("usage"))
                    if "choices" in result:
                        choices = result.get("choices")
                        if not isinstance(choices, list) or not choices:
                            raise ValueError("provider response contained no choices")
                        message = choices[0].get("message", {})
                        result = message.get("content")
                        if not isinstance(result, str):
                            raise ValueError("provider response contained no message content")
            else:
                if not self.api_key:
                    raise RuntimeError("OPENAI_API_KEY is not configured")
                payload = json.dumps(
                    {
                        "model": self.model,
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Extract fields only; do not reconcile or judge them. "
                                    "Return JSON with document_type and a fields object. Each "
                                    "field must include value, page, confidence, and evidence."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                    }
                ).encode("utf-8")
                http_request = request.Request(
                    f"{self.base_url}/chat/completions",
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with request.urlopen(http_request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, Mapping):
                    raise ValueError("provider response was not an object")
                self._capture_usage(event, body.get("usage"))
                choices = body.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise ValueError("provider response contained no choices")
                message = choices[0].get("message", {})
                result = message.get("content")
                if not isinstance(result, str):
                    raise ValueError("provider response contained no message content")

            event["success"] = True
            return result
        except Exception as exc:
            # Exception messages can contain URLs, identifiers, or provider bodies.
            # Persist only the class name in evaluation artifacts.
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["latency_ms"] = (time.perf_counter() - started) * 1000
            self.events.append(event)

    @staticmethod
    def _capture_usage(event: Dict[str, Any], raw_usage: Any) -> None:
        if not isinstance(raw_usage, Mapping):
            return
        input_tokens = _token_count(raw_usage, "prompt_tokens", "input_tokens")
        output_tokens = _token_count(raw_usage, "completion_tokens", "output_tokens")
        total_tokens = _token_count(raw_usage, "total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        if any(value is not None for value in (input_tokens, output_tokens, total_tokens)):
            event.update(
                {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "usage_available": True,
                }
            )


__all__ = ["InstrumentedOpenAIExtractor"]
