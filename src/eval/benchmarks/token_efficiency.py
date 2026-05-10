from __future__ import annotations

from typing import Any

from src.eval.scorer import context_reduction


def run(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Evaluate token efficiency vs baseline retrieval contexts.

    Required fields per case:
    - id
    - graph_tokens: int
    - baseline_tokens: int
    """
    per_case: list[dict[str, Any]] = []
    reduction_sum = 0.0

    for case in cases:
        case_id = str(case.get("id", "unknown"))
        graph_tokens = int(case.get("graph_tokens", 0))
        baseline_tokens = int(case.get("baseline_tokens", 0))
        reduction = context_reduction(
            graph_tokens=graph_tokens,
            baseline_tokens=baseline_tokens,
        )
        reduction_sum += reduction
        per_case.append(
            {
                "id": case_id,
                "graph_tokens": graph_tokens,
                "baseline_tokens": baseline_tokens,
                "reduction": reduction,
            }
        )

    total = len(per_case)
    return {
        "benchmark": "token_efficiency",
        "case_count": total,
        "average_reduction": reduction_sum / total if total else 0.0,
        "cases": per_case,
    }
