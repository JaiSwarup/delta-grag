from __future__ import annotations

from typing import Any

from src.eval.scorer import retrieval_metrics


def run(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Evaluate structural retrieval quality over deterministic fixtures.

    Required fields per case:
    - id
    - retrieved_nodes: list[str]
    - expected_nodes: list[str]
    """
    per_case: list[dict[str, Any]] = []
    precision_sum = 0.0
    recall_sum = 0.0
    f1_sum = 0.0

    for case in cases:
        case_id = str(case.get("id", "unknown"))
        retrieved = set(case.get("retrieved_nodes", []))
        expected = set(case.get("expected_nodes", []))
        metrics = retrieval_metrics(retrieved, expected)
        precision_sum += metrics.precision
        recall_sum += metrics.recall
        f1_sum += metrics.f1
        per_case.append(
            {
                "id": case_id,
                "retrieved_count": len(retrieved),
                "expected_count": len(expected),
                "matched_count": len(retrieved & expected),
                "precision": metrics.precision,
                "recall": metrics.recall,
                "f1": metrics.f1,
            }
        )

    total = len(per_case)
    macro = {
        "precision": precision_sum / total if total else 0.0,
        "recall": recall_sum / total if total else 0.0,
        "f1": f1_sum / total if total else 0.0,
    }
    return {
        "benchmark": "impact_accuracy",
        "case_count": total,
        "macro": macro,
        "cases": per_case,
    }
