from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.eval.metrics import (
    EvalCase,
    build_metrics_table,
    case_from_pipeline_result,
    compute_bleu,
    compute_cross_file_detection_rate,
    compute_hallucination_rate,
    compute_rouge_l,
    compute_structural_recall,
    compute_token_reduction,
    save_metrics_table,
)
from src.impact_subgraph import SubgraphStats
from src.pipeline.pr_orchestrator import PipelineResult


def _make_case(
    *,
    system: str,
    pr_id: str,
    retrieved_fqns: tuple[str, ...] = ("pkg.a.alpha", "pkg.b.beta"),
    ground_truth_fqns: tuple[str, ...] = ("pkg.a.alpha", "pkg.c.gamma"),
    context_tokens: int = 120,
    baseline_context_tokens: int = 300,
    detected_cross_file_fqns: tuple[str, ...] = ("pkg.b.beta",),
    ground_truth_cross_file_fqns: tuple[str, ...] = ("pkg.b.beta", "pkg.c.gamma"),
    issue_fqns: tuple[str, ...] = ("pkg.a.alpha", "pkg.unknown.missing"),
    known_function_registry: tuple[str, ...] = (
        "pkg.a.alpha",
        "pkg.b.beta",
        "pkg.c.gamma",
    ),
    generated_review_text: str = "Potential regression in helper flow.",
    reference_review_text: str = "Potential regression in helper flow.",
) -> EvalCase:
    return EvalCase(
        system=system,
        pr_id=pr_id,
        retrieved_fqns=retrieved_fqns,
        ground_truth_fqns=ground_truth_fqns,
        context_tokens=context_tokens,
        baseline_context_tokens=baseline_context_tokens,
        detected_cross_file_fqns=detected_cross_file_fqns,
        ground_truth_cross_file_fqns=ground_truth_cross_file_fqns,
        issue_fqns=issue_fqns,
        known_function_registry=known_function_registry,
        generated_review_text=generated_review_text,
        reference_review_text=reference_review_text,
    )


def _make_pipeline_result() -> PipelineResult:
    return PipelineResult(
        pr_id="42",
        pr_url="https://github.com/acme/widgets/pull/42",
        review={
            "findings": [
                {
                    "summary": "Potential regression in helper flow",
                    "severity": "medium",
                    "technical_reasoning": "Changed code path no longer validates input.",
                    "evidence": [
                        {
                            "node_id": "pkg.a.alpha",
                            "file_path": "app.py",
                            "start_line": 10,
                            "end_line": 14,
                        }
                    ],
                }
            ],
            "overall_risk": "medium",
            "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
            "metadata": {"title": "Example PR"},
        },
        subgraph_stats=SubgraphStats(
            node_count=4,
            edge_count=3,
            anchor_count=1,
            caller_count=1,
            callee_count=2,
            shared_count=0,
            cutoff_reasons=(),
        ),
        timing_breakdown={"fetch_pr_info_ms": 1.0, "build_graph_ms": 2.0},
        context_tokens=144,
        cache_hit=False,
    )


def test_compute_structural_recall_is_bounded_and_correct() -> None:
    recall = compute_structural_recall(
        retrieved=["pkg.a.alpha", "pkg.b.beta"],
        ground_truth=["pkg.a.alpha", "pkg.c.gamma"],
    )

    assert recall == 0.5
    assert 0.0 <= recall <= 1.0


def test_compute_structural_recall_returns_zero_for_empty_ground_truth() -> None:
    recall = compute_structural_recall(
        retrieved=["pkg.a.alpha"],
        ground_truth=[],
    )

    assert recall == 0.0


def test_compute_token_reduction_uses_percentage() -> None:
    reduction = compute_token_reduction(
        baseline_context_tokens=400,
        candidate_context_tokens=100,
    )

    assert reduction == 75.0


def test_cross_file_detection_rate_returns_none_when_no_cross_file_ground_truth() -> (
    None
):
    rate = compute_cross_file_detection_rate(
        detected_cross_file_fqns=["pkg.a.alpha"],
        ground_truth_cross_file_fqns=[],
    )

    assert rate is None


def test_compute_hallucination_rate_is_bounded_and_correct() -> None:
    rate = compute_hallucination_rate(
        issue_fqns=["pkg.a.alpha", "pkg.unknown.missing"],
        known_fqns=["pkg.a.alpha", "pkg.b.beta"],
    )

    assert rate == 0.5
    assert 0.0 <= rate <= 1.0


def test_compute_hallucination_rate_ignores_empty_issue_list() -> None:
    rate = compute_hallucination_rate(
        issue_fqns=[],
        known_fqns=["pkg.a.alpha"],
    )

    assert rate == 0.0


