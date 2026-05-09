from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.call_graph_builder import build_call_graph
from src.ingestion.anchor_resolver import resolve_anchors_from_parsed_diff
from src.ingestion.diff_parser import parse_unified_diff
from src.token_budget import estimate_token_count


TOP_K = 20


@dataclass(frozen=True)
class MethodMetrics:
    method: str
    recall: float
    precision: float
    f1: float
    avg_tokens: float


def main() -> None:
    dataset_path = PROJECT_ROOT / "eval_final" / "results" / "real_eval_cases.json"
    out_dir = PROJECT_ROOT / "eval_final" / "results"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = list(payload.get("cases", []))
    if not cases:
        raise SystemExit("No cases found in real_eval_cases.json")

    repo_graphs = _prepare_repo_graphs(cases)
    per_case_rows: list[dict[str, Any]] = []
    by_method: dict[str, dict[str, float]] = {}
    case_eval_count = 0

    for case in cases:
        repo = str(case["repo"])
        repo_dir = PROJECT_ROOT / "eval_final" / "repos_cache" / repo
        graph = repo_graphs[repo]
        expected = set(case.get("manual_impacted_nodes", []))
        if not expected:
            continue
        case_eval_count += 1

        base_sha = str(case["base_sha"])
        head_sha = str(case["head_sha"])
        diff_text = _git(repo_dir, ["diff", "--unified=0", base_sha, head_sha, "--", "*.py"])
        parsed = parse_unified_diff(diff_text)

        methods = {
            "dgrag": list(case.get("retrieved_nodes", []))[:TOP_K],
            "diff_only": _diff_only_nodes(graph.graph, parsed)[:TOP_K],
            "file_context": _file_context_nodes(graph.graph, parsed.changed_files)[:TOP_K],
            "semantic_proxy": _semantic_proxy_nodes(graph.graph, diff_text)[:TOP_K],
        }
        tokens = {
            "dgrag": int(case.get("graph_tokens", 0)),
            "diff_only": estimate_token_count(diff_text),
            "file_context": _file_context_tokens(repo_dir, parsed.changed_files),
            "semantic_proxy": _semantic_proxy_tokens(graph.graph, methods["semantic_proxy"]),
        }

        for method, retrieved in methods.items():
            rset = set(retrieved)
            precision, recall, f1 = _prf(rset, expected)
            row = {
                "case_id": str(case["id"]),
                "repo": repo,
                "method": method,
                "retrieved_count": len(rset),
                "expected_count": len(expected),
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "tokens": tokens[method],
            }
            per_case_rows.append(row)
            stats = by_method.setdefault(
                method,
                {"precision": 0.0, "recall": 0.0, "f1": 0.0, "tokens": 0.0, "count": 0.0},
            )
            stats["precision"] += precision
            stats["recall"] += recall
            stats["f1"] += f1
            stats["tokens"] += float(tokens[method])
            stats["count"] += 1.0

    summary = _summarize(by_method)
    summary_path = out_dir / "baseline_comparison_summary.json"
    md_path = out_dir / "baseline_comparison_summary.md"
    csv_path = out_dir / "baseline_comparison_per_case.csv"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown(summary, case_eval_count), encoding="utf-8")
    summary_csv_path = out_dir / "baseline_comparison_summary.csv"
    _write_baseline_summary_csv(summary, case_eval_count, summary_csv_path)
    _write_csv(csv_path, per_case_rows)

    print(f"Wrote: {summary_path}")
    print(f"Wrote: {md_path}")
    print(f"Wrote: {summary_csv_path}")
    print(f"Wrote: {csv_path}")


def _prepare_repo_graphs(cases: list[dict[str, Any]]):
    repos = sorted({str(c["repo"]) for c in cases})
    graphs = {}
    for repo in repos:
        repo_dir = PROJECT_ROOT / "eval_final" / "repos_cache" / repo
        graphs[repo] = build_call_graph(repo_dir)
    return graphs


def _diff_only_nodes(graph, parsed) -> list[str]:
    anchors = resolve_anchors_from_parsed_diff(graph, parsed)
    return list(anchors.anchor_node_ids)


