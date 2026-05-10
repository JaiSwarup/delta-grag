from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalMetrics:
    precision: float
    recall: float
    f1: float


def retrieval_metrics(retrieved: set[str], expected: set[str]) -> RetrievalMetrics:
    if not retrieved and not expected:
        return RetrievalMetrics(precision=1.0, recall=1.0, f1=1.0)

    true_positive = len(retrieved & expected)
    precision = true_positive / len(retrieved) if retrieved else 0.0
    recall = true_positive / len(expected) if expected else 0.0
    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return RetrievalMetrics(precision=precision, recall=recall, f1=f1)


def context_reduction(*, graph_tokens: int, baseline_tokens: int) -> float:
    if baseline_tokens <= 0:
        return 0.0
    reduction = (baseline_tokens - graph_tokens) / baseline_tokens
    return max(-1.0, min(1.0, reduction))


__all__ = ["RetrievalMetrics", "context_reduction", "retrieval_metrics"]