def test_compute_bleu_and_rouge_l_are_bounded() -> None:
    bleu = compute_bleu(
        candidate_text="Potential regression in helper flow.",
        reference_text="Potential regression in helper flow.",
    )
    rouge_l = compute_rouge_l(
        candidate_text="Potential regression in helper flow.",
        reference_text="Potential regression in helper flow.",
    )

    assert 0.0 <= bleu <= 1.0
    assert 0.0 <= rouge_l <= 1.0
    assert bleu > 0.9
    assert rouge_l > 0.9


def test_build_metrics_table_is_reproducible_and_sorted() -> None:
    cases = [
        _make_case(system="semantic_rag", pr_id="2"),
        _make_case(system="dgrag", pr_id="1"),
        _make_case(system="diff_only", pr_id="1"),
    ]

    first = build_metrics_table(cases)
    second = build_metrics_table(list(reversed(cases)))

    assert list(first["system"]) == ["dgrag", "diff_only", "semantic_rag"]
    assert first.to_dict(orient="records") == second.to_dict(orient="records")


def test_save_metrics_table_writes_150_rows_for_50_prs_x_3_systems(
    tmp_path: Path,
) -> None:
    systems = ("dgrag", "semantic_rag", "diff_only")
    cases: list[EvalCase] = []

    for pr_num in range(1, 51):
        for system in systems:
            cases.append(
                _make_case(
                    system=system,
                    pr_id=str(pr_num),
                    generated_review_text=f"{system} review for PR {pr_num}",
                    reference_review_text=f"reference review for PR {pr_num}",
                )
            )

    output_path = tmp_path / "metrics_table.csv"
    df = save_metrics_table(cases, output_path=output_path)

    assert output_path.exists()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 150
    assert list(df.columns) == [
        "system",
        "pr_id",
        "structural_recall",
        "token_reduction_pct",
        "cross_file_detection_rate",
        "hallucination_rate",
        "bleu",
        "rouge_l",
    ]

    loaded = pd.read_csv(output_path)
    assert len(loaded) == 150


def test_case_from_pipeline_result_builds_expected_eval_case() -> None:
    result = _make_pipeline_result()

    case = case_from_pipeline_result(
        system="dgrag",
        result=result,
        ground_truth_fqns=["pkg.a.alpha", "pkg.c.gamma"],
        baseline_context_tokens=300,
        known_function_registry=["pkg.a.alpha", "pkg.b.beta", "pkg.c.gamma"],
        detected_cross_file_fqns=["pkg.b.beta"],
        ground_truth_cross_file_fqns=["pkg.b.beta"],
        reference_review_text="Potential regression in helper flow",
    )

    assert case.system == "dgrag"
    assert case.pr_id == "42"
    assert case.context_tokens == 144
    assert case.baseline_context_tokens == 300
    assert set(case.retrieved_fqns) == {"pkg.a.alpha", "pkg.b.beta"}
    assert case.issue_fqns == ("pkg.a.alpha",)
    assert case.reference_review_text == "Potential regression in helper flow"


def test_metrics_table_rows_have_valid_metric_ranges() -> None:
    cases = [
        _make_case(system="dgrag", pr_id="10"),
        _make_case(
            system="semantic_rag",
            pr_id="11",
            generated_review_text="Different wording for the same issue.",
            reference_review_text="Potential regression in helper flow.",
        ),
    ]

    df = build_metrics_table(cases)

    for row in df.to_dict(orient="records"):
        assert 0.0 <= float(row["structural_recall"]) <= 1.0
        assert -100.0 <= float(row["token_reduction_pct"]) <= 100.0
        cross_file = row["cross_file_detection_rate"]
        if cross_file == cross_file:  # not NaN / pandas null-like
            assert 0.0 <= float(cross_file) <= 1.0
        assert 0.0 <= float(row["hallucination_rate"]) <= 1.0
        assert 0.0 <= float(row["bleu"]) <= 1.0
        assert 0.0 <= float(row["rouge_l"]) <= 1.0


def test_saved_metrics_csv_is_deterministic(tmp_path: Path) -> None:
    cases = [
        _make_case(system="semantic_rag", pr_id="20"),
        _make_case(system="dgrag", pr_id="19"),
        _make_case(system="diff_only", pr_id="21"),
    ]

    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"

    save_metrics_table(cases, output_path=first_path)
    save_metrics_table(list(reversed(cases)), output_path=second_path)

    assert first_path.read_text(encoding="utf-8") == second_path.read_text(
        encoding="utf-8"
    )


def test_metrics_csv_serialization_handles_none_cross_file_rate(tmp_path: Path) -> None:
    case = _make_case(
        system="dgrag",
        pr_id="99",
        detected_cross_file_fqns=(),
        ground_truth_cross_file_fqns=(),
    )

    output_path = tmp_path / "metrics.csv"
    save_metrics_table([case], output_path=output_path)

    payload = pd.read_csv(output_path).to_dict(orient="records")[0]
    assert payload["system"] == "dgrag"
    assert payload["pr_id"] == 99 or payload["pr_id"] == "99"
    assert pd.isna(payload["cross_file_detection_rate"])
