"""
PR URL orchestrator for end-to-end D-GRAG review preparation.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Protocol

from src.call_graph_builder import build_call_graph
from src.impact_subgraph import SubgraphStats, build_impact_subgraph
from src.ingestion.diff_parser import DiffParseResult, parse_unified_diff
from src.repo_manager import clone_at_sha
from src.token_budget import estimate_token_count


class PipelineError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


@dataclass(frozen=True)
class PRInfo:
    pr_id: str
    repo_url: str
    base_sha: str
    head_sha: str
    diff_text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class PRInfoProvider(Protocol):
    async def get_pr_info(self, pr_url: str) -> PRInfo: ...


@dataclass(frozen=True)
class ReviewConfig:
    k_up: int = 2
    k_down: int = 3
    max_nodes: int = 150
    max_edges: int | None = None
    max_per_anchor: int | None = None


@dataclass(frozen=True)
class PipelineResult:
    pr_id: str
    pr_url: str
    review: Mapping[str, Any]
    subgraph_stats: SubgraphStats
    timing_breakdown: Mapping[str, float]
    context_tokens: int
    cache_hit: bool


async def review_pr(
    *,
    pr_url: str,
    config: ReviewConfig,
    cache_dir: str | Path,
    provider: PRInfoProvider,
) -> PipelineResult:
    parsed_url = parse_github_pr_url(pr_url)
    cache_root = Path(cache_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    result_cache_path = cache_root / "results" / f"{_cache_key(parsed_url)}.json"
    clone_cache_dir = cache_root / "snapshots"

    if result_cache_path.exists():
        return _load_cached_result(result_cache_path, pr_url=pr_url)

    timings: dict[str, float] = {}

    try:
        pr_info = await _timed_async(
            timings,
            "fetch_pr_info_ms",
            lambda: provider.get_pr_info(pr_url),
        )

        base_snapshot = _timed(
            timings,
            "clone_base_ms",
            lambda: clone_at_sha(pr_info.repo_url, pr_info.base_sha, clone_cache_dir),
        )
        head_snapshot = _timed(
            timings,
            "clone_head_ms",
            lambda: clone_at_sha(pr_info.repo_url, pr_info.head_sha, clone_cache_dir),
        )

        parsed_diff = _timed(
            timings,
            "parse_diff_ms",
            lambda: parse_unified_diff(pr_info.diff_text),
        )
        call_graph = _timed(
            timings,
            "build_graph_ms",
            lambda: build_call_graph(head_snapshot),
        )
        anchors = _timed(
            timings,
            "resolve_anchors_ms",
            lambda: _resolve_anchor_fqns(call_graph.graph.nodes(data=True), parsed_diff),
        )
        impact = _timed(
            timings,
            "impact_subgraph_ms",
            lambda: build_impact_subgraph(
                call_graph.graph,
                anchors=anchors,
                k_up=config.k_up,
                k_down=config.k_down,
                max_nodes=config.max_nodes,
                max_edges=config.max_edges,
                max_per_anchor=config.max_per_anchor,
            ),
        )
    except Exception as exc:
        if isinstance(exc, PipelineError):
            raise
        raise PipelineError("review_pr", str(exc)) from exc

    context_text = "\n\n".join(
        str(impact.graph.nodes[node].get("source_code", ""))
        for node in impact.node_order
    )
    result = PipelineResult(
        pr_id=pr_info.pr_id,
        pr_url=pr_url,
        review={
            "findings": [],
            "overall_risk": "low",
            "mode": "orchestrator_retrieval_only",
            "metadata": dict(pr_info.metadata),
            "base_snapshot": str(base_snapshot.local_path),
            "head_snapshot": str(head_snapshot.local_path),
        },
        subgraph_stats=impact.stats,
        timing_breakdown=timings,
        context_tokens=estimate_token_count(context_text),
        cache_hit=False,
    )
    _write_cached_result(result_cache_path, result)
    return result


def parse_github_pr_url(pr_url: str) -> dict[str, str]:
    match = re.match(
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)/?$",
        pr_url.strip(),
    )
    if not match:
        raise ValueError("pr_url must be a GitHub PR URL like https://github.com/owner/repo/pull/123")
    return match.groupdict()


def _resolve_anchor_fqns(nodes, parsed_diff: DiffParseResult) -> list[str]:
    changed_lines = parsed_diff.changed_lines_by_file
    anchors: set[str] = set()
    for node_id, data in nodes:
        file_path = str(data.get("file_path", "")).replace("\\", "/")
        start_line = _as_int(data.get("start_line"))
        end_line = _as_int(data.get("end_line"))
        if start_line is None or end_line is None:
            continue
        for rel_path, lines in changed_lines.items():
            if not file_path.endswith(rel_path):
                continue
            if any(start_line <= line <= end_line for line in lines):
                anchors.add(str(node_id))
    return sorted(anchors)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _cache_key(parsed_url: Mapping[str, str]) -> str:
    return f"{parsed_url['owner']}__{parsed_url['repo']}__pr{parsed_url['number']}"


async def _timed_async(timings: dict[str, float], key: str, fn):
    start = perf_counter()
    out = await fn()
    timings[key] = (perf_counter() - start) * 1000.0
    return out


def _timed(timings: dict[str, float], key: str, fn):
    start = perf_counter()
    out = fn()
    timings[key] = (perf_counter() - start) * 1000.0
    return out


def _write_cached_result(path: Path, result: PipelineResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pr_id": result.pr_id,
        "pr_url": result.pr_url,
        "review": dict(result.review),
        "subgraph_stats": asdict(result.subgraph_stats),
        "timing_breakdown": dict(result.timing_breakdown),
        "context_tokens": result.context_tokens,
        "cache_hit": False,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_cached_result(path: Path, *, pr_url: str) -> PipelineResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    stats = payload.get("subgraph_stats", {})
    return PipelineResult(
        pr_id=str(payload["pr_id"]),
        pr_url=pr_url,
        review=dict(payload.get("review", {})),
        subgraph_stats=SubgraphStats(
            node_count=int(stats.get("node_count", 0)),
            edge_count=int(stats.get("edge_count", 0)),
            anchor_count=int(stats.get("anchor_count", 0)),
            caller_count=int(stats.get("caller_count", 0)),
            callee_count=int(stats.get("callee_count", 0)),
            shared_count=int(stats.get("shared_count", 0)),
            cutoff_reasons=tuple(stats.get("cutoff_reasons", ())),
        ),
        timing_breakdown=dict(payload.get("timing_breakdown", {})),
        context_tokens=int(payload.get("context_tokens", 0)),
        cache_hit=True,
    )


__all__ = [
    "PipelineError",
    "PipelineResult",
    "PRInfo",
    "PRInfoProvider",
    "ReviewConfig",
    "parse_github_pr_url",
    "review_pr",
]
