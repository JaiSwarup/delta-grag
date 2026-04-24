from __future__ import annotations

from pathlib import Path

import networkx as nx

from src.call_graph_builder import CallGraph, build_call_graph


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
    assert call_graph.graph.has_edge("run", "util")


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

    assert call_graph.get_callees("a", depth=2) == {"a", "b", "c"}
    assert call_graph.get_callers("c", depth=2) == {"a", "b", "c"}


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

    assert loaded.graph.nodes["run"]["start_line"] == call_graph.graph.nodes["run"]["start_line"]
    assert loaded.graph.nodes["util"]["file_path"] == call_graph.graph.nodes["util"]["file_path"]
    assert loaded.graph.edges[("run", "util")]["call_site_line"] == 5


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

    assert "run" in loaded.nodes
    assert loaded.nodes["run"]["fqn"] == "run"
    assert loaded.nodes["run"]["start_line"] == 4
    assert loaded.nodes["util"]["end_line"] == 2
