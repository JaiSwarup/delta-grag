"""
Graph builder CLI for static intra-repo Python call graph construction.

This module orchestrates:
1) Repository extraction (Tree-sitter based)
2) Static intra-repo call resolution
3) NetworkX DiGraph creation
4) Persistence to .pkl

Expected companion module:
    src/graph/call_extractor.py

Requirements:
    pip install tree-sitter tree-sitter-python networkx
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

import networkx as nx

from .call_extractor import (
    ImportAlias,
    build_symbol_lookup,
    extract_repo,
    resolve_callee_symbol_ids,
)


def build_call_graph(repo_root: Path) -> nx.DiGraph:
    """
    Build a static intra-repo call graph for Python files in `repo_root`.

    Node id:
        symbol_id from extractor (stable-ish textual id)

    Node attributes:
        - name
        - qualified_name
        - file
        - start_line
        - end_line
        - is_nested
        - is_lambda
        - label

    Edge direction:
        caller -> callee
    """
    repo_root = repo_root.resolve()
    extraction = extract_repo(repo_root)

    symbols = extraction.all_symbols()
    calls = extraction.all_calls()
    imports = extraction.all_imports()

    # Lookup tables for call resolution
    by_qualified_name, by_simple_name, module_to_symbol_id = build_symbol_lookup(
        symbols
    )

    # file -> {simple_name: [symbol_id, ...]}
    # Keep list shape to match resolver contract in call_extractor.
    file_symbol_map: Dict[str, Dict[str, List[str]]] = {}
    for s in symbols:
        file_symbol_map.setdefault(s.file_path, {}).setdefault(s.name, []).append(
            s.symbol_id
        )

    # file -> list[ImportAlias]
    imports_by_file: Dict[str, List[ImportAlias]] = {}
    for imp in imports:
        imports_by_file.setdefault(imp.file_path, []).append(imp)

    g = nx.DiGraph()

    # Add nodes
    symbol_by_id = {}
    for s in symbols:
        symbol_by_id[s.symbol_id] = s
        g.add_node(
            s.symbol_id,
            name=s.name,
            qualified_name=s.qualified_name,
            file=s.file_path,
            start_line=s.start_line,
            end_line=s.end_line,
            is_nested=s.is_nested,
            is_lambda=s.is_lambda,
            label=f"{s.qualified_name} ({s.file_path}:{s.start_line})",
        )

    # Add edges caller -> callee
    caller_qualified_name_by_id = {s.symbol_id: s.qualified_name for s in symbols}
    for cs in calls:
        if cs.caller_symbol_id not in symbol_by_id:
            continue

        callee_ids = resolve_callee_symbol_ids(
            call=cs,
            file_local_defs=file_symbol_map,
            global_simple=by_simple_name,
            imports_by_file=imports_by_file,
            module_member_to_symbol=module_to_symbol_id,
            caller_qualified_name_by_id=caller_qualified_name_by_id,
        )

        for callee_id in callee_ids:
            if callee_id in symbol_by_id and callee_id != cs.caller_symbol_id:
                g.add_edge(cs.caller_symbol_id, callee_id, call_line=cs.line)

    return g


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
