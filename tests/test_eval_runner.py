from __future__ import annotations

from pathlib import Path

import networkx as nx

from src.eval.runner import _normalize_graph_file_paths
from src.eval.ground_truth import build_structural_ground_truth
from src.ingestion.diff_parser import parse_unified_diff


def test_eval_path_normalization_keeps_anchor_resolution_working(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "pkg" / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("def run():\n    return 2\n", encoding="utf-8")

    graph = nx.DiGraph()
    graph.add_node(
        "pkg/mod.py::run",
        file_path=str(source.resolve()),
        fqn="run",
        start_line=1,
        end_line=2,
        source_code=source.read_text(encoding="utf-8"),
    )
    diff = parse_unified_diff(
        """\
diff --git a/pkg/mod.py b/pkg/mod.py
index 1111111..2222222 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -2,1 +2,1 @@
-    return 1
+    return 2
"""
    )

    _normalize_graph_file_paths(graph, repo)
    truth, anchors = build_structural_ground_truth(graph=graph, parsed_diff=diff)

    assert graph.nodes["pkg/mod.py::run"]["file_path"] == "pkg/mod.py"
    assert anchors.anchor_node_ids == ["pkg/mod.py::run"]
    assert truth.anchor_fqns == ("run",)
