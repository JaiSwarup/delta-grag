"""LLM-free evaluation system adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Iterable

import networkx as nx

from src.ast_extractor import FunctionNode
from src.baselines.semantic_rag import build_semantic_index, semantic_retrieve
from src.eval.ground_truth import StructuralGroundTruth, fqn_for_nodes
from src.graph.impact_subgraph import extract_impact_subgraph
from src.ingestion.diff_parser import DiffParseResult
from src.linearization.bfs_linearizer import linearize_subgraph
from src.token_budget import estimate_token_count


@dataclass(frozen=True)
class EvalSystemConfig:
    k_up: int = 2
    k_down: int = 3
    max_nodes: int = 180
    max_edges: int = 320
    max_per_anchor: int = 60
    max_chars: int = 12000
    semantic_top_k: int = 50


@dataclass(frozen=True)
class EvalSystemOutput:
    system: str
    retrieved_fqns: tuple[str, ...] = field(default_factory=tuple)
    context_tokens: int = 0
    detected_cross_file_fqns: tuple[str, ...] = field(default_factory=tuple)
    node_count: int = 0
    edge_count: int = 0
    anchor_count: int = 0
    runtime_ms: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)


def run_system(
    system: str,
    *,
    graph: nx.DiGraph,
    parsed_diff: DiffParseResult,
    diff_text: str,
    repo_root: Path,
    anchor_node_ids: Iterable[str],
    ground_truth: StructuralGroundTruth,
    config: EvalSystemConfig,
) -> EvalSystemOutput:
    if system == "dgrag":
        return _run_dgrag(
            graph=graph,
            diff_text=diff_text,
            repo_root=repo_root,
            anchor_node_ids=anchor_node_ids,
            ground_truth=ground_truth,
            config=config,
        )
    if system == "diff_only":
        return _run_diff_only(diff_text=diff_text, ground_truth=ground_truth)
    if system == "file_context":
        return _run_file_context(
            graph=graph,
            repo_root=repo_root,
            ground_truth=ground_truth,
        )
    if system == "semantic_rag":
        return _run_semantic_rag(
            graph=graph,
            diff_text=diff_text,
            ground_truth=ground_truth,
            top_k=config.semantic_top_k,
        )
    raise ValueError(f"Unknown evaluation system: {system}")


def _run_dgrag(
    *,
    graph: nx.DiGraph,
    diff_text: str,
    repo_root: Path,
    anchor_node_ids: Iterable[str],
    ground_truth: StructuralGroundTruth,
    config: EvalSystemConfig,
) -> EvalSystemOutput:
    start = perf_counter()
    impact_graph, node_order = extract_impact_subgraph(
        graph,
        anchors=anchor_node_ids,
        k_up=config.k_up,
        k_down=config.k_down,
        max_nodes=config.max_nodes,
        max_edges=config.max_edges,
        max_per_anchor=config.max_per_anchor,
    )
    context = linearize_subgraph(
        impact_graph,
        pr_diff=diff_text,
        anchors=anchor_node_ids,
        max_chars=config.max_chars,
        include_code=True,
        include_diff_section=False,
        repo_root=str(repo_root),
        file_attr="file_path",
        qualified_name_attr="fqn",
        code_attr_candidates=("source_code", "code", "source", "snippet", "text"),
    )
    retrieved = fqn_for_nodes(graph, node_order)
    cross_file = tuple(fqn for fqn in retrieved if fqn in set(ground_truth.cross_file_fqns))
    return EvalSystemOutput(
        system="dgrag",
        retrieved_fqns=retrieved,
        context_tokens=estimate_token_count(context),
        detected_cross_file_fqns=cross_file,
        node_count=impact_graph.number_of_nodes(),
        edge_count=impact_graph.number_of_edges(),
        anchor_count=len(tuple(anchor_node_ids)),
        runtime_ms=(perf_counter() - start) * 1000.0,
        warnings=tuple(str(item) for item in impact_graph.graph.get("cutoff_reasons", ())),
    )


def _run_diff_only(
    *,
    diff_text: str,
    ground_truth: StructuralGroundTruth,
) -> EvalSystemOutput:
    start = perf_counter()
    return EvalSystemOutput(
        system="diff_only",
        retrieved_fqns=ground_truth.anchor_fqns,
        context_tokens=estimate_token_count(diff_text),
        detected_cross_file_fqns=(),
        node_count=len(ground_truth.anchor_fqns),
        anchor_count=len(ground_truth.anchor_fqns),
        runtime_ms=(perf_counter() - start) * 1000.0,
    )


def _run_file_context(
    *,
    graph: nx.DiGraph,
    repo_root: Path,
    ground_truth: StructuralGroundTruth,
) -> EvalSystemOutput:
    start = perf_counter()
    changed = {path.replace("\\", "/") for path in ground_truth.changed_files}
    nodes = [
        node_id
        for node_id, data in graph.nodes(data=True)
        if _node_file(data).endswith(tuple(changed))
    ]
    context = "\n\n".join(_read_changed_file(repo_root, path) for path in sorted(changed))
    retrieved = fqn_for_nodes(graph, nodes)
    return EvalSystemOutput(
        system="file_context",
        retrieved_fqns=retrieved,
        context_tokens=estimate_token_count(context),
        detected_cross_file_fqns=(),
        node_count=len(nodes),
        anchor_count=len(ground_truth.anchor_fqns),
        runtime_ms=(perf_counter() - start) * 1000.0,
    )


def _run_semantic_rag(
    *,
    graph: nx.DiGraph,
    diff_text: str,
    ground_truth: StructuralGroundTruth,
    top_k: int,
) -> EvalSystemOutput:
    start = perf_counter()
    functions = [
        FunctionNode(
            fqn=str(data.get("fqn") or data.get("qualified_name") or node_id),
            file_path=Path(str(data.get("file_path") or data.get("file") or "")),
            start_line=int(data.get("start_line") or 1),
            end_line=int(data.get("end_line") or data.get("start_line") or 1),
            source_code=str(data.get("source_code") or data.get("source") or ""),
            params=list(data.get("params") or ()),
            is_method=bool(data.get("is_method", False)),
            class_name=data.get("class_name"),
            is_nested=bool(data.get("is_nested", False)),
            is_lambda=bool(data.get("is_lambda", False)),
        )
        for node_id, data in graph.nodes(data=True)
    ]
    index = build_semantic_index(functions)
    result = semantic_retrieve(diff_text, index, top_k=top_k)
    retrieved = tuple(fqn for fqn, _score in result.retrieved)
    cross_file = tuple(fqn for fqn in retrieved if fqn in set(ground_truth.cross_file_fqns))
    return EvalSystemOutput(
        system="semantic_rag",
        retrieved_fqns=retrieved,
        context_tokens=result.query_tokens,
        detected_cross_file_fqns=cross_file,
        node_count=len(retrieved),
        anchor_count=len(ground_truth.anchor_fqns),
        runtime_ms=(perf_counter() - start) * 1000.0,
    )


def _node_file(data: dict) -> str:
    return str(data.get("file_path") or data.get("file") or "").replace("\\", "/")


def _read_changed_file(repo_root: Path, rel_path: str) -> str:
    path = repo_root / rel_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


__all__ = [
    "EvalSystemConfig",
    "EvalSystemOutput",
    "run_system",
]
