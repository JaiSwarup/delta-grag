from __future__ import annotations

from pathlib import Path

import networkx as nx

from src.call_graph_builder import (
    GRAPH_BUILDER_VERSION,
    CallGraph,
    build_call_graph,
    graph_cache_key,
    load_graph_from_cache,
    save_graph_to_cache,
)


def _node_id(path: str, fqn: str) -> str:
    return f"{path}::{fqn}"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_call_graph_creates_directed_graph_with_expected_nodes_and_edges(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "mod.py",
        "def util():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    return util()\n",
    )

    call_graph = build_call_graph(tmp_path)

    assert isinstance(call_graph, CallGraph)
    assert nx.is_directed(call_graph.graph)
    assert call_graph.graph.number_of_nodes() == 2
    assert call_graph.graph.number_of_edges() == 1
    assert call_graph.graph.has_edge(
        _node_id("mod.py", "run"),
        _node_id("mod.py", "util"),
    )
    assert call_graph.graph.nodes[_node_id("mod.py", "run")]["fqn"] == "run"


def test_call_graph_get_callers_and_callees_respect_depth(tmp_path: Path) -> None:
    _write(
        tmp_path / "mod.py",
        "def c():\n"
        "    return 1\n"
        "\n"
        "def b():\n"
        "    return c()\n"
        "\n"
        "def a():\n"
        "    return b()\n",
    )

    call_graph = build_call_graph(tmp_path)

    assert call_graph.get_callees(_node_id("mod.py", "a"), depth=2) == {
        _node_id("mod.py", "a"),
        _node_id("mod.py", "b"),
        _node_id("mod.py", "c"),
    }
    assert call_graph.get_callers(_node_id("mod.py", "c"), depth=2) == {
        _node_id("mod.py", "a"),
        _node_id("mod.py", "b"),
        _node_id("mod.py", "c"),
    }


def test_call_graph_json_round_trip_preserves_attrs(tmp_path: Path) -> None:
    _write(
        tmp_path / "mod.py",
        "def util():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    return util()\n",
    )
    call_graph = build_call_graph(tmp_path)
    json_path = tmp_path / "artifacts" / "call_graph.json"

    call_graph.save_json(json_path)
    loaded = CallGraph.load_json(json_path)

    run_id = _node_id("mod.py", "run")
    util_id = _node_id("mod.py", "util")

    assert (
        loaded.graph.nodes[run_id]["start_line"]
        == call_graph.graph.nodes[run_id]["start_line"]
    )
    assert (
        loaded.graph.nodes[util_id]["file_path"]
        == call_graph.graph.nodes[util_id]["file_path"]
    )
    assert loaded.graph.edges[(run_id, util_id)]["call_site_line"] == 5


def test_call_graph_graphml_round_trip_preserves_core_node_attrs(tmp_path: Path) -> None:
    _write(
        tmp_path / "mod.py",
        "def util():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    return util()\n",
    )
    call_graph = build_call_graph(tmp_path)
    graphml_path = tmp_path / "artifacts" / "call_graph.graphml"

    call_graph.save_graphml(graphml_path)
    loaded = nx.read_graphml(graphml_path)

    run_id = _node_id("mod.py", "run")
    util_id = _node_id("mod.py", "util")

    assert run_id in loaded.nodes
    assert loaded.nodes[run_id]["fqn"] == "run"
    assert loaded.nodes[run_id]["start_line"] == 4
    assert loaded.nodes[util_id]["end_line"] == 2


def test_file_qualified_ids_prevent_cross_file_name_collisions(tmp_path: Path) -> None:
    _write(
        tmp_path / "a.py",
        "def util():\n"
        "    return 'a'\n"
        "\n"
        "def run():\n"
        "    return util()\n",
    )
    _write(
        tmp_path / "b.py",
        "def util():\n"
        "    return 'b'\n"
        "\n"
        "def run():\n"
        "    return util()\n",
    )

    call_graph = build_call_graph(tmp_path)

    assert set(call_graph.graph.nodes()) == {
        _node_id("a.py", "util"),
        _node_id("a.py", "run"),
        _node_id("b.py", "util"),
        _node_id("b.py", "run"),
    }
    assert call_graph.graph.has_edge(
        _node_id("a.py", "run"),
        _node_id("a.py", "util"),
    )
    assert call_graph.graph.has_edge(
        _node_id("b.py", "run"),
        _node_id("b.py", "util"),
    )


# ---------------------------------------------------------------------------
# New tests: GRAPH_BUILDER_VERSION, load_json repo_root, single-parse parity
# ---------------------------------------------------------------------------


def test_graph_cache_key_changes_when_builder_version_changes(tmp_path: Path) -> None:
    """Bumping GRAPH_BUILDER_VERSION must produce a different cache key."""
    import src.call_graph_builder as _mod

    root = tmp_path / "repo"
    root.mkdir()
    sha = "deadbeef"

    original_version = _mod.GRAPH_BUILDER_VERSION
    key_v1 = graph_cache_key(root, sha)

    _mod.GRAPH_BUILDER_VERSION = original_version + "_changed"
    try:
        key_v2 = graph_cache_key(root, sha)
    finally:
        _mod.GRAPH_BUILDER_VERSION = original_version

    assert key_v1 != key_v2, "cache key must change when GRAPH_BUILDER_VERSION changes"


def test_load_json_with_repo_root_sets_repo_root(tmp_path: Path) -> None:
    """load_json(repo_root=...) must set repo_root on the returned CallGraph."""
    _write(tmp_path / "mod.py", "def f():\n    return 1\n")
    call_graph = build_call_graph(tmp_path)
    json_path = tmp_path / "cg.json"
    call_graph.save_json(json_path)

    loaded = CallGraph.load_json(json_path, repo_root=tmp_path)
    assert loaded.repo_root is not None
    assert loaded.repo_root == tmp_path.expanduser().resolve()


def test_load_json_without_repo_root_leaves_repo_root_none(tmp_path: Path) -> None:
    """load_json without repo_root must leave repo_root as None (backwards compat)."""
    _write(tmp_path / "mod.py", "def f():\n    return 1\n")
    call_graph = build_call_graph(tmp_path)
    json_path = tmp_path / "cg.json"
    call_graph.save_json(json_path)

    loaded = CallGraph.load_json(json_path)
    assert loaded.repo_root is None


def test_single_parse_build_produces_same_graph_as_triple_parse(tmp_path: Path) -> None:
    """build_call_graph (single-parse) must produce identical nodes and edges
    to the structure produced by the original triple-parse approach.

    We verify this by checking node count, edge count, and that all edges
    present before refactor are still present after.
    """
    _write(
        tmp_path / "helpers.py",
        "def helper(value):\n    return value + 1\n",
    )
    _write(
        tmp_path / "app.py",
        "from helpers import helper\n\ndef run(value):\n    return helper(value)\n",
    )

    call_graph = build_call_graph(tmp_path)

    # Structural assertions that must hold regardless of parse count
    assert call_graph.graph.number_of_nodes() == 2
    assert call_graph.graph.number_of_edges() == 1
    assert call_graph.graph.has_edge(
        _node_id("app.py", "run"),
        _node_id("helpers.py", "helper"),
    )
    # Attributes must survive the single-parse path unchanged
    run_data = call_graph.graph.nodes[_node_id("app.py", "run")]
    assert run_data["fqn"] == "run"
    assert run_data["start_line"] == 3
