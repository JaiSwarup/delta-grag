from __future__ import annotations

import networkx as nx

from src.graph.impact_subgraph import extract_impact_subgraph


def _build_test_graph() -> nx.DiGraph:
    """
    Build a directed graph where edges mean caller -> callee.

    Structure:
      u2 -> u1 -> a1 -> d1 -> d2 -> d3
                       \
                        -> shared
      u3 -> a2 -> d4
             \
              -> shared
    """
    g = nx.DiGraph()
    g.add_edges_from(
        [
            ("u2", "u1"),
            ("u1", "a1"),
            ("a1", "d1"),
            ("d1", "d2"),
            ("d2", "d3"),
            ("a1", "shared"),
            ("u3", "a2"),
            ("a2", "d4"),
            ("a2", "shared"),
        ]
    )
    return g


def test_bidirectional_bfs_single_anchor_respects_depths() -> None:
    g = _build_test_graph()

    g_prime, order = extract_impact_subgraph(
        g,
        anchors={"a1"},
        k_up=2,
        k_down=2,
        max_nodes=100,
    )

    # Upstream(depth=2): a1, u1, u2
    # Downstream(depth=2): a1, d1, shared, d2
    expected_nodes = {"a1", "u1", "u2", "d1", "d2", "shared"}

    assert set(g_prime.nodes()) == expected_nodes
    assert set(order) == expected_nodes
    assert "d3" not in g_prime.nodes()  # beyond k_down=2
    assert "a1" in order and order[0] == "a1"


def test_multi_anchor_union_and_deterministic_anchor_order() -> None:
    g = _build_test_graph()

    # Anchors provided as set; implementation should sort deterministically.
    g_prime, order = extract_impact_subgraph(
        g,
        anchors={"a2", "a1"},
        k_up=1,
        k_down=1,
        max_nodes=100,
    )

    # For a1 (up1/down1): a1, u1, d1, shared
    # For a2 (up1/down1): a2, u3, d4, shared
    expected_nodes = {"a1", "u1", "d1", "shared", "a2", "u3", "d4"}

    assert set(g_prime.nodes()) == expected_nodes
    assert set(order) == expected_nodes

    # Deterministic: sorted anchors means a1 comes before a2.
    assert order[0] == "a1"
    assert order.index("a1") < order.index("a2")


def test_pruning_is_deterministic_and_keeps_prefix_order() -> None:
    g = _build_test_graph()

    _, full_order = extract_impact_subgraph(
        g,
        anchors={"a1", "a2"},
        k_up=2,
        k_down=3,
        max_nodes=100,
    )

    max_nodes = 5
    g_prime_pruned, pruned_order = extract_impact_subgraph(
        g,
        anchors={"a1", "a2"},
        k_up=2,
        k_down=3,
        max_nodes=max_nodes,
    )

    assert len(pruned_order) == max_nodes
    assert pruned_order == full_order[:max_nodes]
    assert list(g_prime_pruned.nodes()) == pruned_order


def test_invalid_params_raise_value_error() -> None:
    g = _build_test_graph()

    for kwargs in (
        {"k_up": -1, "k_down": 1, "max_nodes": 10},
        {"k_up": 1, "k_down": -1, "max_nodes": 10},
        {"k_up": 1, "k_down": 1, "max_nodes": 0},
        {"k_up": 1, "k_down": 1, "max_nodes": 10, "max_edges": 0},
        {"k_up": 1, "k_down": 1, "max_nodes": 10, "max_per_anchor": 0},
        {"k_up": 1, "k_down": 1, "max_nodes": 10, "time_ms": 0},
    ):
        try:
            extract_impact_subgraph(g, anchors={"a1"}, **kwargs)
            assert False, f"Expected ValueError for args: {kwargs}"
        except ValueError:
            pass


def test_missing_anchors_return_empty_subgraph_and_order() -> None:
    g = _build_test_graph()

    g_prime, order = extract_impact_subgraph(
        g,
        anchors={"not_in_graph"},
        k_up=2,
        k_down=2,
        max_nodes=10,
    )

    assert order == []
    assert g_prime.number_of_nodes() == 0
    assert g_prime.number_of_edges() == 0


def test_subgraph_node_insertion_order_matches_returned_order() -> None:
    g = _build_test_graph()

    g_prime, order = extract_impact_subgraph(
        g,
        anchors={"a1", "a2"},
        k_up=2,
        k_down=3,
        max_nodes=6,
    )

    assert order, "Expected non-empty deterministic order"
    assert list(g_prime.nodes()) == order


def test_max_edges_cap_limits_result_edges_and_emits_cutoff_reason() -> None:
    g = _build_test_graph()
    g_prime, _ = extract_impact_subgraph(
        g,
        anchors={"a1"},
        k_up=3,
        k_down=3,
        max_nodes=100,
        max_edges=2,
    )
    assert g_prime.number_of_edges() <= 2
    reasons = g_prime.graph.get("cutoff_reasons", ())
    assert any("MAX_EDGES_REACHED" in r for r in reasons)


def test_max_per_anchor_cap_limits_expansion() -> None:
    g = _build_test_graph()
    g_prime, order = extract_impact_subgraph(
        g,
        anchors={"a1"},
        k_up=3,
        k_down=3,
        max_nodes=100,
        max_per_anchor=2,
    )
    # anchor + at most two additional nodes for that anchor expansion
    assert len(order) <= 3
    reasons = g_prime.graph.get("cutoff_reasons", ())
    assert any("MAX_PER_ANCHOR_REACHED" in r for r in reasons)


def test_time_budget_argument_supported_and_recorded() -> None:
    g = _build_test_graph()
    g_prime, _ = extract_impact_subgraph(
        g,
        anchors={"a1", "a2"},
        k_up=5,
        k_down=5,
        max_nodes=100,
        time_ms=1,
    )
    assert g_prime.graph.get("budget", {}).get("time_ms") == 1
