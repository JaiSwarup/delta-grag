"""
Async LLM caller with retry, telemetry, and strict schema parsing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Mapping, Optional, Protocol

from src.postprocess.review_types import NormalizedReview, normalize_review_output


class AsyncLLMProvider(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        model_name: str,
        temperature: float,
        max_output_tokens: int,
    ) -> Any: ...


@dataclass(frozen=True)
class LLMCallerConfig:
    model_name: str
    temperature: float = 0.1
    max_output_tokens: int = 2048
    max_attempts: int = 3
    retry_base_delay_ms: int = 250
    strict_schema: bool = True


@dataclass(frozen=True)
class LLMCallTelemetry:
    attempts: int
    latency_ms: float
    tokens_used: Optional[int]
    provider_name: str
    model_name: str
    parse_warning_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMCallResult:
    raw_text: str
    review: NormalizedReview
    telemetry: LLMCallTelemetry


class LLMCallError(RuntimeError):
    """Raised when model invocation or schema parsing cannot be completed."""


async def call_llm_json(
    *,
    prompt: str,
    provider: AsyncLLMProvider,
    config: LLMCallerConfig,
) -> LLMCallResult:
    if not prompt or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if config.max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if config.retry_base_delay_ms < 0:
        raise ValueError("retry_base_delay_ms must be >= 0")

    started_at = perf_counter()
    last_error: Exception | None = None

    for attempt in range(1, config.max_attempts + 1):
        try:
            response = await provider.generate(
                prompt=prompt,
                model_name=config.model_name,
                temperature=config.temperature,
                max_output_tokens=config.max_output_tokens,
            )
            raw_text, tokens_used, provider_metadata = _normalize_provider_response(
                response
            )
            review = normalize_review_output(raw_text)
            if config.strict_schema and review.warnings:
                raise LLMCallError(
                    "Schema validation failed: " + "; ".join(review.warnings)
                )

            latency_ms = (perf_counter() - started_at) * 1000.0
            telemetry = LLMCallTelemetry(
                attempts=attempt,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
                provider_name=type(provider).__name__,
                model_name=config.model_name,
                parse_warning_count=len(review.warnings),
                metadata=provider_metadata,
            )
            return LLMCallResult(
                raw_text=raw_text,
                review=review,
                telemetry=telemetry,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= config.max_attempts:
                break
            await asyncio.sleep(
                (config.retry_base_delay_ms * (2 ** (attempt - 1))) / 1000.0
            )

    raise LLMCallError(str(last_error) if last_error else "LLM call failed")


def _normalize_provider_response(
    response: Any,
) -> tuple[str, Optional[int], Mapping[str, Any]]:
    if isinstance(response, str):
        return response, None, {}

    if isinstance(response, Mapping):
        raw_text = response.get("text")
        if not isinstance(raw_text, str):
            raise LLMCallError(
                "Provider mapping response must include string field 'text'"
            )

        tokens_used = _coerce_optional_int(
            response.get("tokens_used") or response.get("usage", {}).get("total_tokens")
            if isinstance(response.get("usage"), Mapping)
            else response.get("tokens_used")
        )
        metadata = response.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}
        return raw_text, tokens_used, dict(metadata)

    raise LLMCallError("Unsupported provider response type")


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed >= 0 else None


__all__ = [
    "AsyncLLMProvider",
    "LLMCallerConfig",
    "LLMCallError",
    "LLMCallResult",
    "LLMCallTelemetry",
    "call_llm_json",
]
