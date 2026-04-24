from __future__ import annotations

import networkx as nx

from src.ingestion.diff_parser import parse_unified_diff
from src.pipeline.review_pipeline import (
    PipelineConfig,
    run_review_pipeline,
    run_review_pipeline_from_parsed_diff,
    summarize_pipeline_result,
)


def _build_test_call_graph() -> nx.DiGraph:
    g = nx.DiGraph()

    # file: a.py
    g.add_node(
        "a:outer",
        file="a.py",
        start_line=1,
        end_line=40,
        qualified_name="pkg.a.outer",
        name="outer",
        code="def outer():\n    return 1",
    )
    g.add_node(
        "a:inner",
        file="a.py",
        start_line=10,
        end_line=20,
        qualified_name="pkg.a.outer.inner",
        name="inner",
        code="def inner():\n    return 2",
    )
    g.add_node(
        "a:tail",
        file="a.py",
        start_line=50,
        end_line=60,
        qualified_name="pkg.a.tail",
        name="tail",
        code="def tail():\n    return 3",
    )

    # file: b.py
    g.add_node(
        "b:alpha",
        file="b.py",
        start_line=5,
        end_line=12,
        qualified_name="pkg.b.alpha",
        name="alpha",
        code="def alpha():\n    return 10",
    )
    g.add_node(
        "b:beta",
        file="b.py",
        start_line=30,
        end_line=35,
        qualified_name="pkg.b.beta",
        name="beta",
        code="def beta():\n    return 20",
    )

    # caller -> callee
    g.add_edges_from(
        [
            ("a:outer", "a:inner"),
            ("a:inner", "b:alpha"),
            ("b:alpha", "b:beta"),
            ("a:tail", "b:beta"),
        ]
    )
    return g


def test_run_review_pipeline_happy_path_resolves_anchors_and_builds_context() -> None:
    g = _build_test_call_graph()
    diff_text = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -11,2 +11,2 @@
-    old_inner()
+    new_inner()
diff --git a/b.py b/b.py
index 3333333..4444444 100644
--- a/b.py
+++ b/b.py
@@ -31,1 +31,1 @@
-    old_beta()
+    new_beta()
"""

    cfg = PipelineConfig(
        k_up=2,
        k_down=2,
        max_nodes=20,
        max_chars=20_000,
        include_code=True,
        include_diff_in_context=True,
    )
    result = run_review_pipeline(
        call_graph=g,
        pr_diff=diff_text,
        config=cfg,
        pr_metadata={"pr_id": 101, "title": "pipeline-test"},
    )

    assert result.parsed_diff.changed_files == ("a.py", "b.py")
    assert result.anchors.anchor_node_ids == ["a:inner", "b:beta"]
    assert result.anchors.pr_metadata["pr_id"] == 101

    assert result.impact_subgraph.number_of_nodes() >= 2
    assert result.impact_subgraph.number_of_edges() >= 1
    assert result.node_order == list(result.impact_subgraph.nodes())

    text = result.linearized_context
    assert "## PR DIFF HUNK" in text
    assert "## MODIFIED" in text
    assert "## CALLERS (depth k)" in text
    assert "## CALLEES (depth m)" in text
    assert "pkg.a.outer.inner" in text
    assert "pkg.b.beta" in text

    md = result.metadata
    assert md["config"]["k_up"] == 2
    assert md["config"]["k_down"] == 2
    assert md["diff"]["changed_file_count"] == 2
    assert md["anchors"]["resolved_count"] == 2
    assert md["impact_subgraph"]["node_order_count"] == len(result.node_order)


def test_run_review_pipeline_from_parsed_diff_matches_raw_pipeline_anchor_set() -> None:
    g = _build_test_call_graph()
    diff_text = """\
diff --git a/b.py b/b.py
index 3333333..4444444 100644
--- a/b.py
+++ b/b.py
@@ -6,1 +6,1 @@
-    old_alpha()
+    new_alpha()
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -55,1 +55,1 @@
-    old_tail()
+    new_tail()
"""

    parsed = parse_unified_diff(diff_text)

    cfg = PipelineConfig(
        k_up=1,
        k_down=1,
        max_nodes=10,
        max_chars=8_000,
        include_code=True,
        include_diff_in_context=True,
    )

    raw_result = run_review_pipeline(
        call_graph=g,
        pr_diff=diff_text,
        config=cfg,
        pr_metadata={"source": "raw"},
    )
    parsed_result = run_review_pipeline_from_parsed_diff(
        call_graph=g,
        parsed_diff=parsed,
        raw_pr_diff=diff_text,
        config=cfg,
        pr_metadata={"source": "parsed"},
    )

    assert raw_result.anchors.anchor_node_ids == parsed_result.anchors.anchor_node_ids
    assert raw_result.node_order == parsed_result.node_order
    assert (
        raw_result.impact_subgraph.number_of_nodes()
        == parsed_result.impact_subgraph.number_of_nodes()
    )
    assert (
        raw_result.impact_subgraph.number_of_edges()
        == parsed_result.impact_subgraph.number_of_edges()
    )
    assert "## PR DIFF HUNK" in parsed_result.linearized_context
    assert parsed_result.anchors.pr_metadata["source"] == "parsed"


def test_run_review_pipeline_with_no_resolved_anchors_keeps_contract_stable() -> None:
    g = _build_test_call_graph()
    diff_text = """\
