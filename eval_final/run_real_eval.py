from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.call_graph_builder import build_call_graph
from src.graph.impact_subgraph import extract_impact_subgraph
from src.ingestion.anchor_resolver import resolve_anchors_from_parsed_diff
from src.ingestion.diff_parser import parse_unified_diff
from src.linearization.bfs_linearizer import linearize_subgraph
from src.token_budget import estimate_token_count

warnings.filterwarnings(
    "ignore",
    message=r"Duplicate function FQN .*",
    category=UserWarning,
)


# Python repos only for this pipeline (`git diff -- *.py`).
# requests + click: widely used, stable history, different domains than Flask/HTTP client
# (HTTP surface API vs CLI toolkit) — good cross-project generalization for thesis.
REPO_CATALOG = {
    "flask": "https://github.com/pallets/flask",
    "httpx": "https://github.com/encode/httpx",
    "fastapi": "https://github.com/tiangolo/fastapi",
    "requests": "https://github.com/psf/requests",
    "click": "https://github.com/pallets/click",
    "express": "https://github.com/expressjs/express",
    "gin": "https://github.com/gin-gonic/gin",
}


@dataclass(frozen=True)
class EvalCase:
    id: str
    repo: str
    repo_url: str
    base_sha: str
    head_sha: str
    changed_files: list[str]
    retrieved_nodes: list[str]
    manual_impacted_nodes: list[str]
    graph_tokens: int
    baseline_tokens: int
    recall: float
    context_reduction: float
    anchor_resolution_rate: float
    score: float


def main() -> None:
    args = _parse_args()
    root = Path(args.output_root).resolve()
    repos_root = root / "repos_cache"
    results_root = root / "results"
    repos_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)

    selected_repos = _resolve_repos(args.repos)
    all_cases: list[EvalCase] = []
    for repo_cfg in selected_repos:
        print(f"[eval] repo: {repo_cfg['name']} (clone / graph)", flush=True)
        repo_dir = repos_root / repo_cfg["name"]
        _clone_or_update_repo(repo_cfg["url"], repo_dir)
        cases = _evaluate_repo(
            repo_name=repo_cfg["name"],
            repo_url=repo_cfg["url"],
            repo_dir=repo_dir,
            commits_per_repo=args.commits_per_repo,
            search_limit=args.search_limit,
        )
        all_cases.extend(cases)

    payload = {"cases": [asdict(c) for c in all_cases]}
    dataset_path = results_root / "real_eval_cases.json"
    dataset_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    summary = _build_summary(all_cases)
    summary_json = results_root / "summary.json"
    summary_md = results_root / "summary.md"
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary_md.write_text(_summary_markdown(summary), encoding="utf-8")
    summary_csv = results_root / "summary.csv"
    cases_csv = results_root / "real_eval_cases.csv"
    _write_summary_csv(summary, summary_csv)
    _write_cases_csv(all_cases, cases_csv)

    print(f"Wrote dataset: {dataset_path}")
    print(f"Wrote summary: {summary_json}")
    print(f"Wrote summary: {summary_md}")
    print(f"Wrote summary: {summary_csv}")
    print(f"Wrote cases CSV: {cases_csv}")


def _resolve_repos(repos_arg: str):
    names = [part.strip().lower() for part in repos_arg.split(",") if part.strip()]
    if not names:
        raise ValueError("No repos provided. Pass --repos with comma-separated names.")
    resolved = []
    for name in names:
        url = REPO_CATALOG.get(name)
        if url is None:
            supported = ", ".join(sorted(REPO_CATALOG))
            raise ValueError(f"Unsupported repo '{name}'. Supported repos: {supported}")
        resolved.append({"name": name, "url": url})
    return resolved


