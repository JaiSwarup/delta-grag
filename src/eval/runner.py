"""Real-project, LLM-free evaluation runner for BTP/D-GRAG."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Sequence

import pandas as pd

from src.call_graph_builder import build_call_graph
from src.eval.corpus import EvalCommitCase, iter_cases, load_configs
from src.eval.ground_truth import build_structural_ground_truth
from src.eval.materializer import MaterializedCommit, materialize_commit
from src.eval.metrics import (
    compute_cross_file_detection_rate,
    compute_structural_recall,
    compute_token_reduction,
)
from src.eval.systems import EvalSystemConfig, EvalSystemOutput, run_system
from src.ingestion.diff_parser import parse_unified_diff


DEFAULT_OUTPUT_DIR = Path("evaluate/results")
DEFAULT_REPOS_DIR = Path("evaluate/test_repos")
DEFAULT_SYSTEMS = ("dgrag", "diff_only", "file_context", "semantic_rag")


@dataclass(frozen=True)
class EvalRunConfig:
    configs_dir: str | Path | None = None
    repos: tuple[str, ...] = ()
    systems: tuple[str, ...] = DEFAULT_SYSTEMS
    output_dir: str | Path = DEFAULT_OUTPUT_DIR
    repos_dir: str | Path = DEFAULT_REPOS_DIR
    limit: int | None = None
    refresh: bool = False
    k_up: int = 2
    k_down: int = 3
    max_nodes: int = 180
    max_edges: int = 320
    max_per_anchor: int = 60
    max_chars: int = 12000


def run_eval(config: EvalRunConfig | None = None) -> pd.DataFrame:
    cfg = config or EvalRunConfig()
    output_dir = Path(cfg.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    repo_configs = load_configs(
        configs_dir=cfg.configs_dir,
        repos=cfg.repos or None,
        limit=cfg.limit,
    )
    system_config = EvalSystemConfig(
        k_up=cfg.k_up,
        k_down=cfg.k_down,
        max_nodes=cfg.max_nodes,
        max_edges=cfg.max_edges,
        max_per_anchor=cfg.max_per_anchor,
        max_chars=cfg.max_chars,
    )

    rows: list[dict] = []
    raw_path = output_dir / "raw_runs.jsonl"
    with raw_path.open("w", encoding="utf-8") as raw_file:
        for case in iter_cases(repo_configs):
            case_rows = _run_case(
                case,
                cfg=cfg,
                system_config=system_config,
            )
            for row in case_rows:
                raw_file.write(json.dumps(row, sort_keys=True) + "\n")
            rows.extend(case_rows)

    df = pd.DataFrame(rows)
    metrics_path = output_dir / "metrics_table.csv"
    df.to_csv(metrics_path, index=False)
    _write_summary(df, output_dir / "summary.md")
    _write_daily_csv(rows, output_dir)
    return df


def _run_case(
    case: EvalCommitCase,
    *,
    cfg: EvalRunConfig,
    system_config: EvalSystemConfig,
) -> list[dict]:
    materialized = materialize_commit(
        case,
        repos_dir=cfg.repos_dir,
        refresh=cfg.refresh,
    )
    parsed = parse_unified_diff(materialized.diff_text)

    graph_start = perf_counter()
    call_graph = build_call_graph(materialized.repo_path)
    graph_build_ms = (perf_counter() - graph_start) * 1000.0
    graph = call_graph.graph
    _normalize_graph_file_paths(graph, materialized.repo_path)

    truth, anchors = build_structural_ground_truth(
        graph=graph,
        parsed_diff=parsed,
        k_up=1,
        k_down=1,
        max_nodes=max(system_config.max_nodes, 250),
    )
    baseline_tokens = _changed_file_tokens(materialized)

    rows: list[dict] = []
    for system in cfg.systems:
        output = run_system(
            system,
            graph=graph,
            parsed_diff=parsed,
            diff_text=materialized.diff_text,
            repo_root=materialized.repo_path,
            anchor_node_ids=anchors.anchor_node_ids,
            ground_truth=truth,
            config=system_config,
        )
        rows.append(
            _row_from_output(
                case=case,
                materialized=materialized,
                output=output,
                baseline_context_tokens=baseline_tokens,
                impacted_fqns=truth.impacted_fqns,
                cross_file_fqns=truth.cross_file_fqns,
                graph_build_ms=graph_build_ms,
                graph_node_count=graph.number_of_nodes(),
                graph_edge_count=graph.number_of_edges(),
                unresolved_hunks=truth.unresolved_hunks,
            )
        )
    return rows


def _row_from_output(
    *,
    case: EvalCommitCase,
    materialized: MaterializedCommit,
    output: EvalSystemOutput,
    baseline_context_tokens: int,
    impacted_fqns: Sequence[str],
    cross_file_fqns: Sequence[str],
    graph_build_ms: float,
    graph_node_count: int,
    graph_edge_count: int,
    unresolved_hunks: int,
) -> dict:
    precision, recall, f1 = _precision_recall_f1(output.retrieved_fqns, impacted_fqns)
    return {
        "repo": case.repo,
        "language": case.language,
        "pr_id": case.pr_id,
        "commit": materialized.head_sha,
        "description": case.description,
        "system": output.system,
        "changed_files": len(materialized.changed_files),
        "expected_changed_files": case.expected_changed_files,
        "graph_nodes": graph_node_count,
        "graph_edges": graph_edge_count,
        "graph_build_ms": round(graph_build_ms, 3),
        "retrieval_ms": round(output.runtime_ms, 3),
        "anchor_count": output.anchor_count,
        "unresolved_hunks": unresolved_hunks,
        "retrieved_fqns": len(output.retrieved_fqns),
        "ground_truth_fqns": len(impacted_fqns),
        "structural_recall": round(compute_structural_recall(output.retrieved_fqns, impacted_fqns), 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "context_tokens": output.context_tokens,
        "baseline_context_tokens": baseline_context_tokens,
        "token_reduction_pct": round(
            compute_token_reduction(baseline_context_tokens, output.context_tokens),
            4,
        ),
        "cross_file_ground_truth": len(cross_file_fqns),
        "cross_file_detection_rate": compute_cross_file_detection_rate(
            output.detected_cross_file_fqns,
            cross_file_fqns,
        ),
        "output_nodes": output.node_count,
        "output_edges": output.edge_count,
        "warnings": "; ".join(output.warnings),
    }


def _changed_file_tokens(materialized: MaterializedCommit) -> int:
    total = 0
    for rel_path in materialized.changed_files:
        path = materialized.repo_path / rel_path
        if path.is_file():
            from src.token_budget import estimate_token_count

            total += estimate_token_count(path.read_text(encoding="utf-8", errors="replace"))
    return total


def _normalize_graph_file_paths(graph, repo_root: Path) -> None:
    root = repo_root.expanduser().resolve()
    for _node_id, data in graph.nodes(data=True):
        raw = data.get("file_path")
        if not raw:
            continue
        path = Path(str(raw))
        try:
            data["file_path"] = path.resolve().relative_to(root).as_posix()
        except ValueError:
            data["file_path"] = str(raw).replace("\\", "/")


def _precision_recall_f1(predicted: Sequence[str], actual: Sequence[str]) -> tuple[float, float, float]:
    predicted_set = {item for item in predicted if item}
    actual_set = {item for item in actual if item}
    if not predicted_set and not actual_set:
        return 1.0, 1.0, 1.0
    tp = len(predicted_set & actual_set)
    precision = tp / len(predicted_set) if predicted_set else 0.0
    recall = tp / len(actual_set) if actual_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _write_daily_csv(rows: list[dict], output_dir: Path) -> None:
    if not rows:
        return
    path = output_dir / f"btp_real_project_eval_{date.today().isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary(df: pd.DataFrame, path: Path) -> None:
    if df.empty:
        path.write_text("# BTP Real-Project Evaluation\n\nNo results.\n", encoding="utf-8")
        return
    grouped = (
        df.groupby("system", dropna=False)
        .agg(
            cases=("pr_id", "count"),
            structural_recall=("structural_recall", "mean"),
            precision=("precision", "mean"),
            f1=("f1", "mean"),
            token_reduction_pct=("token_reduction_pct", "mean"),
            graph_build_ms=("graph_build_ms", "mean"),
            retrieval_ms=("retrieval_ms", "mean"),
        )
        .reset_index()
    )
    lines = [
        "# BTP Real-Project Evaluation",
        "",
        "Corpus: sibling code-review-graph eval configs.",
        "",
        grouped.to_markdown(index=False),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


__all__ = ["DEFAULT_OUTPUT_DIR", "DEFAULT_REPOS_DIR", "DEFAULT_SYSTEMS", "EvalRunConfig", "run_eval"]