def _file_context_nodes(graph, changed_files: tuple[str, ...]) -> list[str]:
    changed = [p.replace("\\", "/") for p in changed_files]
    out: list[str] = []
    for node_id, data in sorted(graph.nodes(data=True), key=lambda x: str(x[0])):
        file_path = str(data.get("file_path") or data.get("file") or "").replace("\\", "/")
        if any(file_path.endswith(rel) for rel in changed):
            out.append(str(node_id))
    return out


def _semantic_proxy_nodes(graph, diff_text: str) -> list[str]:
    q = set(_tokens(diff_text))
    scored: list[tuple[str, int]] = []
    for node_id, data in graph.nodes(data=True):
        text = f"{node_id} {data.get('fqn','')} {data.get('qualified_name','')} {data.get('source_code','')}"
        t = set(_tokens(text))
        score = len(q & t)
        if score > 0:
            scored.append((str(node_id), score))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return [nid for nid, _ in scored]


def _semantic_proxy_tokens(graph, node_ids: list[str]) -> int:
    chunks = []
    for nid in node_ids[:TOP_K]:
        if nid not in graph:
            continue
        d = graph.nodes[nid]
        chunks.append(str(d.get("source_code") or d.get("fqn") or nid))
    return estimate_token_count("\n".join(chunks))


def _file_context_tokens(repo_dir: Path, changed_files: tuple[str, ...]) -> int:
    total = 0
    for rel in changed_files:
        p = repo_dir / rel
        if not p.exists() or not p.is_file():
            continue
        if p.suffix != ".py":
            continue
        total += estimate_token_count(p.read_text(encoding="utf-8", errors="replace"))
    return total


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text.lower())


def _prf(retrieved: set[str], expected: set[str]) -> tuple[float, float, float]:
    tp = len(retrieved & expected)
    precision = tp / len(retrieved) if retrieved else 0.0
    recall = tp / len(expected) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def _summarize(by_method: dict[str, dict[str, float]]) -> dict[str, Any]:
    methods: list[MethodMetrics] = []
    for method, v in sorted(by_method.items()):
        n = max(1.0, v["count"])
        methods.append(
            MethodMetrics(
                method=method,
                precision=v["precision"] / n,
                recall=v["recall"] / n,
                f1=v["f1"] / n,
                avg_tokens=v["tokens"] / n,
            )
        )
    file_ctx = next((m for m in methods if m.method == "file_context"), None)
    out: dict[str, Any] = {"methods": [m.__dict__ for m in methods]}
    if file_ctx is not None and file_ctx.avg_tokens > 0:
        reductions = {}
        for m in methods:
            reductions[m.method] = (file_ctx.avg_tokens - m.avg_tokens) / file_ctx.avg_tokens
        out["context_reduction_vs_file_context"] = reductions
    return out


def _to_markdown(summary: dict[str, Any], case_count: int) -> str:
    lines = [
        "# Baseline Comparison Summary",
        "",
        f"Method-wise macro averages on the same {case_count} real commit cases.",
        "",
        "| Method | Precision | Recall | F1 | Avg Tokens |",
        "|---|---:|---:|---:|---:|",
    ]
    for m in summary.get("methods", []):
        lines.append(
            f"| {m['method']} | {m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} | {m['avg_tokens']:.1f} |"
        )
    lines.append("")
    red = summary.get("context_reduction_vs_file_context", {})
    if red:
        lines.append("## Context Reduction vs File Context")
        for method, value in sorted(red.items()):
            lines.append(f"- `{method}`: {value:.4f}")
    lines.append("")
    return "\n".join(lines)


def _write_baseline_summary_csv(
    summary: dict[str, Any], evaluated_cases: int, path: Path
) -> None:
    methods = list(summary.get("methods", []))
    red = summary.get("context_reduction_vs_file_context", {})
    fieldnames = [
        "evaluated_cases",
        "method",
        "precision",
        "recall",
        "f1",
        "avg_tokens",
        "context_reduction_vs_file_context",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in methods:
            name = str(m["method"])
            writer.writerow(
                {
                    "evaluated_cases": evaluated_cases,
                    "method": name,
                    "precision": m["precision"],
                    "recall": m["recall"],
                    "f1": m["f1"],
                    "avg_tokens": m["avg_tokens"],
                    "context_reduction_vs_file_context": red.get(name, ""),
                }
            )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _git(repo_dir: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout or ""


if __name__ == "__main__":
    main()
