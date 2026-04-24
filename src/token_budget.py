"""
Tokenizer-aware budget pruning with anchor retention guarantees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable


DEFAULT_MAX_TOKENS = 8_000
DEFAULT_RESERVED_TOKENS = 512


@dataclass(frozen=True)
class BudgetedChunk:
    chunk_id: str
    text: str
    is_anchor: bool = False
    priority: int = 100


@dataclass(frozen=True)
class TokenBudgetResult:
    kept_chunks: list[BudgetedChunk]
    dropped_chunk_ids: list[str]
    used_tokens: int
    max_tokens: int
    reserved_tokens: int
    truncated: bool
    anchors_retained: bool


def apply_token_budget(
    chunks: Iterable[BudgetedChunk],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    reserved_tokens: int = DEFAULT_RESERVED_TOKENS,
    tokenizer: Callable[[str], int] | None = None,
) -> TokenBudgetResult:
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")
    if reserved_tokens < 0:
        raise ValueError("reserved_tokens must be >= 0")
    if reserved_tokens >= max_tokens:
        raise ValueError("reserved_tokens must be smaller than max_tokens")

    token_counter = tokenizer or estimate_token_count
    token_budget = max_tokens - reserved_tokens
    chunk_list = list(chunks)

    anchor_chunks = [chunk for chunk in chunk_list if chunk.is_anchor]
    non_anchor_chunks = [chunk for chunk in chunk_list if not chunk.is_anchor]

    kept_chunks: list[BudgetedChunk] = []
    used_tokens = 0
    dropped_chunk_ids: list[str] = []

    for chunk in anchor_chunks:
        chunk_tokens = token_counter(chunk.text)
        if used_tokens + chunk_tokens <= token_budget:
            kept_chunks.append(chunk)
            used_tokens += chunk_tokens
        else:
            dropped_chunk_ids.append(chunk.chunk_id)

    sortable_non_anchor = sorted(
        enumerate(non_anchor_chunks),
        key=lambda item: (item[1].priority, item[0]),
    )
    for _, chunk in sortable_non_anchor:
        chunk_tokens = token_counter(chunk.text)
        if used_tokens + chunk_tokens <= token_budget:
            kept_chunks.append(chunk)
            used_tokens += chunk_tokens
        else:
            dropped_chunk_ids.append(chunk.chunk_id)

    kept_ids = {chunk.chunk_id for chunk in kept_chunks}
    anchors_retained = all(chunk.chunk_id in kept_ids for chunk in anchor_chunks)
    truncated = len(dropped_chunk_ids) > 0

    return TokenBudgetResult(
        kept_chunks=kept_chunks,
        dropped_chunk_ids=dropped_chunk_ids,
        used_tokens=used_tokens,
        max_tokens=max_tokens,
        reserved_tokens=reserved_tokens,
        truncated=truncated,
        anchors_retained=anchors_retained,
    )


def estimate_token_count(text: str) -> int:
    """
    Lightweight token estimate.

    This is tokenizer-aware in spirit rather than model-specific: code-ish punctuation,
    identifiers, and word boundaries are counted separately so the estimate is stricter
    than plain character-count proxies.
    """
    if not text:
        return 0
    pieces = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\s]", text)
    return len(pieces)


__all__ = [
    "BudgetedChunk",
    "TokenBudgetResult",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_RESERVED_TOKENS",
    "apply_token_budget",
    "estimate_token_count",
]