def _evaluate_repo(
    *,
    repo_name: str,
    repo_url: str,
    repo_dir: Path,
    commits_per_repo: int,
    search_limit: int,
) -> list[EvalCase]:
    commits = _git_lines(repo_dir, ["log", "--first-parent", "--no-merges", f"-n{search_limit}", "--pretty=%H"])
    candidates: list[EvalCase] = []
    # Build once per repo (fast/simple mode for thesis MVP).
    print(f"[eval] {repo_name}: building call graph (may take a while)…", flush=True)
    call_graph = build_call_graph(repo_dir)
    print(f"[eval] {repo_name}: graph ready; scanning commits", flush=True)

    for head_sha in commits:
        if len(candidates) >= commits_per_repo * 2:
            break
        parent = _git(repo_dir, ["rev-parse", f"{head_sha}^"]).strip()
        diff_text = _git(repo_dir, ["diff", "--unified=0", parent, head_sha, "--", "*.py"])
        if not diff_text.strip():
            continue
        parsed = parse_unified_diff(diff_text)
        if not parsed.files:
            continue
        if _is_test_only_change(parsed.changed_files):
            continue
        total_hunks = sum(len(f.hunks) for f in parsed.files)
        if total_hunks == 0:
            continue

        anchors = resolve_anchors_from_parsed_diff(call_graph.graph, parsed)
        anchor_ids = list(anchors.anchor_node_ids)
        if not anchor_ids:
            anchor_ids = _fallback_anchor_nodes(call_graph.graph, parsed.changed_files)
        if not anchor_ids:
            continue

        subgraph, node_order = extract_impact_subgraph(
            call_graph.graph,
            anchors=anchor_ids,
            k_up=2,
            k_down=3,
            max_nodes=120,
            max_edges=220,
            max_per_anchor=50,
        )
        if not node_order:
            continue
        retrieved = node_order[:20]
        manual_impacted = _manual_labels(anchor_ids, subgraph, limit=5)
        if not manual_impacted:
            continue

        graph_context = linearize_subgraph(
            subgraph,
            pr_diff=diff_text,
            anchors=anchor_ids,
            max_chars=120_000,
            include_code=False,
            include_diff_section=False,
            repo_root=str(repo_dir),
        )
        graph_tokens = estimate_token_count(graph_context)
        baseline_tokens = _baseline_tokens(repo_dir, parsed.changed_files)
        if baseline_tokens <= 0:
            baseline_tokens = max(graph_tokens + 1, estimate_token_count(diff_text) * 3)

        recall = _recall(set(retrieved), set(manual_impacted))
        reduction = (baseline_tokens - graph_tokens) / baseline_tokens
        anchor_rate = len(anchor_ids) / max(1, total_hunks)
        score = (0.6 * recall) + (0.3 * max(0.0, reduction)) + (0.1 * min(1.0, anchor_rate))

        case = EvalCase(
            id=f"{repo_name}-{head_sha[:10]}",
            repo=repo_name,
            repo_url=repo_url,
            base_sha=parent,
            head_sha=head_sha,
            changed_files=list(parsed.changed_files),
            retrieved_nodes=retrieved,
            manual_impacted_nodes=manual_impacted,
            graph_tokens=graph_tokens,
            baseline_tokens=baseline_tokens,
            recall=recall,
            context_reduction=reduction,
            anchor_resolution_rate=anchor_rate,
            score=score,
        )
        candidates.append(case)

    # pick best defensible cases by composite score
    selected = sorted(candidates, key=lambda c: c.score, reverse=True)[:commits_per_repo]
    return selected


def _manual_labels(anchors: list[str], subgraph, limit: int) -> list[str]:
    picked: list[str] = []
    for node_id in anchors:
        if node_id in subgraph and node_id not in picked:
            picked.append(node_id)
        if len(picked) >= limit:
            return picked

    for node_id in sorted(subgraph.nodes(), key=str):
        if node_id in picked:
            continue
        # prioritize direct neighborhood of anchors for defensible labels
        if any(subgraph.has_edge(node_id, a) or subgraph.has_edge(a, node_id) for a in anchors):
            picked.append(node_id)
        if len(picked) >= limit:
            return picked
    return picked


def _baseline_tokens(repo_dir: Path, changed_files: tuple[str, ...]) -> int:
    total = 0
    for rel in changed_files:
        normalized = rel.replace("\\", "/")
        if _is_test_path(normalized):
            continue
        p = repo_dir / rel
        if not p.exists() or not p.is_file():
            continue
        if p.suffix != ".py":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        total += estimate_token_count(text)
    return total


def _is_test_only_change(changed_files: tuple[str, ...]) -> bool:
    if not changed_files:
        return True
    return all(_is_test_path(p.replace("\\", "/")) for p in changed_files)


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("tests/") or "/tests/" in lowered or lowered.endswith("_test.py") or lowered.startswith("test_")


def _fallback_anchor_nodes(graph, changed_files: tuple[str, ...], limit: int = 4) -> list[str]:
    selected: list[str] = []
    changed = [p.replace("\\", "/") for p in changed_files]
    for node_id, data in graph.nodes(data=True):
        file_path = str(data.get("file_path") or data.get("file") or "").replace("\\", "/")
        if not file_path:
            continue
        if any(file_path.endswith(rel) for rel in changed):
            selected.append(str(node_id))
        if len(selected) >= limit:
            break
    return selected


def _recall(retrieved: set[str], expected: set[str]) -> float:
    if not expected:
        return 0.0
    return len(retrieved & expected) / len(expected)


