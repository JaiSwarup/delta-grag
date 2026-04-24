from __future__ import annotations

import networkx as nx

from src.pipeline.review_pipeline import (
    PipelineConfig,
    run_review_pipeline,
    summarize_pipeline_result,
)


def test_full_pipeline_end_to_end_with_stubbed_review_output(
    sample_diff_text: str,
    sample_pipeline_config: PipelineConfig,
    mock_review_payload: dict[str, object],
) -> None:
    """
    End-to-end integration test for the small-repo pipeline flow.

    This intentionally avoids any real external LLM/API call and instead runs the
    full review pipeline with a deterministic mock response payload.
    """
    call_graph = nx.DiGraph()
    call_graph.add_node(
        "app.helper",
        file_path="app.py",
        file="app.py",
        start_line=1,
        end_line=2,
        qualified_name="app.helper",
        name="helper",
        source_code="def helper():\n    return 2\n",
        code="def helper():\n    return 2\n",
    )
    call_graph.add_node(
        "app.run",
        file_path="app.py",
        file="app.py",
        start_line=4,
        end_line=5,
        qualified_name="app.run",
        name="run",
        source_code="def run():\n    return helper()\n",
        code="def run():\n    return helper()\n",
    )
    call_graph.add_edge("app.run", "app.helper")

    cfg = PipelineConfig(
        k_up=sample_pipeline_config.k_up,
        k_down=sample_pipeline_config.k_down,
        max_nodes=sample_pipeline_config.max_nodes,
        max_edges=sample_pipeline_config.max_edges,
        max_per_anchor=sample_pipeline_config.max_per_anchor,
        max_chars=sample_pipeline_config.max_chars,
        include_code=sample_pipeline_config.include_code,
        include_diff_in_context=sample_pipeline_config.include_diff_in_context,
        run_full_review=True,
        strict_json_output=True,
        llm_backend="mock",
        llm_model_name="integration-mock",
        llm_temperature=0.0,
        llm_max_new_tokens=512,
        llm_mock_response_text=str(mock_review_payload).replace("'", '"'),
        allow_dev_mock_controls=True,
        output_format="json",
    )

    result = run_review_pipeline(
        call_graph=call_graph,
        pr_diff=sample_diff_text,
        config=cfg,
        pr_metadata={
            "pr_id": "integration-1",
            "title": "Small repo integration flow",
            "description": "Validate end-to-end pipeline behavior without external calls.",
        },
    )

    summary = summarize_pipeline_result(result)

    assert result.parsed_diff.changed_files == ("app.py",)
    assert result.anchors.anchor_node_ids == ["app.helper"]
    assert result.impact_subgraph.number_of_nodes() >= 1
    assert result.impact_subgraph.number_of_edges() >= 0
    assert result.prompt is not None and len(result.prompt) > 0
    assert result.raw_model_output is not None and len(result.raw_model_output) > 0
    assert result.normalized_review is not None
    assert result.formatted_review is not None and len(result.formatted_review) > 0

    findings = result.normalized_review["findings"]
    assert len(findings) == 1
    assert findings[0]["summary"] == "Potential regression in helper flow"
    assert findings[0]["severity"] == "medium"
    assert findings[0]["category"] == "correctness"

    assert summary["changed_file_count"] == 1
    assert summary["resolved_anchor_count"] == 1
    assert summary["impact_nodes"] >= 1
    assert summary["has_prompt"] is True
    assert summary["has_raw_model_output"] is True
    assert summary["has_formatted_review"] is True
    assert summary["finding_count"] == 1
    assert summary["overall_risk"] == "medium"
