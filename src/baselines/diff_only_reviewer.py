"""
Diff-only LLM review baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.llm_caller import AsyncLLMProvider, LLMCallResult, LLMCallerConfig, call_llm_json
from src.token_budget import estimate_token_count


DEFAULT_DIFF_TOKEN_BUDGET = 8_000


@dataclass(frozen=True)
class DiffOnlyReviewOutput:
    prompt: str
    truncated_diff: str
    review: LLMCallResult
    total_tokens: int
    was_truncated: bool


async def diff_only_review(
    *,
    diff_text: str,
    pr_metadata: Mapping[str, Any],
    provider: AsyncLLMProvider,
    config: LLMCallerConfig,
    diff_token_budget: int = DEFAULT_DIFF_TOKEN_BUDGET,
) -> DiffOnlyReviewOutput:
    truncated_diff, was_truncated = truncate_to_token_budget(
        diff_text,
        max_tokens=diff_token_budget,
    )
    prompt = build_diff_only_prompt(
        diff_text=truncated_diff,
        pr_metadata=pr_metadata,
    )
    review = await call_llm_json(
        prompt=prompt,
        provider=provider,
        config=config,
    )
    return DiffOnlyReviewOutput(
        prompt=prompt,
        truncated_diff=truncated_diff,
        review=review,
        total_tokens=estimate_token_count(prompt),
        was_truncated=was_truncated,
    )


def build_diff_only_prompt(
    *,
    diff_text: str,
    pr_metadata: Mapping[str, Any],
) -> str:
    title = str(pr_metadata.get("title", "Untitled PR"))
    pr_id = str(pr_metadata.get("pr_id", pr_metadata.get("id", "unknown")))
    description = str(pr_metadata.get("description", ""))
    return (
        "You are an expert code reviewer. Review only the unified diff below.\n"
        "Do not assume access to files or graph context that is not present in the diff.\n"
        "Return ONLY valid JSON matching this schema:\n"
        '{"findings":[{"category":"string","severity":"low|medium|high|critical",'
        '"confidence":0.0,"summary":"string","technical_reasoning":"string",'
        '"suggested_fix":"string","evidence":[{"node_id":"string","file_path":"string",'
        '"start_line":1,"end_line":1}]}],"overall_risk":"low|medium|high|critical"}\n\n'
        f"PR ID: {pr_id}\n"
        f"PR Title: {title}\n"
        f"PR Description:\n{description}\n\n"
        f"Unified Diff:\n```diff\n{diff_text.strip()}\n```\n"
    )


def truncate_to_token_budget(
    text: str,
    *,
    max_tokens: int,
) -> tuple[str, bool]:
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")
    if estimate_token_count(text) <= max_tokens:
        return text, False

    lines = text.splitlines()
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line])
        if estimate_token_count(candidate) > max_tokens:
            break
        kept.append(line)

    omitted = max(0, len(lines) - len(kept))
    suffix = f"\n[TRUNCATED - {omitted} lines omitted]"
    while kept and estimate_token_count("\n".join(kept) + suffix) > max_tokens:
        kept.pop()
        omitted += 1
        suffix = f"\n[TRUNCATED - {omitted} lines omitted]"

    if not kept:
        return suffix.strip(), True
    return "\n".join(kept) + suffix, True


__all__ = [
    "DEFAULT_DIFF_TOKEN_BUDGET",
    "DiffOnlyReviewOutput",
    "build_diff_only_prompt",
    "diff_only_review",
    "truncate_to_token_budget",
]
