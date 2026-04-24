"""
Explicit impact-subgraph datamodel and role enrichment wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import networkx as nx

from src.graph.impact_subgraph import extract_impact_subgraph as _extract_impact_subgraph


NodeRole = Literal["anchor", "caller", "callee", "shared"]


@dataclass(frozen=True)
class ImpactSubgraphNode:
    node_id: str
    role: NodeRole
    depth_up: int | None
    depth_down: int | None


@dataclass(frozen=True)
class SubgraphStats:
    node_count: int
    edge_count: int
    anchor_count: int
    caller_count: int
    callee_count: int
    shared_count: int
    cutoff_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ImpactSubgraph:
    graph: nx.DiGraph
    node_order: list[str]
    nodes: list[ImpactSubgraphNode]
    stats: SubgraphStats


def build_impact_subgraph(
    graph: nx.DiGraph,
    *,
    anchors: set[str] | list[str] | tuple[str, ...],
    k_up: int = 2,
    k_down: int = 3,
    max_nodes: int = 100,
    max_edges: int | None = None,
    max_per_anchor: int | None = None,
    time_ms: int | None = None,
) -> ImpactSubgraph:
    subgraph, node_order = _extract_impact_subgraph(
        graph,
        anchors=anchors,
        k_up=k_up,
        k_down=k_down,
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_per_anchor=max_per_anchor,
        time_ms=time_ms,
    )

    anchor_set = {anchor for anchor in anchors if anchor in subgraph}
    caller_depths = _min_depths_to_targets(
        graph=subgraph.reverse(copy=False),
        starts=anchor_set,
        max_depth=k_up,
    )
    callee_depths = _min_depths_to_targets(
        graph=subgraph,
        starts=anchor_set,
        max_depth=k_down,
    )
    upstream_anchor_hits = _anchor_reach_counts(
        graph=subgraph.reverse(copy=False),
        anchors=anchor_set,
        max_depth=k_up,
    )
    downstream_anchor_hits = _anchor_reach_counts(
        graph=subgraph,
        anchors=anchor_set,
        max_depth=k_down,
    )

    enriched_nodes: list[ImpactSubgraphNode] = []
    caller_count = 0
    callee_count = 0
    shared_count = 0

    for node_id in node_order:
        if node_id in anchor_set:
            role: NodeRole = "anchor"
        else:
            has_up = caller_depths.get(node_id) is not None
            has_down = callee_depths.get(node_id) is not None
            hit_count = upstream_anchor_hits.get(node_id, 0) + downstream_anchor_hits.get(
                node_id, 0
            )
            if hit_count >= 2 or (has_up and has_down):
                role = "shared"
                shared_count += 1
            elif has_up:
                role = "caller"
                caller_count += 1
            else:
                role = "callee"
                callee_count += 1

        enriched_nodes.append(
            ImpactSubgraphNode(
                node_id=node_id,
                role=role,
                depth_up=caller_depths.get(node_id),
                depth_down=callee_depths.get(node_id),
            )
        )

    stats = SubgraphStats(
        node_count=subgraph.number_of_nodes(),
        edge_count=subgraph.number_of_edges(),
        anchor_count=len(anchor_set),
        caller_count=caller_count,
        callee_count=callee_count,
        shared_count=shared_count,
        cutoff_reasons=tuple(subgraph.graph.get("cutoff_reasons", ())),
    )

    return ImpactSubgraph(
        graph=subgraph,
        node_order=node_order,
        nodes=enriched_nodes,
        stats=stats,
    )


def _min_depths_to_targets(
    *,
    graph: nx.DiGraph,
    starts: set[str],
    max_depth: int,
) -> dict[str, int | None]:
    if not starts:
        return {}

    depths: dict[str, int] = {start: 0 for start in starts if start in graph}
    frontier = {start for start in starts if start in graph}

    for depth in range(1, max_depth + 1):
        next_frontier: set[str] = set()
        for node in frontier:
            next_frontier.update(graph.neighbors(node))
        next_frontier -= set(depths)
        if not next_frontier:
            break
        for node in sorted(next_frontier):
            depths[node] = depth
        frontier = next_frontier

    return {node: depth for node, depth in depths.items() if depth > 0}


def _anchor_reach_counts(
    *,
    graph: nx.DiGraph,
    anchors: set[str],
    max_depth: int,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for anchor in anchors:
        depths = _min_depths_to_targets(graph=graph, starts={anchor}, max_depth=max_depth)
        for node in depths:
            counts[node] = counts.get(node, 0) + 1
    return counts


__all__ = [
    "ImpactSubgraph",
    "ImpactSubgraphNode",
    "SubgraphStats",
    "build_impact_subgraph",
]
