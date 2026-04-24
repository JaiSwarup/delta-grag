from __future__ import annotations

import pickle
from pathlib import Path

import networkx as nx
import pytest

from src.graph import graph_builder


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_test_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(
        repo / "app.py",
        "def helper():\n    return 1\n\ndef run():\n    return helper()\n",
    )
    return repo


def test_save_graph_writes_pickle_round_trip(tmp_path: Path) -> None:
    graph = nx.DiGraph()
    graph.add_node("app.run", label="app.run (app.py:4)")
    graph.add_node("app.helper", label="app.helper (app.py:1)")
    graph.add_edge("app.run", "app.helper", call_line=5)

    output_path = tmp_path / "artifacts" / "graph.pkl"
    graph_builder.save_graph(graph, output_path)

    assert output_path.exists()
    with output_path.open("rb") as fh:
        loaded = pickle.load(fh)

    assert isinstance(loaded, nx.DiGraph)
    assert loaded.number_of_nodes() == 2
    assert loaded.number_of_edges() == 1
    assert loaded.has_edge("app.run", "app.helper")
    assert loaded.edges[("app.run", "app.helper")]["call_line"] == 5


def test_print_summary_emits_nodes_edges_and_edge_listing(capsys) -> None:
    graph = nx.DiGraph()
    graph.add_node("a", label="a (a.py:1)")
    graph.add_node("b", label="b (b.py:2)")
    graph.add_edge("a", "b", call_line=7)

    graph_builder.print_summary(graph)
    out = capsys.readouterr().out

    assert "Nodes: 2" in out
    assert "Edges: 1" in out
    assert "a (a.py:1) -> b (b.py:2)  [line=7]" in out


def test_print_summary_handles_edges_without_call_line(capsys) -> None:
    graph = nx.DiGraph()
    graph.add_node("a", label="a")
    graph.add_node("b", label="b")
    graph.add_edge("a", "b")

    graph_builder.print_summary(graph)
    out = capsys.readouterr().out

    assert "Nodes: 2" in out
    assert "Edges: 1" in out
    assert "a -> b" in out


def test_build_call_graph_builds_expected_edge_from_repo(tmp_path: Path) -> None:
    repo = _build_test_repo(tmp_path)

    graph = graph_builder.build_call_graph(repo)

    assert graph.number_of_nodes() >= 2
    assert graph.number_of_edges() >= 1

    run_nodes = [n for n, d in graph.nodes(data=True) if d.get("name") == "run"]
    helper_nodes = [n for n, d in graph.nodes(data=True) if d.get("name") == "helper"]

    assert run_nodes
    assert helper_nodes
    assert any(graph.has_edge(r, h) for r in run_nodes for h in helper_nodes)


def test_main_saves_graph_and_prints_summary(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _build_test_repo(tmp_path)
    output_path = tmp_path / "out" / "graph.pkl"

    monkeypatch.setattr(
        graph_builder,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "repo": str(repo),
                "output": str(output_path),
                "print_summary": True,
            },
        )(),
    )

    graph_builder.main()
    out = capsys.readouterr().out

    assert output_path.exists()
    assert "Saved call graph to:" in out
    assert "Nodes:" in out
    assert "Edges:" in out


def test_main_rejects_invalid_repo_path(tmp_path: Path, monkeypatch) -> None:
    missing_repo = tmp_path / "missing"
    output_path = tmp_path / "graph.pkl"

    monkeypatch.setattr(
        graph_builder,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "repo": str(missing_repo),
                "output": str(output_path),
                "print_summary": False,
            },
        )(),
    )

    with pytest.raises(SystemExit) as exc_info:
        graph_builder.main()

    assert "Invalid --repo path" in str(exc_info.value)


def test_parse_args_reads_expected_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "graph_builder.py",
            "--repo",
            "sample-repo",
            "--output",
            "graph.pkl",
            "--print-summary",
        ],
    )

    args = graph_builder.parse_args()

    assert args.repo == "sample-repo"
    assert args.output == "graph.pkl"
    assert args.print_summary is True
