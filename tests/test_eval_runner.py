from __future__ import annotations

from pathlib import Path

from src.eval.runner import run_benchmarks
from src.eval.scorer import context_reduction, retrieval_metrics


def test_retrieval_metrics_computes_precision_recall_f1() -> None:
    metrics = retrieval_metrics({"a", "b", "c"}, {"b", "c", "d"})
    assert round(metrics.precision, 4) == 0.6667
    assert round(metrics.recall, 4) == 0.6667
    assert round(metrics.f1, 4) == 0.6667


def test_context_reduction_handles_standard_case() -> None:
    reduction = context_reduction(graph_tokens=250, baseline_tokens=500)
    assert reduction == 0.5


def test_run_benchmarks_writes_json_and_markdown(tmp_path: Path) -> None:
    fixture_path = Path("tests/fixtures/eval_cases.json").resolve()
    result = run_benchmarks(
        fixture_path=fixture_path,
        output_dir=tmp_path,
    )

    assert result.json_path.exists()
    assert result.markdown_path.exists()
    assert result.payload["case_count"] == 2

    impact = result.payload["benchmarks"]["impact_accuracy"]
    assert impact["benchmark"] == "impact_accuracy"
    assert impact["case_count"] == 2
    assert impact["macro"]["recall"] > 0

    efficiency = result.payload["benchmarks"]["token_efficiency"]
    assert efficiency["benchmark"] == "token_efficiency"
    assert efficiency["average_reduction"] > 0