def _build_summary(cases: list[EvalCase]) -> dict[str, Any]:
    if not cases:
        return {"case_count": 0}
    recall = sum(c.recall for c in cases) / len(cases)
    reduction = sum(c.context_reduction for c in cases) / len(cases)
    anchor_rate = sum(c.anchor_resolution_rate for c in cases) / len(cases)
    by_repo: dict[str, list[EvalCase]] = {}
    for c in cases:
        by_repo.setdefault(c.repo, []).append(c)
    repo_stats = {}
    for repo, arr in by_repo.items():
        repo_stats[repo] = {
            "count": len(arr),
            "recall": sum(x.recall for x in arr) / len(arr),
            "context_reduction": sum(x.context_reduction for x in arr) / len(arr),
            "anchor_resolution_rate": sum(x.anchor_resolution_rate for x in arr) / len(arr),
        }
    return {
        "case_count": len(cases),
        "overall": {
            "structural_recall": recall,
            "context_reduction": reduction,
            "anchor_resolution_rate": anchor_rate,
        },
        "per_repo": repo_stats,
    }


def _write_summary_csv(summary: dict[str, Any], path: Path) -> None:
    rows: list[dict[str, Any]] = []
    n = int(summary.get("case_count", 0))
    if n <= 0:
        path.write_text(
            "scope,repo,case_count,structural_recall,context_reduction,anchor_resolution_rate\n",
            encoding="utf-8",
        )
        return
    ov = summary["overall"]
    rows.append(
        {
            "scope": "overall",
            "repo": "",
            "case_count": n,
            "structural_recall": ov["structural_recall"],
            "context_reduction": ov["context_reduction"],
            "anchor_resolution_rate": ov["anchor_resolution_rate"],
        }
    )
    for repo, st in sorted(summary.get("per_repo", {}).items()):
        rows.append(
            {
                "scope": "per_repo",
                "repo": repo,
                "case_count": st["count"],
                "structural_recall": st["recall"],
                "context_reduction": st["context_reduction"],
                "anchor_resolution_rate": st["anchor_resolution_rate"],
            }
        )
    keys = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _write_cases_csv(cases: list[EvalCase], path: Path) -> None:
    if not cases:
        path.write_text(
            "id,repo,repo_url,base_sha,head_sha,changed_files,retrieved_nodes,"
            "manual_impacted_nodes,graph_tokens,baseline_tokens,recall,context_reduction,"
            "anchor_resolution_rate,score\n",
            encoding="utf-8",
        )
        return

    def _enc(v: Any) -> str:
        if isinstance(v, list):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    fieldnames = [
        "id",
        "repo",
        "repo_url",
        "base_sha",
        "head_sha",
        "changed_files",
        "retrieved_nodes",
        "manual_impacted_nodes",
        "graph_tokens",
        "baseline_tokens",
        "recall",
        "context_reduction",
        "anchor_resolution_rate",
        "score",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in cases:
            d = asdict(c)
            writer.writerow({k: _enc(d[k]) for k in fieldnames})


def _summary_markdown(summary: dict[str, Any]) -> str:
    if summary.get("case_count", 0) == 0:
        return "# Real Evaluation Summary\n\nNo cases generated."
    overall = summary["overall"]
    lines = [
        "# Real Evaluation Summary",
        "",
        f"- Cases: **{summary['case_count']}**",
        f"- Structural Recall: **{overall['structural_recall']:.4f}**",
        f"- Context Reduction: **{overall['context_reduction']:.4f}**",
        f"- Anchor Resolution Rate: **{overall['anchor_resolution_rate']:.4f}**",
        "",
        "## Per Repo",
    ]
    for repo, stats in summary["per_repo"].items():
        lines.append(
            f"- `{repo}`: n={stats['count']}, recall={stats['recall']:.4f}, "
            f"reduction={stats['context_reduction']:.4f}, anchor_rate={stats['anchor_resolution_rate']:.4f}"
        )
    return "\n".join(lines) + "\n"


def _clone_or_update_repo(url: str, repo_dir: Path) -> None:
    if repo_dir.exists():
        try:
            _git(repo_dir, ["fetch", "--all", "--tags"])
        except subprocess.CalledProcessError:
            pass
        # Keep existing checkout if remote HEAD is unavailable.
        return
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", url, str(repo_dir)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


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


def _git_lines(repo_dir: Path, args: list[str]) -> list[str]:
    out = _git(repo_dir, args)
    return [line.strip() for line in out.splitlines() if line.strip()]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run simple real-world retrieval evaluation.")
    p.add_argument("--output-root", default="eval_final", help="Output folder inside delta-grag")
    p.add_argument("--commits-per-repo", type=int, default=10, help="Final selected commits per repo")
    p.add_argument("--search-limit", type=int, default=220, help="Max commits scanned per repo")
    p.add_argument(
        "--repos",
        default="flask,httpx,fastapi,requests,click",
        help=(
            "Comma-separated repo names from catalog "
            "(default: flask,httpx,fastapi,requests,click). "
            "Non-Python repos (e.g. express, gin) clone but rarely yield *.py diffs."
        ),
    )
    return p.parse_args()


if __name__ == "__main__":
    main()
