from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import networkx as nx
import pytest

from src.call_graph_builder import (
    GRAPH_BUILDER_VERSION,
    CallGraph,
    build_call_graph,
    graph_cache_key,
    load_graph_from_cache,
    save_graph_to_cache,
)
from src.eval.runner import _normalize_graph_file_paths
from src.eval.ground_truth import build_structural_ground_truth
from src.ingestion.diff_parser import parse_unified_diff


# ---------------------------------------------------------------------------
# Existing regression test (preserved unchanged)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Cache: miss → build → write
# ---------------------------------------------------------------------------


def _make_repo(path: Path) -> Path:
    """Create a minimal Python repo under *path* and return the repo root."""
    root = path / "repo"
    root.mkdir(parents=True)
    (root / "mod.py").write_text(
        "def util():\n    return 1\n\ndef run():\n    return util()\n",
        encoding="utf-8",
    )
    return root


def test_cache_miss_builds_graph_and_writes_cache_file(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    cache_dir = tmp_path / "cache"
    head_sha = "abc123"

    key = graph_cache_key(repo, head_sha)
    cache_file = cache_dir / f"{key}.json"

    assert not cache_file.exists(), "cache should not exist before first build"

    graph = build_call_graph(repo)
    save_graph_to_cache(cache_dir, key, graph)

    assert cache_file.exists(), "cache file should have been written"


# ---------------------------------------------------------------------------
# Cache: hit → load without calling build_call_graph
# ---------------------------------------------------------------------------


def test_cache_hit_loads_graph_without_build(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    cache_dir = tmp_path / "cache"
    head_sha = "abc123"

    key = graph_cache_key(repo, head_sha)

    # Prime the cache
    graph = build_call_graph(repo)
    save_graph_to_cache(cache_dir, key, graph)

    # Now load from cache; build_call_graph must NOT be called
    with patch("src.call_graph_builder.build_call_graph") as mock_build:
        result = load_graph_from_cache(cache_dir, key, repo_root=repo)

    assert result is not None, "should be a cache hit"
    loaded_graph, load_ms = result
    mock_build.assert_not_called()
    assert loaded_graph.graph.number_of_nodes() == graph.graph.number_of_nodes()
    assert load_ms >= 0.0


# ---------------------------------------------------------------------------
# Cache: loaded graph still resolves anchors (repo_root set correctly)
# ---------------------------------------------------------------------------


def test_graph_loaded_from_cache_resolves_anchors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "pkg" / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("def run():\n    return 2\n", encoding="utf-8")

    cache_dir = tmp_path / "cache"
    head_sha = "deadbeef"
    key = graph_cache_key(repo, head_sha)

    # Build and cache
    graph_obj = build_call_graph(repo)
    save_graph_to_cache(cache_dir, key, graph_obj)

    # Load with repo_root explicitly set
    result = load_graph_from_cache(cache_dir, key, repo_root=repo)
    assert result is not None
    loaded, _ = result

    graph = loaded.graph
    _normalize_graph_file_paths(graph, repo)

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
    truth, anchors = build_structural_ground_truth(graph=graph, parsed_diff=diff)
    assert anchors.anchor_node_ids == ["pkg/mod.py::run"]


# ---------------------------------------------------------------------------
# Cache: rebuild_graphs flag — cache ignored, overwritten
# ---------------------------------------------------------------------------


def test_rebuild_graphs_overwrites_existing_cache(tmp_path: Path) -> None:
    """Simulate what _load_or_build_graph does with rebuild_graphs=True."""
    repo = _make_repo(tmp_path)
    cache_dir = tmp_path / "cache"
    head_sha = "abc123"

    key = graph_cache_key(repo, head_sha)

    # Write a stale / invalid cache entry
    cache_file = cache_dir / f"{key}.json"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"nodes": [], "edges": []}', encoding="utf-8")

    # Rebuild: ignore cache, build fresh, overwrite
    fresh_graph = build_call_graph(repo)
    save_graph_to_cache(cache_dir, key, fresh_graph)

    # The cache file should now contain real data
    result = load_graph_from_cache(cache_dir, key, repo_root=repo)
    assert result is not None
    loaded, _ = result
    assert loaded.graph.number_of_nodes() == fresh_graph.graph.number_of_nodes()
