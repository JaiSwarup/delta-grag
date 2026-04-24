from __future__ import annotations

import networkx as nx

from src.ingestion.anchor_resolver import (
    ChangedHunk,
    resolve_anchors,
    resolve_anchors_from_diff_map,
    resolve_anchors_from_diff_text,
    resolve_anchors_from_parsed_diff,
)
from src.ingestion.diff_parser import parse_unified_diff


def _build_graph() -> nx.DiGraph:
    g = nx.DiGraph()

    # file a.py
    g.add_node(
        "a:outer",
        file="a.py",
        start_line=1,
        end_line=40,
        qualified_name="pkg.a.outer",
        name="outer",
    )
    g.add_node(
        "a:inner",
        file="a.py",
        start_line=10,
        end_line=20,
        qualified_name="pkg.a.outer.inner",
        name="inner",
    )
    g.add_node(
        "a:tail",
        file="a.py",
        start_line=50,
        end_line=60,
        qualified_name="pkg.a.tail",
        name="tail",
    )

    # file b.py
    g.add_node(
        "b:alpha",
        file="b.py",
        start_line=5,
        end_line=12,
        qualified_name="pkg.b.alpha",
        name="alpha",
    )
    g.add_node(
        "b:beta",
        file="b.py",
        start_line=30,
        end_line=35,
        qualified_name="pkg.b.beta",
        name="beta",
    )

    return g


def test_enclosure_prefers_smallest_enclosing_function() -> None:
    g = _build_graph()

    # This hunk is enclosed by both outer(1-40) and inner(10-20).
    # Resolver should pick smallest enclosing span => inner.
    hunks = [ChangedHunk(file_path="a.py", start_line=12, end_line=13, hunk_id="h1")]
    res = resolve_anchors(g, hunks)

    assert res.anchor_node_ids == ["a:inner"]
    assert res.unresolved_hunks == []
    assert res.hunk_to_anchor["h1"] == "a:inner"


def test_enclosure_normalizes_paths_and_line_order() -> None:
    g = _build_graph()

    # Backslashes + reversed line range should be normalized.
    hunks = [ChangedHunk(file_path=r"a.py", start_line=20, end_line=10, hunk_id="h2")]
    res = resolve_anchors(g, hunks)

    # normalized to [10,20], enclosed by both outer and inner => pick inner
    assert res.anchor_node_ids == ["a:inner"]
    assert res.hunk_to_anchor["h2"] == "a:inner"


def test_nearest_fallback_same_file_when_no_enclosing_node() -> None:
    g = _build_graph()

    # No enclosure in a.py (line 45), nearest should be tail(50-60) distance=5,
    # outer(1-40) also distance=5; tie-break prefers smaller span => tail.
    hunks = [ChangedHunk(file_path="a.py", start_line=45, end_line=45, hunk_id="h3")]
    res = resolve_anchors(g, hunks)

    assert res.anchor_node_ids == ["a:tail"]
    assert res.hunk_to_anchor["h3"] == "a:tail"
    assert res.unresolved_hunks == []


def test_unresolved_when_file_has_no_symbols() -> None:
    g = _build_graph()

    hunks = [
        ChangedHunk(file_path="missing.py", start_line=1, end_line=2, hunk_id="h4")
    ]
    res = resolve_anchors(g, hunks)

    assert res.anchor_node_ids == []
    assert len(res.unresolved_hunks) == 1
    assert res.unresolved_hunks[0].file_path == "missing.py"
    assert res.hunk_to_anchor == {}


def test_deduplicates_anchor_ids_preserving_resolution_order() -> None:
    g = _build_graph()

    # Both hunks resolve to the same anchor "a:inner"
    hunks = [
        ChangedHunk(file_path="a.py", start_line=12, end_line=12, hunk_id="h5"),
        ChangedHunk(file_path="a.py", start_line=18, end_line=18, hunk_id="h6"),
    ]
    res = resolve_anchors(g, hunks)

    assert res.anchor_node_ids == ["a:inner"]
    assert res.hunk_to_anchor["h5"] == "a:inner"
    assert res.hunk_to_anchor["h6"] == "a:inner"


def test_resolve_from_diff_map_happy_path() -> None:
    g = _build_graph()

    diff_map = {
        "b.py": [(6, 6), (31, 31)],  # should resolve to b:alpha and b:beta
        "a.py": [(12, 13)],  # should resolve to a:inner
    }

    res = resolve_anchors_from_diff_map(
        g,
        diff_map,
        pr_metadata={"pr_id": 123, "title": "test"},
    )

    # Deterministic by sorted file path then span order: a.py first, then b.py spans
    assert res.anchor_node_ids == ["a:inner", "b:alpha", "b:beta"]
    assert res.pr_metadata["pr_id"] == 123
    assert res.unresolved_hunks == []


def test_resolve_anchors_from_parsed_diff_happy_path() -> None:
    g = _build_graph()

    diff_text = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -11,2 +11,2 @@
-    old_inner()
+    new_inner()
diff --git a/b.py b/b.py
index 3333333..4444444 100644
--- a/b.py
+++ b/b.py
@@ -31,1 +31,1 @@
-    old_beta()
+    new_beta()
"""
    parsed = parse_unified_diff(diff_text)

    res = resolve_anchors_from_parsed_diff(
        g,
        parsed,
        pr_metadata={"source": "parsed"},
    )

    assert res.anchor_node_ids == ["a:inner", "b:beta"]
    assert res.pr_metadata["source"] == "parsed"
    assert res.unresolved_hunks == []


def test_resolve_anchors_from_diff_text_happy_path() -> None:
    g = _build_graph()

    diff_text = """\
diff --git a/b.py b/b.py
index 3333333..4444444 100644
--- a/b.py
+++ b/b.py
@@ -6,1 +6,1 @@
-    old_alpha()
+    new_alpha()
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -55,1 +55,1 @@
-    old_tail()
+    new_tail()
"""
    res = resolve_anchors_from_diff_text(
        g,
        diff_text,
        pr_metadata={"source": "text"},
    )

    # Deterministic ordering by file path then hunk span => a.py first, then b.py
    assert res.anchor_node_ids == ["a:tail", "b:alpha"]
    assert res.pr_metadata["source"] == "text"
    assert res.unresolved_hunks == []


def test_resolve_anchors_from_parsed_diff_none_returns_empty() -> None:
    g = _build_graph()
    res = resolve_anchors_from_parsed_diff(g, None)
    assert res.anchor_node_ids == []
    assert res.changed_hunks == []
    assert res.unresolved_hunks == []


def test_resolve_anchors_from_diff_text_type_error() -> None:
    g = _build_graph()
    try:
        resolve_anchors_from_diff_text(g, 123)  # type: ignore[arg-type]
        assert False, "Expected TypeError for non-string diff_text"
    except TypeError:
        pass
