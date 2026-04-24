"""
Incremental call graph update for modified repository files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Sequence

from src.ast_extractor import FunctionNode, extract_functions
from src.call_extractor import build_import_map, extract_call_edges
from src.call_graph_builder import CallGraph
from src.ingestion.diff_parser import DiffParseResult, FileDiff
from src.repo_manager import RepoSnapshot


@dataclass(frozen=True)
class GraphDelta:
    added_nodes: list[str]
    removed_nodes: list[str]
    added_edges: list[tuple[str, str]]
    removed_edges: list[tuple[str, str]]
    unchanged_nodes: int
    update_time_ms: float


def incremental_update(
    call_graph: CallGraph,
    diff_hunks: DiffParseResult | Sequence[FileDiff],
    snapshot: RepoSnapshot | str | Path,
) -> tuple[CallGraph, GraphDelta]:
    started_at = perf_counter()
    root = _resolve_snapshot_root(snapshot)
    file_diffs = _normalize_file_diffs(diff_hunks)

    updated_graph = CallGraph()
    updated_graph.graph = call_graph.graph.copy()

    current_python_files = _collect_current_python_files(file_diffs, root)
    existing_nodes_by_path = _group_nodes_by_rel_path(updated_graph, root)

    old_node_ids_by_current_path: dict[str, set[str]] = {}
    removed_nodes: set[str] = set()
    added_nodes: set[str] = set()

    # Remove deleted-file nodes first.
    for file_diff in file_diffs:
        if file_diff.is_deleted_file:
            old_path = _normalize_rel_path(file_diff.old_path)
            if old_path:
                for node_id in existing_nodes_by_path.get(old_path, set()):
                    if node_id in updated_graph.graph:
                        updated_graph.graph.remove_node(node_id)
                        removed_nodes.add(node_id)

    # Re-extract changed/renamed/current files and update nodes in place when possible.
    for rel_path, abs_path in current_python_files.items():
        old_node_ids = set(existing_nodes_by_path.get(rel_path, set()))
        old_node_ids_by_current_path[rel_path] = old_node_ids

        new_functions = extract_functions(abs_path)
        new_ids = {function.fqn for function in new_functions}

        for stale_node_id in sorted(old_node_ids - new_ids):
            if stale_node_id in updated_graph.graph:
                updated_graph.graph.remove_node(stale_node_id)
                removed_nodes.add(stale_node_id)

        function_by_id = {function.fqn: function for function in new_functions}
        for function_id in sorted(old_node_ids & new_ids):
            _update_function_node(updated_graph, function_by_id[function_id])

        for function_id in sorted(new_ids - old_node_ids):
            updated_graph.add_function(function_by_id[function_id])
            added_nodes.add(function_id)

    removed_edges: set[tuple[str, str]] = set()
    for rel_path, node_ids in old_node_ids_by_current_path.items():
        for node_id in sorted(node_ids):
            if node_id not in updated_graph.graph:
                continue
            for _, target in list(updated_graph.graph.out_edges(node_id)):
                removed_edges.add((node_id, target))
            updated_graph.graph.remove_edges_from(
                list(updated_graph.graph.out_edges(node_id))
            )

    # Build up-to-date function registry from the updated graph and re-add outgoing edges.
    all_functions = _function_nodes_from_graph(updated_graph)
    added_edges: set[tuple[str, str]] = set()

    for rel_path, abs_path in current_python_files.items():
        import_map = build_import_map(abs_path)
        for edge in extract_call_edges(
            abs_path,
            all_functions=all_functions,
            import_map=import_map,
        ):
            if (
                edge.caller_fqn not in updated_graph.graph
                or edge.callee_fqn not in updated_graph.graph
            ):
                continue
            if updated_graph.graph.has_edge(edge.caller_fqn, edge.callee_fqn):
                updated_graph.graph.edges[(edge.caller_fqn, edge.callee_fqn)].update(
                    {
                        "call_site_line": edge.call_site_line,
                        "is_resolved": edge.is_resolved,
                        "resolution_method": edge.resolution_method,
                        "raw_callee": edge.raw_callee,
                    }
                )
            else:
                updated_graph.add_call(edge)
                added_edges.add((edge.caller_fqn, edge.callee_fqn))

    final_node_ids = set(updated_graph.graph.nodes())
    unchanged_nodes = len(final_node_ids - added_nodes)
    update_time_ms = (perf_counter() - started_at) * 1000.0

    return updated_graph, GraphDelta(
        added_nodes=sorted(added_nodes),
        removed_nodes=sorted(removed_nodes),
        added_edges=sorted(added_edges),
        removed_edges=sorted(removed_edges),
        unchanged_nodes=unchanged_nodes,
        update_time_ms=update_time_ms,
    )


def _normalize_file_diffs(
    diff_hunks: DiffParseResult | Sequence[FileDiff],
) -> list[FileDiff]:
    if isinstance(diff_hunks, DiffParseResult):
        return list(diff_hunks.files)
    return list(diff_hunks)


def _resolve_snapshot_root(snapshot: RepoSnapshot | str | Path) -> Path:
    if isinstance(snapshot, RepoSnapshot):
        return snapshot.local_path.resolve()
    return Path(snapshot).expanduser().resolve()


def _collect_current_python_files(
    file_diffs: Sequence[FileDiff],
    root: Path,
) -> dict[str, Path]:
    current_files: dict[str, Path] = {}
    for file_diff in file_diffs:
        if file_diff.is_deleted_file:
            continue
        rel_path = _normalize_rel_path(
            file_diff.new_path or file_diff.rename_to or file_diff.path
        )
        if not rel_path or not rel_path.endswith(".py"):
            continue
        abs_path = root / rel_path
        if abs_path.exists() and abs_path.is_file():
            current_files[rel_path] = abs_path
    return current_files


def _group_nodes_by_rel_path(call_graph: CallGraph, root: Path) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for node_id, data in call_graph.graph.nodes(data=True):
        rel_path = _normalize_rel_path(str(data.get("file_path", "")), root=root)
        if not rel_path:
            continue
        grouped.setdefault(rel_path, set()).add(str(node_id))
    return grouped


def _normalize_rel_path(value: str | None, *, root: Path | None = None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    path = Path(raw)
    if root is not None and path.is_absolute():
        try:
            raw = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            raw = path.resolve().as_posix()
    normalized = raw.replace("\\", "/").strip()
    return normalized or None


def _update_function_node(call_graph: CallGraph, function: FunctionNode) -> None:
    if function.fqn not in call_graph.graph:
        return
    call_graph.graph.nodes[function.fqn].update(
        {
            "fqn": function.fqn,
            "file_path": str(function.file_path),
            "start_line": function.start_line,
            "end_line": function.end_line,
            "source_code": function.source_code,
            "params": list(function.params),
            "is_method": function.is_method,
            "class_name": function.class_name,
            "is_nested": function.is_nested,
            "is_lambda": function.is_lambda,
        }
    )


def _function_nodes_from_graph(call_graph: CallGraph) -> list[FunctionNode]:
    functions: list[FunctionNode] = []
    for node_id, data in call_graph.graph.nodes(data=True):
        functions.append(
            FunctionNode(
                fqn=str(node_id),
                file_path=Path(str(data.get("file_path", ""))).resolve(),
                start_line=int(data.get("start_line", 1)),
                end_line=int(data.get("end_line", 1)),
                source_code=str(data.get("source_code", "")),
                params=list(data.get("params", [])),
                is_method=bool(data.get("is_method", False)),
                class_name=str(data["class_name"]) if data.get("class_name") else None,
                is_nested=bool(data.get("is_nested", False)),
                is_lambda=bool(data.get("is_lambda", False)),
            )
        )
    return functions


__all__ = ["GraphDelta", "incremental_update"]
