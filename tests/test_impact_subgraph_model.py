from __future__ import annotations

import networkx as nx

from src.impact_subgraph import build_impact_subgraph


def _build_test_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_edges_from(
        [
            ("u2", "u1"),
            ("u1", "a1"),
            ("a1", "d1"),
            ("d1", "d2"),
            ("a1", "shared"),
            ("a2", "shared"),
            ("u3", "a2"),
            ("a2", "d4"),
        ]
    )
    return g


def test_build_impact_subgraph_enriches_node_roles_and_depths() -> None:
    graph = _build_test_graph()

    result = build_impact_subgraph(
        graph,
        anchors={"a1", "a2"},
        k_up=1,
        k_down=1,
        max_nodes=20,
    )

    roles = {node.node_id: node.role for node in result.nodes}
    assert roles["a1"] == "anchor"
    assert roles["a2"] == "anchor"
    assert roles["u1"] == "caller"
    assert roles["u3"] == "caller"
    assert roles["d1"] == "callee"
    assert roles["d4"] == "callee"
    assert roles["shared"] == "shared"

    depths = {node.node_id: (node.depth_up, node.depth_down) for node in result.nodes}
    assert depths["u1"] == (1, None)
    assert depths["d1"] == (None, 1)
    assert depths["shared"] == (None, 1)


def test_build_impact_subgraph_stats_reflect_role_counts() -> None:
    graph = _build_test_graph()

    result = build_impact_subgraph(
        graph,
        anchors={"a1", "a2"},
        k_up=1,
        k_down=1,
        max_nodes=20,
    )

    assert result.stats.node_count == result.graph.number_of_nodes()
    assert result.stats.edge_count == result.graph.number_of_edges()
    assert result.stats.anchor_count == 2
    assert result.stats.caller_count == 2
    assert result.stats.callee_count == 2
    assert result.stats.shared_count == 1


def test_build_impact_subgraph_preserves_cutoff_reasons() -> None:
    graph = _build_test_graph()

    result = build_impact_subgraph(
        graph,
        anchors={"a1"},
        k_up=3,
        k_down=3,
        max_nodes=3,
    )

    assert result.stats.cutoff_reasons
    assert any("MAX_NODES_REACHED" in reason for reason in result.stats.cutoff_reasons)
