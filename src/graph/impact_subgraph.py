"""
Bidirectional BFS impact subgraph extraction for PR-aware context slicing.

Given:
- Directed call graph G (caller -> callee)
- Anchor nodes (modified functions)
- Upstream depth (k_up): traverse reverse edges (callers)
- Downstream depth (k_down): traverse forward edges (callees)

Returns:
- G': induced subgraph over selected nodes
- node_order: deterministic BFS-based node list (anchors first, then BFS expansion)

Also includes a small visualization helper using networkx/matplotlib.
"""

from __future__ import annotations

from collections import deque
from time import perf_counter
from typing import Iterable, List, Sequence, Tuple

import networkx as nx


def _sorted_neighbors(nodes: Iterable[str]) -> List[str]:
    """Deterministic neighbor ordering."""
    return sorted(nodes, key=str)


def _bfs_limited(
    graph: nx.DiGraph,
    start: str,
    max_depth: int,
    reverse: bool = False,
) -> List[str]:
    """
    Depth-limited BFS from `start`.

    Parameters
    ----------
    graph:
        Directed graph.
    start:
        Starting node.
    max_depth:
        Maximum depth to traverse (0 returns only start).
    reverse:
        If True, traverse predecessors (upstream).
        If False, traverse successors (downstream).

    Returns
    -------
    List[str]
        Nodes in deterministic BFS discovery order, including `start`.
    """
    if start not in graph:
        return []

    visited = {start}
    order = [start]
    q: deque[Tuple[str, int]] = deque([(start, 0)])

    while q:
        node, depth = q.popleft()
        if depth >= max_depth:
            continue

        if reverse:
            neigh_iter = graph.predecessors(node)
        else:
            neigh_iter = graph.successors(node)

        for nbr in _sorted_neighbors(neigh_iter):
            if nbr in visited:
                continue
            visited.add(nbr)
            order.append(nbr)
            q.append((nbr, depth + 1))

    return order


