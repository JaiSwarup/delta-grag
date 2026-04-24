from __future__ import annotations

import asyncio

from src.baselines.diff_only_reviewer import (
    build_diff_only_prompt,
    diff_only_review,
    truncate_to_token_budget,
)
from src.llm_caller import LLMCallerConfig
from src.token_budget import estimate_token_count


class _AsyncStubProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def generate(
        self,
        *,
        prompt: str,
        model_name: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        self.prompts.append(prompt)
        return self.response


def test_build_diff_only_prompt_contains_diff_and_schema() -> None:
    prompt = build_diff_only_prompt(
        diff_text="@@ -1 +1 @@\n-old\n+new",
        pr_metadata={"pr_id": 7, "title": "Change behavior"},
    )

    assert "Review only the unified diff" in prompt
    assert "Return ONLY valid JSON" in prompt
    assert "PR ID: 7" in prompt
    assert "@@ -1 +1 @@" in prompt


def test_truncate_to_token_budget_marks_omitted_lines() -> None:
    diff = "\n".join(f"+line_{idx} = {idx}" for idx in range(100))

    truncated, was_truncated = truncate_to_token_budget(diff, max_tokens=30)

    assert was_truncated is True
    assert "[TRUNCATED -" in truncated
    assert estimate_token_count(truncated) <= 30


def test_diff_only_review_invokes_provider_and_parses_review() -> None:
    provider = _AsyncStubProvider(
        '{"findings":[{"category":"correctness","severity":"medium","confidence":0.6,'
        '"summary":"Check edge case","technical_reasoning":"Diff changes behavior",'
        '"suggested_fix":"Add a test","evidence":[{"node_id":"diff","file_path":"a.py",'
        '"start_line":1,"end_line":1}]}],"overall_risk":"medium"}'
    )
    config = LLMCallerConfig(
        model_name="stub-model",
        max_attempts=1,
        strict_schema=True,
    )

    result = asyncio.run(
        diff_only_review(
            diff_text="@@ -1 +1 @@\n-old\n+new",
            pr_metadata={"title": "Change behavior"},
            provider=provider,
            config=config,
            diff_token_budget=100,
        )
    )

    assert provider.prompts
    assert result.review.review.findings[0].summary == "Check edge case"
    assert result.was_truncated is False
    assert result.total_tokens > 0


def test_diff_only_review_truncates_large_diff_before_provider_call() -> None:
    provider = _AsyncStubProvider('{"findings":[],"overall_risk":"low"}')
    config = LLMCallerConfig(
        model_name="stub-model",
        max_attempts=1,
        strict_schema=True,
    )
    large_diff = "\n".join(f"+line_{idx} = {idx}" for idx in range(100))

    result = asyncio.run(
        diff_only_review(
            diff_text=large_diff,
            pr_metadata={},
            provider=provider,
            config=config,
            diff_token_budget=30,
        )
    )

    assert result.was_truncated is True
    assert "[TRUNCATED -" in result.truncated_diff
    assert result.truncated_diff in provider.prompts[0]
