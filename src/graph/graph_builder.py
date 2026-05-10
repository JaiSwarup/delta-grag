"""
Compatibility wrapper over the canonical call-graph builder.

This module preserves the existing CLI/test API surface under ``src.graph`` while
delegating graph extraction to ``src.call_graph_builder``. That keeps one
authoritative graph-construction implementation across the codebase.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import networkx as nx

from src.call_graph_builder import build_call_graph as _build_call_graph_canonical


def build_call_graph(repo_root: Path) -> nx.DiGraph:
    """
    Build a static intra-repo call graph for Python files in ``repo_root``.

    Returns a plain ``networkx.DiGraph`` for backward compatibility.
    """
    canonical = _build_call_graph_canonical(repo_root.resolve())
    graph = canonical.graph.copy()
    _add_compat_labels(graph)
    return graph


def _add_compat_labels(graph: nx.DiGraph) -> None:
    for node_id, data in graph.nodes(data=True):
        if not data.get("qualified_name") and data.get("fqn"):
            data["qualified_name"] = data["fqn"]
        if not data.get("file") and data.get("file_path"):
            data["file"] = data["file_path"]
        if not data.get("name"):
            qualified_name = str(data.get("qualified_name") or "")
            data["name"] = qualified_name.split(".")[-1] if qualified_name else str(node_id)

        if data.get("label"):
            continue
        qn = (
            data.get("qualified_name")
            or data.get("fqn")
            or data.get("name")
            or str(node_id)
        )
        file_path = data.get("file") or data.get("file_path") or "unknown"
        start_line = data.get("start_line")
        if start_line is None:
            data["label"] = f"{qn} ({file_path})"
        else:
            data["label"] = f"{qn} ({file_path}:{start_line})"

    for _, _, data in graph.edges(data=True):
        _normalize_call_line(data)


def _normalize_call_line(attrs: dict[str, Any]) -> None:
    if "call_line" in attrs:
        return
    if "call_site_line" in attrs:
        attrs["call_line"] = attrs["call_site_line"]


def save_graph(graph: nx.DiGraph, output_path: Path) -> None:
    """
    Save graph to .pkl.

    Uses `pickle.dump` when available
    """
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import pickle

    with output_path.open("wb") as f:
        pickle.dump(graph, f, protocol=pickle.HIGHEST_PROTOCOL)


def print_summary(graph: nx.DiGraph) -> None:
    print(f"Nodes: {graph.number_of_nodes()}")
    print(f"Edges: {graph.number_of_edges()}")

    # Deterministic-ish edge dump
    for u, v, data in sorted(
        graph.edges(data=True), key=lambda e: (str(e[0]), str(e[1]))
    ):
        u_label = graph.nodes[u].get("label", u)
        v_label = graph.nodes[v].get("label", v)
        line = data.get("call_line")
        if line is not None:
            print(f"{u_label} -> {v_label}  [line={line}]")
        else:
            print(f"{u_label} -> {v_label}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build static intra-repo Python call graph and save as .pkl."
    )
    p.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Path to repository root.",
    )
    p.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output .pkl file path.",
    )
    p.add_argument(
        "--print-summary",
        action="store_true",
        help="Print graph node/edge summary and edge list.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    repo_root = Path(args.repo).resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise SystemExit(f"Invalid --repo path: {repo_root}")

    output_path = Path(args.output).resolve()
    graph = build_call_graph(repo_root)
    save_graph(graph, output_path)

    if args.print_summary:
        print_summary(graph)

    print(f"Saved call graph to: {output_path}")


if __name__ == "__main__":
    main()