def extract_impact_subgraph(
    G: nx.DiGraph,
    anchors: Iterable[str],
    k_up: int = 2,
    k_down: int = 3,
    max_nodes: int = 100,
    max_edges: int | None = None,
    max_per_anchor: int | None = None,
    time_ms: int | None = None,
) -> Tuple[nx.DiGraph, List[str]]:
    """
    Extract PR impact subgraph using bidirectional BFS.

    Logic:
    - For each anchor `a`:
        - upstream BFS on reverse edges to depth `k_up`
        - downstream BFS on forward edges to depth `k_down`
    - Union all discovered nodes.
    - If node count exceeds `max_nodes`, prune by earliest deterministic BFS order.
    - Return induced subgraph and final node order.

    Parameters
    ----------
    G:
        Input directed graph.
    anchors:
        Modified function node IDs.
    k_up:
        Upstream traversal depth (callers).
    k_down:
        Downstream traversal depth (callees).
    max_nodes:
        Maximum nodes in result (token-bound proxy).

    Returns
    -------
    (nx.DiGraph, List[str])
        - Induced subgraph over selected nodes.
        - Deterministic node list in BFS order.
    """
    if max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")
    if k_up < 0 or k_down < 0:
        raise ValueError("k_up and k_down must be >= 0")
    if max_edges is not None and max_edges < 1:
        raise ValueError("max_edges must be >= 1 when provided")
    if max_per_anchor is not None and max_per_anchor < 1:
        raise ValueError("max_per_anchor must be >= 1 when provided")
    if time_ms is not None and time_ms < 1:
        raise ValueError("time_ms must be >= 1 when provided")

    # Keep only anchors present in graph, deterministic order
    anchor_list = _sorted_neighbors(a for a in anchors if a in G)
    if not anchor_list:
        empty = nx.DiGraph()
        empty.graph["cutoff_reasons"] = ()
        return empty, []

    selected: set[str] = set()
    selected_edges: set[tuple[str, str]] = set()
    order: List[str] = []
    cutoff_reasons: List[str] = []
    deadline = (
        perf_counter() + (float(time_ms) / 1000.0) if time_ms is not None else None
    )

    def add_in_order(nodes: Sequence[str]) -> None:
        for n in nodes:
            if n not in selected:
                selected.add(n)
                order.append(n)

    def time_budget_hit() -> bool:
        return deadline is not None and perf_counter() >= deadline

    # Anchors first (deterministic)
    add_in_order(anchor_list)

    # Bidirectional expansions per anchor with structural/time limits.
    for a in anchor_list:
        per_anchor_added = 0
        stop_due_to_time = False

        visited_up = {(a, 0)}
        q_up: deque[tuple[str, int]] = deque([(a, 0)])
        while q_up:
            if time_budget_hit():
                cutoff_reasons.append(f"TIME_BUDGET_EXCEEDED(anchor={a},direction=UP)")
                stop_due_to_time = True
                break

            node, depth = q_up.popleft()
            if depth >= k_up:
                continue

            for caller in _sorted_neighbors(G.predecessors(node)):
                state = (caller, depth + 1)
                if state in visited_up:
                    continue
                visited_up.add(state)

                if caller not in selected:
                    if len(selected) >= max_nodes:
                        cutoff_reasons.append(
                            f"MAX_NODES_REACHED(anchor={a},direction=UP)"
                        )
                        q_up.clear()
                        break
                    if (
                        max_per_anchor is not None
                        and per_anchor_added >= max_per_anchor
                    ):
                        cutoff_reasons.append(
                            f"MAX_PER_ANCHOR_REACHED(anchor={a},direction=UP)"
                        )
                        q_up.clear()
                        break
                    selected.add(caller)
                    order.append(caller)
                    per_anchor_added += 1

                edge = (caller, node)
                if edge not in selected_edges:
                    if max_edges is not None and len(selected_edges) >= max_edges:
                        cutoff_reasons.append(
                            f"MAX_EDGES_REACHED(anchor={a},direction=UP)"
                        )
                        q_up.clear()
                        break
                    selected_edges.add(edge)

                q_up.append((caller, depth + 1))

        if stop_due_to_time:
            break

        visited_down = {(a, 0)}
        q_down: deque[tuple[str, int]] = deque([(a, 0)])
        while q_down:
            if time_budget_hit():
                cutoff_reasons.append(
                    f"TIME_BUDGET_EXCEEDED(anchor={a},direction=DOWN)"
                )
                stop_due_to_time = True
                break

            node, depth = q_down.popleft()
            if depth >= k_down:
                continue

            for callee in _sorted_neighbors(G.successors(node)):
                state = (callee, depth + 1)
                if state in visited_down:
                    continue
                visited_down.add(state)

                if callee not in selected:
                    if len(selected) >= max_nodes:
                        cutoff_reasons.append(
                            f"MAX_NODES_REACHED(anchor={a},direction=DOWN)"
                        )
                        q_down.clear()
                        break
                    if (
                        max_per_anchor is not None
                        and per_anchor_added >= max_per_anchor
                    ):
                        cutoff_reasons.append(
                            f"MAX_PER_ANCHOR_REACHED(anchor={a},direction=DOWN)"
                        )
                        q_down.clear()
                        break
                    selected.add(callee)
                    order.append(callee)
                    per_anchor_added += 1

                edge = (node, callee)
                if edge not in selected_edges:
                    if max_edges is not None and len(selected_edges) >= max_edges:
                        cutoff_reasons.append(
                            f"MAX_EDGES_REACHED(anchor={a},direction=DOWN)"
                        )
                        q_down.clear()
                        break
                    selected_edges.add(edge)

                q_down.append((callee, depth + 1))

        if stop_due_to_time:
            break

    # Prune if token-bound exceeded: keep earliest BFS discoveries
    if len(order) > max_nodes:
        cutoff_reasons.append("MAX_NODES_REACHED(global_prune)")
        order = order[:max_nodes]
        selected = set(order)

    G_prime = nx.DiGraph()
    for n in order:
        G_prime.add_node(n, **G.nodes[n])

    allowed = set(order)
    for u, v in sorted(selected_edges, key=lambda x: (str(x[0]), str(x[1]))):
        if u in allowed and v in allowed and G.has_edge(u, v):
            G_prime.add_edge(u, v, **G.get_edge_data(u, v, default={}))

    G_prime.graph["cutoff_reasons"] = tuple(cutoff_reasons)
    G_prime.graph["budget"] = {
        "k_up": k_up,
        "k_down": k_down,
        "max_nodes": max_nodes,
        "max_edges": max_edges,
        "max_per_anchor": max_per_anchor,
        "time_ms": time_ms,
    }

    return G_prime, order


def draw_impact_subgraph(
    G_prime: nx.DiGraph,
    node_order: Sequence[str] | None = None,
    *,
    figsize: Tuple[int, int] = (10, 7),
    with_labels: bool = True,
    node_size: int = 1000,
    font_size: int = 8,
    arrows: bool = True,
    layout_seed: int = 42,
) -> None:
    """
    Visualize impact subgraph with deterministic node placement.

    Notes
    -----
    - Uses `spring_layout` with fixed seed for deterministic layout.
    - If `node_order` is provided, labels prefer node `name` attribute when present.
    """
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError(
            "matplotlib is required for visualization. Install it to use draw_impact_subgraph."
        ) from exc

    plt.figure(figsize=figsize)

    # Deterministic layout
    pos = nx.spring_layout(G_prime, seed=layout_seed)

    labels = None
    if with_labels:
        labels = {}
        if node_order is None:
            ordered_nodes = list(G_prime.nodes())
        else:
            ordered_nodes = [n for n in node_order if n in G_prime]

        for n in ordered_nodes:
            node_name = G_prime.nodes[n].get("name")
            labels[n] = str(node_name) if node_name else str(n)

        # Include any nodes not present in node_order
        for n in G_prime.nodes():
            labels.setdefault(n, str(G_prime.nodes[n].get("name", n)))

    draw_kwargs = {
        "with_labels": with_labels,
        "labels": labels,
        "node_size": node_size,
        "font_size": font_size,
    }
    if arrows:
        draw_kwargs["arrows"] = True

    nx.draw(
        G_prime,
        pos=pos,
        **draw_kwargs,
    )

    plt.title("PR Impact Subgraph (Bidirectional BFS)")
    plt.tight_layout()
    plt.show()


__all__ = ["extract_impact_subgraph", "draw_impact_subgraph"]