diff --git a/missing.py b/missing.py
index 1111111..2222222 100644
--- a/missing.py
+++ b/missing.py
@@ -1,2 +1,2 @@
-old()
+new()
"""

    result = run_review_pipeline(
        call_graph=g,
        pr_diff=diff_text,
        config=PipelineConfig(k_up=1, k_down=1, max_nodes=5, max_chars=6_000),
        pr_metadata={"pr_id": 202},
    )

    assert result.anchors.anchor_node_ids == []
    assert len(result.anchors.unresolved_hunks) == 1
    assert result.impact_subgraph.number_of_nodes() == 0
    assert result.impact_subgraph.number_of_edges() == 0
    assert result.node_order == []

    text = result.linearized_context
    assert "## MODIFIED" in text
    assert "- None (no anchors found in subgraph)" in text
    assert "## CALLERS (depth k)" in text
    assert "## CALLEES (depth m)" in text


def test_run_review_pipeline_budget_truncation_marker_present_when_forced() -> None:
    g = _build_test_call_graph()
    diff_text = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -11,2 +11,2 @@
-    old_inner()
+    new_inner()
"""

    result = run_review_pipeline(
        call_graph=g,
        pr_diff=diff_text,
        config=PipelineConfig(
            k_up=2,
            k_down=2,
            max_nodes=20,
            max_chars=600,
            include_code=True,
            include_diff_in_context=True,
        ),
    )

    assert len(result.linearized_context) <= 1200
    assert "<!-- TRUNCATED: character budget reached -->" in result.linearized_context


def test_pipeline_config_validation_rejects_invalid_values() -> None:
    g = _build_test_call_graph()
    diff_text = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -11,2 +11,2 @@
-    old_inner()
+    new_inner()
"""

    bad_cfgs = [
        PipelineConfig(k_up=-1, k_down=1, max_nodes=10, max_chars=5000),
        PipelineConfig(k_up=1, k_down=-1, max_nodes=10, max_chars=5000),
        PipelineConfig(k_up=1, k_down=1, max_nodes=0, max_chars=5000),
        PipelineConfig(k_up=1, k_down=1, max_nodes=10, max_chars=200),
        PipelineConfig(
            k_up=1,
            k_down=1,
            max_nodes=10,
            max_chars=5000,
            run_full_review=True,
        ),
        PipelineConfig(
            k_up=1,
            k_down=1,
            max_nodes=10,
            max_chars=5000,
            run_full_review=True,
            llm_backend="hf_pipeline",
        ),
        PipelineConfig(
            k_up=1,
            k_down=1,
            max_nodes=10,
            max_chars=5000,
            llm_mock_response_text='{"findings":[]}',
        ),
    ]

    for cfg in bad_cfgs:
        try:
            run_review_pipeline(call_graph=g, pr_diff=diff_text, config=cfg)
            assert False, f"Expected ValueError for config: {cfg}"
        except ValueError:
            pass


def test_summarize_pipeline_result_returns_expected_counts() -> None:
    g = _build_test_call_graph()
    diff_text = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -11,2 +11,2 @@
-    old_inner()
+    new_inner()
"""

    result = run_review_pipeline(
        call_graph=g,
        pr_diff=diff_text,
        config=PipelineConfig(k_up=1, k_down=1, max_nodes=10, max_chars=10_000),
    )
    summary = summarize_pipeline_result(result)

    assert summary["changed_file_count"] == 1
    assert summary["changed_files"] == ["a.py"]
    assert summary["resolved_anchor_count"] == 1
    assert summary["unresolved_hunk_count"] == 0
    assert summary["impact_nodes"] == result.impact_subgraph.number_of_nodes()
    assert summary["impact_edges"] == result.impact_subgraph.number_of_edges()
    assert summary["node_order_count"] == len(result.node_order)
    assert summary["context_chars"] == len(result.linearized_context)


def test_pipeline_context_excludes_diff_by_default() -> None:
    g = _build_test_call_graph()
    diff_text = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -11,2 +11,2 @@
-    old_inner()
+    new_inner()
"""

    result = run_review_pipeline(
        call_graph=g,
        pr_diff=diff_text,
        config=PipelineConfig(k_up=1, k_down=1, max_nodes=10, max_chars=8_000),
    )
    assert "## PR DIFF HUNK" not in result.linearized_context
