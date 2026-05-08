from __future__ import annotations

from pathlib import Path

from src.call_graph_builder import build_call_graph
from src.graph_updater import incremental_update
from src.ingestion.diff_parser import parse_unified_diff


def _node_id(path: str, fqn: str) -> str:
    return f"{path}::{fqn}"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_incremental_update_refreshes_only_modified_file_nodes_and_edges(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "a.py",
        "def util():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    return util()\n",
    )
    _write(
        tmp_path / "b.py",
        "def helper():\n"
        "    return 2\n",
    )

    call_graph = build_call_graph(tmp_path)

    _write(
        tmp_path / "a.py",
        "def util():\n"
        "    return 1\n"
        "\n"
        "def helper_local():\n"
        "    return util()\n"
        "\n"
        "def run():\n"
        "    return helper_local()\n",
    )

    diff = parse_unified_diff(
        """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -1,5 +1,8 @@
 def util():
     return 1
 
+def helper_local():
+    return util()
+
 def run():
-    return util()
+    return helper_local()
"""
    )

    updated_graph, delta = incremental_update(call_graph, diff, tmp_path)

    assert _node_id("a.py", "helper_local") in updated_graph.graph
    assert updated_graph.graph.has_edge(
        _node_id("a.py", "run"),
        _node_id("a.py", "helper_local"),
    )
    assert updated_graph.graph.has_edge(
        _node_id("a.py", "helper_local"),
        _node_id("a.py", "util"),
    )
    assert _node_id("b.py", "helper") in updated_graph.graph  # untouched file remains
    assert _node_id("a.py", "helper_local") in delta.added_nodes
    assert (_node_id("a.py", "run"), _node_id("a.py", "util")) in delta.removed_edges
    assert delta.unchanged_nodes > 0


def test_incremental_update_removes_deleted_file_nodes(tmp_path: Path) -> None:
    _write(
        tmp_path / "obsolete.py",
        "def old():\n"
        "    return 1\n",
    )
    _write(
        tmp_path / "keep.py",
        "def keep():\n"
        "    return 2\n",
    )

    call_graph = build_call_graph(tmp_path)
    (tmp_path / "obsolete.py").unlink()

    diff = parse_unified_diff(
        """\
diff --git a/obsolete.py b/obsolete.py
deleted file mode 100644
index 1111111..0000000
--- a/obsolete.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def old():
-    return 1
"""
    )

    updated_graph, delta = incremental_update(call_graph, diff, tmp_path)

    assert _node_id("obsolete.py", "old") not in updated_graph.graph
    assert _node_id("keep.py", "keep") in updated_graph.graph
    assert delta.removed_nodes == [_node_id("obsolete.py", "old")]


def test_incremental_update_handles_renamed_files_by_updating_node_paths(
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "old_name.py"
    new_path = tmp_path / "new_name.py"
    _write(
        old_path,
        "def run():\n"
        "    return 1\n",
    )

    call_graph = build_call_graph(tmp_path)
    old_path.rename(new_path)

    diff = parse_unified_diff(
        """\
diff --git a/old_name.py b/new_name.py
similarity index 100%
rename from old_name.py
rename to new_name.py
"""
    )

    updated_graph, delta = incremental_update(call_graph, diff, tmp_path)

    run_id = _node_id("new_name.py", "run")

    assert run_id in updated_graph.graph
    assert (
        updated_graph.graph.nodes[run_id]["file_path"]
        .replace("\\", "/")
        .endswith("new_name.py")
    )
    assert delta.removed_nodes == []
