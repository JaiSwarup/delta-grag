from __future__ import annotations

from src.token_budget import BudgetedChunk, apply_token_budget, estimate_token_count


def test_estimate_token_count_splits_code_like_text() -> None:
    text = "def run(x): return x + 1"
    assert estimate_token_count(text) == 10


def test_apply_token_budget_retains_anchor_chunks_first() -> None:
    chunks = [
        BudgetedChunk("anchor-1", "def anchor_one(): return 1", is_anchor=True),
        BudgetedChunk("anchor-2", "def anchor_two(): return 2", is_anchor=True),
        BudgetedChunk("caller-1", "def upstream(): return anchor_one()", priority=10),
        BudgetedChunk("callee-1", "def downstream(): return anchor_two()", priority=20),
    ]

    result = apply_token_budget(
        chunks,
        max_tokens=18,
        reserved_tokens=2,
    )

    kept_ids = [chunk.chunk_id for chunk in result.kept_chunks]
    assert kept_ids[:2] == ["anchor-1", "anchor-2"]
    assert result.anchors_retained is True
    assert result.truncated is True


def test_apply_token_budget_drops_lower_priority_non_anchor_chunks_first() -> None:
    chunks = [
        BudgetedChunk("anchor", "def anchor(): return 1", is_anchor=True),
        BudgetedChunk("important", "def important(): return anchor()", priority=10),
        BudgetedChunk("less-important", "def less(): return important()", priority=50),
    ]

    result = apply_token_budget(
        chunks,
        max_tokens=22,
        reserved_tokens=2,
    )

    kept_ids = [chunk.chunk_id for chunk in result.kept_chunks]
    assert "anchor" in kept_ids
    assert "important" in kept_ids
    assert "less-important" in result.dropped_chunk_ids


def test_apply_token_budget_reports_missing_anchor_retention_when_budget_too_small() -> None:
    chunks = [
        BudgetedChunk("anchor", "def anchor(): return 1", is_anchor=True),
    ]

    result = apply_token_budget(
        chunks,
        max_tokens=4,
        reserved_tokens=1,
    )

    assert result.kept_chunks == []
    assert result.anchors_retained is False
    assert result.dropped_chunk_ids == ["anchor"]


def test_apply_token_budget_validates_limits() -> None:
    try:
        apply_token_budget([], max_tokens=0)
        assert False, "Expected ValueError for max_tokens=0"
    except ValueError:
        pass

    try:
        apply_token_budget([], max_tokens=10, reserved_tokens=10)
        assert False, "Expected ValueError when reserved_tokens >= max_tokens"
    except ValueError:
        pass
