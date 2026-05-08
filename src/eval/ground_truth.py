"""Deterministic structural ground truth for LLM-free PR evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import networkx as nx

from src.graph.impact_subgraph import extract_impact_subgraph
from src.ingestion.anchor_resolver import AnchorSet, resolve_anchors_from_parsed_diff
from src.ingestion.diff_parser import DiffParseResult


@dataclass(frozen=True)
class StructuralGroundTruth:
    changed_files: tuple[str, ...]
    anchor_fqns: tuple[str, ...]
    impacted_fqns: tuple[str, ...]
    cross_file_fqns: tuple[str, ...]
    known_function_registry: tuple[str, ...]
    unresolved_hunks: int


def build_structural_ground_truth(
    *,
    graph: nx.DiGraph,
    parsed_diff: DiffParseResult,
    k_up: int = 1,
    k_down: int = 1,
    max_nodes: int = 250,
) -> tuple[StructuralGroundTruth, AnchorSet]:
    anchors = resolve_anchors_from_parsed_diff(
        graph,
        parsed_diff,
        file_attr="file_path",
        qualified_name_attr="fqn",
    )
    impact_graph, node_order = extract_impact_subgraph(
        graph,
        anchors=anchors.anchor_node_ids,
        k_up=k_up,
        k_down=k_down,
        max_nodes=max_nodes,
    )
    changed_files = tuple(parsed_diff.changed_files)
    changed_file_set = {_normalize_path(path) for path in changed_files}
    impacted = tuple(_node_fqn(graph, node_id) for node_id in node_order)
    cross_file = tuple(
        fqn
        for node_id, fqn in zip(node_order, impacted)
        if _node_file(graph, node_id) not in changed_file_set
    )
    registry = tuple(sorted(_node_fqn(graph, node_id) for node_id in graph.nodes))
    return (
        StructuralGroundTruth(
            changed_files=changed_files,
            anchor_fqns=tuple(_node_fqn(graph, node_id) for node_id in anchors.anchor_node_ids),
            impacted_fqns=tuple(dict.fromkeys(impacted)),
            cross_file_fqns=tuple(dict.fromkeys(cross_file)),
            known_function_registry=registry,
            unresolved_hunks=len(anchors.unresolved_hunks),
        ),
        anchors,
    )


def fqn_for_nodes(graph: nx.DiGraph, node_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_node_fqn(graph, node_id) for node_id in node_ids))


def file_for_nodes(graph: nx.DiGraph, node_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_node_file(graph, node_id) for node_id in node_ids))


def _node_fqn(graph: nx.DiGraph, node_id: str) -> str:
    data = graph.nodes[node_id]
    return str(data.get("fqn") or data.get("qualified_name") or node_id)


def _node_file(graph: nx.DiGraph, node_id: str) -> str:
    data = graph.nodes[node_id]
    return _normalize_path(str(data.get("file_path") or data.get("file") or ""))


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


__all__ = [
    "StructuralGroundTruth",
    "build_structural_ground_truth",
    "file_for_nodes",
    "fqn_for_nodes",
]
