from __future__ import annotations

import networkx as nx

from src.linearization.bfs_linearizer import linearize_subgraph


def _make_graph() -> nx.DiGraph:
    """
    Caller -> callee graph.

    upstream1 -> anchor1 -> down1 -> down2
                    |
                    -> shared
    upstream2 -> anchor2 -> down3
                    |
                    -> shared
    """
    g = nx.DiGraph()
    g.add_node(
        "anchor1",
        qualified_name="pkg.mod.anchor1",
        name="anchor1",
        file="a.py",
        start_line=1,
        end_line=3,
        code="def anchor1():\n    return 1",
    )
    g.add_node(
        "anchor2",
        qualified_name="pkg.mod.anchor2",
        name="anchor2",
        file="b.py",
        start_line=10,
        end_line=12,
        code="def anchor2():\n    return 2",
    )
    g.add_node(
        "upstream1",
        qualified_name="pkg.mod.upstream1",
        name="upstream1",
        file="u1.py",
        start_line=5,
        end_line=8,
        code="def upstream1():\n    return anchor1()",
    )
    g.add_node(
        "upstream2",
        qualified_name="pkg.mod.upstream2",
        name="upstream2",
        file="u2.py",
        start_line=5,
        end_line=8,
        code="def upstream2():\n    return anchor2()",
    )
    g.add_node(
        "down1",
        qualified_name="pkg.mod.down1",
        name="down1",
        file="d1.py",
        start_line=20,
        end_line=22,
        code="def down1():\n    return 10",
    )
    g.add_node(
        "down2",
        qualified_name="pkg.mod.down2",
        name="down2",
        file="d2.py",
        start_line=30,
        end_line=31,
        code="def down2():\n    return down1()",
    )
    g.add_node(
        "down3",
        qualified_name="pkg.mod.down3",
        name="down3",
        file="d3.py",
        start_line=40,
        end_line=41,
        code="def down3():\n    return anchor2()",
    )
    g.add_node(
        "shared",
        qualified_name="pkg.mod.shared",
        name="shared",
        file="shared.py",
        start_line=50,
        end_line=53,
        code="def shared():\n    return 42",
    )

    g.add_edges_from(
        [
            ("upstream1", "anchor1"),
            ("upstream2", "anchor2"),
            ("anchor1", "down1"),
            ("down1", "down2"),
            ("anchor1", "shared"),
            ("anchor2", "down3"),
            ("anchor2", "shared"),
        ]
    )
    return g


def _find_pos(text: str, needle: str) -> int:
    pos = text.find(needle)
    assert pos >= 0, f"Missing expected text: {needle}"
    return pos


def test_linearize_subgraph_section_order_and_presence() -> None:
    g = _make_graph()
    pr_diff = "@@ -1,2 +1,3 @@\n-def old(): pass\n+def anchor1():\n+    return 1"

    out = linearize_subgraph(
        g,
        pr_diff=pr_diff,
        anchors={"anchor2", "anchor1"},  # intentionally unordered set
        max_chars=200_000,
    )

    p_diff = _find_pos(out, "## PR DIFF HUNK")
    p_mod = _find_pos(out, "## MODIFIED")
    p_callers = _find_pos(out, "## CALLERS (depth k)")
    p_callees = _find_pos(out, "## CALLEES (depth m)")

    assert p_diff < p_mod < p_callers < p_callees

    # Anchors should be deterministic by sorted node id: anchor1 before anchor2
    p_a1 = _find_pos(out, "### MODIFIED: `pkg.mod.anchor1` `a.py`")
    p_a2 = _find_pos(out, "### MODIFIED: `pkg.mod.anchor2` `b.py`")
    assert p_a1 < p_a2

    # Expected context appears in caller/callee sections
    assert "### CALLERS: `pkg.mod.upstream1` `u1.py`, depth=1" in out
    assert "### CALLERS: `pkg.mod.upstream2` `u2.py`, depth=1" in out
    assert "### CALLEES: `pkg.mod.down1` `d1.py`, depth=1" in out
    assert "### CALLEES: `pkg.mod.down3` `d3.py`, depth=1" in out


def test_linearize_subgraph_includes_diff_hunk_verbatim() -> None:
    g = _make_graph()
    pr_diff = "@@ -10,3 +10,5 @@\n-    old_call()\n+    anchor1()\n+    shared()\n"
    out = linearize_subgraph(g, pr_diff=pr_diff, anchors={"anchor1"}, max_chars=200_000)

    assert "```diff" in out
    assert pr_diff.strip() in out
    assert "## PR DIFF HUNK" in out


def test_linearize_subgraph_budget_enforced_and_truncation_marker() -> None:
    g = _make_graph()
    pr_diff = "@@ -1 +1 @@\n-x\n+y\n"

    out = linearize_subgraph(
        g,
        pr_diff=pr_diff,
        anchors={"anchor1", "anchor2"},
        max_chars=1200,  # force truncation
    )

    assert len(out) <= 1200
    assert "<!-- TRUNCATED: character budget reached -->" in out


def test_linearize_subgraph_small_budget_raises_value_error() -> None:
    g = _make_graph()
    try:
        linearize_subgraph(
            g, pr_diff="@@ -1 +1 @@\n-a\n+b", anchors={"anchor1"}, max_chars=100
        )
        assert False, "Expected ValueError for too-small max_chars"
    except ValueError:
        pass


def test_linearize_subgraph_missing_anchors_emits_empty_modified_note() -> None:
    g = _make_graph()
    out = linearize_subgraph(
        g,
        pr_diff="@@ -1 +1 @@\n-a\n+b",
        anchors={"not_in_graph"},
        max_chars=50_000,
    )

    assert "## MODIFIED" in out
    assert "- None (no anchors found in subgraph)" in out
    assert "## CALLERS (depth k)" in out
    assert "## CALLEES (depth m)" in out


def test_linearize_subgraph_resolves_relative_files_against_repo_root(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    src_file = repo_root / "pkg" / "mod.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("def anchor():\n    return 7\n", encoding="utf-8")

    g = nx.DiGraph()
    g.add_node(
        "anchor",
        qualified_name="pkg.mod.anchor",
        name="anchor",
        file="pkg/mod.py",
        start_line=1,
        end_line=2,
    )

    out = linearize_subgraph(
        g,
        pr_diff="@@ -1 +1 @@\n-a\n+b",
        anchors={"anchor"},
        max_chars=20_000,
        repo_root=str(repo_root),
    )
    assert "def anchor():" in out
