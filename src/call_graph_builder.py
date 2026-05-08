"""
Standalone call graph wrapper, serializer, and builder.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import networkx as nx

from src.ast_extractor import FunctionNode, extract_functions, extract_functions_from_module
from src.call_extractor import CallEdge, build_import_map, build_import_map_from_module, extract_call_edges
from src.graph_identity import build_node_id
from src.repo_manager import RepoSnapshot

log = logging.getLogger(__name__)

# Bump this string whenever the graph schema or extraction logic changes
# so that stale cache entries are automatically invalidated.
GRAPH_BUILDER_VERSION = "1"


@dataclass
class _FileExtraction:
    """Holds the per-file parse results extracted in a single AST pass."""

    file_path: Path
    functions: list[FunctionNode]
    import_map: dict[str, str]


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
    def load_json(cls, path: str | Path, *, repo_root: str | Path | None = None) -> "CallGraph":
        """Load a CallGraph from JSON.

        Args:
            path: Path to the JSON file written by ``save_json``.
            repo_root: Optional repo root to set on the loaded graph.
                       This is **required** for anchor resolution after loading
                       from cache because ``save_json`` does not persist ``repo_root``.
        """
        payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
        graph = cls(repo_root=repo_root)
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
    """Build a call graph for *snapshot* using a single AST parse per file.

    Each Python file is read and parsed once; functions, imports, and call
    candidates are all extracted from that single ``ast.parse()`` result.
    A structured ``DEBUG`` log line is emitted with per-phase timing so that
    hot-paths are visible without additional instrumentation.
    """
    root = _resolve_snapshot_root(snapshot)
    t0 = perf_counter()

    python_files = sorted(root.rglob("*.py"))
    discovery_ms = (perf_counter() - t0) * 1000.0

    # --- Single parse per file ---------------------------------------------------
    t1 = perf_counter()
    extractions: list[_FileExtraction] = [_extract_file(fp) for fp in python_files]
    all_functions: list[FunctionNode] = [fn for ex in extractions for fn in ex.functions]
    extraction_ms = (perf_counter() - t1) * 1000.0

    # --- Node insertion ----------------------------------------------------------
    t2 = perf_counter()
    call_graph = CallGraph(repo_root=root)
    for function in all_functions:
        call_graph.add_function(function)
    node_insertion_ms = (perf_counter() - t2) * 1000.0

    # --- Edge insertion (uses already-parsed import_map from extraction) ---------
    t3 = perf_counter()
    for ex in extractions:
        for edge in extract_call_edges(
            ex.file_path,
            all_functions=all_functions,
            import_map=ex.import_map,
        ):
            caller_id = call_graph.node_id_for_edge_endpoint(
                edge.caller_file_path,
                edge.caller_fqn,
            )
            callee_id = _edge_callee_id(call_graph, edge)
            if caller_id in call_graph.graph and callee_id in call_graph.graph:
                call_graph.add_call(edge)
    edge_insertion_ms = (perf_counter() - t3) * 1000.0

    total_ms = (perf_counter() - t0) * 1000.0
    log.debug(
        "build_call_graph: files=%d nodes=%d edges=%d "
        "discovery=%.1fms extraction=%.1fms node_insert=%.1fms edge_insert=%.1fms total=%.1fms",
        len(python_files),
        call_graph.graph.number_of_nodes(),
        call_graph.graph.number_of_edges(),
        discovery_ms,
        extraction_ms,
        node_insertion_ms,
        edge_insertion_ms,
        total_ms,
    )
    return call_graph


def graph_cache_key(repo_root: str | Path, head_sha: str) -> str:
    """Return a stable cache-key string for a (repo, sha) pair.

    The key encodes ``GRAPH_BUILDER_VERSION`` so it automatically changes
    whenever the extraction logic is updated, preventing stale cache reads.
    """
    root = Path(repo_root).expanduser().resolve()
    repo_name = root.name
    raw = f"{repo_name}:{head_sha.strip()}:{GRAPH_BUILDER_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()


def load_graph_from_cache(
    cache_dir: str | Path,
    key: str,
    *,
    repo_root: str | Path | None = None,
) -> tuple[CallGraph, float] | None:
    """Try to load a cached graph.

    Returns ``(graph, load_ms)`` on a hit, or ``None`` on a miss.
    ``repo_root`` is forwarded to :meth:`CallGraph.load_json` so that anchor
    resolution works correctly after loading.
    """
    cache_path = Path(cache_dir) / f"{key}.json"
    if not cache_path.exists():
        return None
    t0 = perf_counter()
    graph = CallGraph.load_json(cache_path, repo_root=repo_root)
    load_ms = (perf_counter() - t0) * 1000.0
    log.debug("graph cache hit: %s (%.1fms)", cache_path, load_ms)
    return graph, load_ms


def save_graph_to_cache(
    cache_dir: str | Path,
    key: str,
    graph: CallGraph,
) -> Path:
    """Persist *graph* to *cache_dir*/<key>.json and return the written path."""
    cache_path = Path(cache_dir) / f"{key}.json"
    graph.save_json(cache_path)
    log.debug("graph cache written: %s", cache_path)
    return cache_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_file(file_path: Path) -> _FileExtraction:
    """Read and parse *file_path* once, returning functions and import map."""
    source_text = file_path.read_text(encoding="utf-8", errors="replace")
    module = ast.parse(source_text, filename=str(file_path))
    return _FileExtraction(
        file_path=file_path,
        functions=extract_functions_from_module(source_text, module, file_path),
        import_map=build_import_map_from_module(module, file_path),
    )


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


__all__ = [
    "GRAPH_BUILDER_VERSION",
    "CallGraph",
    "build_call_graph",
    "graph_cache_key",
    "load_graph_from_cache",
    "save_graph_to_cache",
]
