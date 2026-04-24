from __future__ import annotations

import asyncio

from src.llm_caller import LLMCallError, LLMCallerConfig, call_llm_json


class _AsyncStubProvider:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def generate(
        self,
        *,
        prompt: str,
        model_name: str,
        temperature: float,
        max_output_tokens: int,
    ):
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_call_llm_json_parses_mapping_response_with_telemetry() -> None:
    provider = _AsyncStubProvider(
        [
            {
                "text": '{"findings":[{"category":"correctness","severity":"high","confidence":0.8,"summary":"Bug","technical_reasoning":"Reason","suggested_fix":"Fix","evidence":[{"node_id":"n1"}]}]}',
                "tokens_used": 123,
                "metadata": {"provider": "stub"},
            }
        ]
    )
    config = LLMCallerConfig(model_name="stub-model", strict_schema=True)

    result = asyncio.run(call_llm_json(prompt="review this", provider=provider, config=config))

    assert result.telemetry.attempts == 1
    assert result.telemetry.tokens_used == 123
    assert result.telemetry.model_name == "stub-model"
    assert len(result.review.findings) == 1
    assert result.review.findings[0].summary == "Bug"


def test_call_llm_json_retries_then_succeeds() -> None:
    provider = _AsyncStubProvider(
        [
            RuntimeError("temporary failure"),
            '{"findings":[{"category":"perf","severity":"medium","confidence":0.7,"summary":"Slow path","technical_reasoning":"Reason","suggested_fix":"Fix","evidence":[{"node_id":"n2"}]}]}',
        ]
    )
    config = LLMCallerConfig(
        model_name="stub-model",
        strict_schema=True,
        max_attempts=2,
        retry_base_delay_ms=1,
    )

    result = asyncio.run(call_llm_json(prompt="review this", provider=provider, config=config))

    assert provider.calls == 2
    assert result.telemetry.attempts == 2
    assert result.review.findings[0].summary == "Slow path"


def test_call_llm_json_strict_schema_failure_raises() -> None:
    provider = _AsyncStubProvider(["not json at all"])
    config = LLMCallerConfig(
        model_name="stub-model",
        strict_schema=True,
        max_attempts=1,
    )

    try:
        asyncio.run(call_llm_json(prompt="review this", provider=provider, config=config))
        assert False, "Expected LLMCallError for invalid strict-schema output"
    except LLMCallError:
        pass


def test_call_llm_json_non_strict_schema_allows_parse_warnings() -> None:
    provider = _AsyncStubProvider(["not json at all"])
    config = LLMCallerConfig(
        model_name="stub-model",
        strict_schema=False,
        max_attempts=1,
    )

    result = asyncio.run(call_llm_json(prompt="review this", provider=provider, config=config))

    assert result.review.findings == ()
    assert result.telemetry.parse_warning_count > 0
