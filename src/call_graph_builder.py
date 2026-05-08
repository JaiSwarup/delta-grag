"""
Standalone call graph wrapper and serializer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from src.ast_extractor import FunctionNode, extract_functions
from src.call_extractor import CallEdge, build_import_map, extract_call_edges
from src.graph_identity import build_node_id
from src.repo_manager import RepoSnapshot


@dataclass
class CallGraph:
    graph: nx.DiGraph
    repo_root: Path | None

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.graph = nx.DiGraph()
        self.repo_root = Path(repo_root).expanduser().resolve() if repo_root else None

    def add_function(self, function: FunctionNode) -> None:
        node_id = self.node_id_for_function(function)
        self.graph.add_node(
            node_id,
            id=node_id,
            fqn=function.fqn,
            file_path=str(function.file_path),
            start_line=function.start_line,
            end_line=function.end_line,
            source_code=function.source_code,
            params=list(function.params),
            is_method=function.is_method,
            class_name=function.class_name,
            is_nested=function.is_nested,
            is_lambda=function.is_lambda,
        )

    def add_call(self, edge: CallEdge) -> None:
        caller_id = self.node_id_for_edge_endpoint(
            edge.caller_file_path,
            edge.caller_fqn,
        )
        callee_id = _edge_callee_id(self, edge)
        self.graph.add_edge(
            caller_id,
            callee_id,
            call_site_line=edge.call_site_line,
            is_resolved=edge.is_resolved,
            resolution_method=edge.resolution_method,
            raw_callee=edge.raw_callee,
        )

    def node_id_for_function(self, function: FunctionNode) -> str:
        return self.node_id_for_edge_endpoint(function.file_path, function.fqn)

    def node_id_for_edge_endpoint(self, file_path: str | Path, fqn: str) -> str:
        if self.repo_root is None:
            return fqn
        return build_node_id(file_path, fqn, self.repo_root)

    def get_callers(self, fqn: str, depth: int = 1) -> set[str]:
        return _bounded_bfs(self.graph.reverse(copy=False), fqn, depth)

    def get_callees(self, fqn: str, depth: int = 1) -> set[str]:
        return _bounded_bfs(self.graph, fqn, depth)

    def save_graphml(self, path: str | Path) -> None:
        output_path = Path(path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        serializable = nx.DiGraph()
        for node_id, data in self.graph.nodes(data=True):
            serializable.add_node(node_id, **_graphml_safe_mapping(data))
        for source, target, data in self.graph.edges(data=True):
            serializable.add_edge(source, target, **_graphml_safe_mapping(data))
        nx.write_graphml(serializable, output_path)

    def save_json(self, path: str | Path) -> None:
        output_path = Path(path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": [
                {"id": node_id, **dict(data)}
                for node_id, data in sorted(self.graph.nodes(data=True))
            ],
            "edges": [
                {"source": source, "target": target, **dict(data)}
                for source, target, data in sorted(self.graph.edges(data=True))
            ],
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load_json(cls, path: str | Path) -> "CallGraph":
        payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
        graph = cls()
        for node in payload.get("nodes", []):
            node_id = node["id"]
            attrs = {key: value for key, value in node.items() if key != "id"}
            graph.graph.add_node(node_id, **attrs)
        for edge in payload.get("edges", []):
            source = edge["source"]
            target = edge["target"]
            attrs = {
                key: value
                for key, value in edge.items()
                if key not in {"source", "target"}
            }
            graph.graph.add_edge(source, target, **attrs)
        return graph


def build_call_graph(snapshot: RepoSnapshot | str | Path) -> CallGraph:
    root = _resolve_snapshot_root(snapshot)
    python_files = sorted(root.rglob("*.py"))

    all_functions: list[FunctionNode] = []
    for file_path in python_files:
        all_functions.extend(extract_functions(file_path))

    call_graph = CallGraph(repo_root=root)
    for function in all_functions:
        call_graph.add_function(function)

    for file_path in python_files:
        import_map = build_import_map(file_path)
        for edge in extract_call_edges(
            file_path,
            all_functions=all_functions,
            import_map=import_map,
        ):
            caller_id = call_graph.node_id_for_edge_endpoint(
                edge.caller_file_path,
                edge.caller_fqn,
            )
            callee_id = _edge_callee_id(call_graph, edge)
            if caller_id in call_graph.graph and callee_id in call_graph.graph:
                call_graph.add_call(edge)

    return call_graph


def _resolve_snapshot_root(snapshot: RepoSnapshot | str | Path) -> Path:
    if isinstance(snapshot, RepoSnapshot):
        return snapshot.local_path.resolve()
    return Path(snapshot).expanduser().resolve()


def _bounded_bfs(graph: nx.DiGraph, start: str, depth: int) -> set[str]:
    if depth < 0:
        raise ValueError("depth must be >= 0")
    if start not in graph:
        return set()

    seen = {start}
    frontier = {start}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for node in frontier:
            next_frontier.update(graph.neighbors(node))
        next_frontier -= seen
        if not next_frontier:
            break
        seen.update(next_frontier)
        frontier = next_frontier
    return seen


def _edge_callee_id(call_graph: CallGraph, edge: CallEdge) -> str:
    if edge.callee_file_path is None:
        return edge.callee_fqn
    return call_graph.node_id_for_edge_endpoint(edge.callee_file_path, edge.callee_fqn)


def _graphml_safe_mapping(data: dict) -> dict[str, str | int | float | bool]:
    safe: dict[str, str | int | float | bool] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = "" if value is None else value
        else:
            safe[key] = json.dumps(value, ensure_ascii=False)
    return safe


__all__ = ["CallGraph", "build_call_graph"]
