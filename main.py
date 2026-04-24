from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from src.graph.graph_builder import build_call_graph, save_graph
from src.pipeline.review_pipeline import (
    PipelineConfig,
    run_review_pipeline,
    summarize_pipeline_result,
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_graph(path: Path):
    with path.open("rb") as f:
        return pickle.load(f)


def _load_json_if_present(path: Optional[Path]) -> Optional[Mapping[str, Any]]:
    if path is None:
        return None
    text = _read_text(path).strip()
    if not text:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"Metadata file must contain a JSON object: {path}")
    return payload


def cmd_build_graph(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    out = Path(args.output).resolve()

    if not repo.exists() or not repo.is_dir():
        print(f"[error] Invalid repo path: {repo}", file=sys.stderr)
        return 2

    graph = build_call_graph(repo)
    save_graph(graph, out)

    print("[ok] Graph built")
    print(f"  repo:   {repo}")
    print(f"  output: {out}")
    print(f"  nodes:  {graph.number_of_nodes()}")
    print(f"  edges:  {graph.number_of_edges()}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    graph_path = Path(args.graph).resolve()
    diff_path = Path(args.diff).resolve()
    output_path = Path(args.output).resolve() if args.output else None
    summary_path = Path(args.summary_output).resolve() if args.summary_output else None
    metadata_path = Path(args.pr_metadata).resolve() if args.pr_metadata else None

    if not graph_path.exists():
        print(f"[error] Graph file not found: {graph_path}", file=sys.stderr)
        return 2
    if not diff_path.exists():
        print(f"[error] Diff file not found: {diff_path}", file=sys.stderr)
        return 2

    graph = _load_graph(graph_path)
    diff_text = _read_text(diff_path)
    pr_meta = _load_json_if_present(metadata_path)

    if args.full_review:
        if not args.llm_backend or not str(args.llm_backend).strip():
            raise ValueError(
                "--full-review requires --llm-backend with a real backend, e.g. hf_pipeline"
            )
        if args.llm_backend.strip().lower() == "mock":
            raise ValueError(
                "--full-review does not allow the mock backend on the production CLI path"
            )
        if not args.llm_model or not str(args.llm_model).strip():
            raise ValueError("--full-review requires --llm-model")

    cfg = PipelineConfig(
        k_up=args.k_up,
        k_down=args.k_down,
        max_nodes=args.max_nodes,
        max_edges=args.max_edges,
        max_per_anchor=args.max_per_anchor,
        traversal_time_ms=args.traversal_time_ms,
        max_chars=args.max_chars,
        include_code=not args.no_code,
        include_diff_in_context=args.include_diff_in_context,
        repo_root=str(Path(args.repo_root).resolve()) if args.repo_root else None,
        run_full_review=args.full_review,
        strict_json_output=not args.non_strict_json,
        llm_backend=args.llm_backend,
        llm_model_name=args.llm_model,
        llm_temperature=args.llm_temperature,
        llm_max_new_tokens=args.llm_max_new_tokens,
        output_format=args.output_format,
        dedupe_findings_enabled=not args.no_dedupe,
        include_suggested_fix_in_dedupe_key=args.dedupe_include_fix,
    )

    result = run_review_pipeline(
        call_graph=graph,
        pr_diff=diff_text,
        config=cfg,
        pr_metadata=pr_meta,
    )
    summary = summarize_pipeline_result(result)

    # Choose content to write/print
    if cfg.run_full_review:
        content = result.formatted_review or ""
    else:
        # Retrieval-only fallback
        content = result.linearized_context

    if output_path:
        _write_text(output_path, content)
        print(f"[ok] Wrote output to: {output_path}")
    else:
        print(content)

    if summary_path:
        _write_text(summary_path, json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"[ok] Wrote summary to: {summary_path}")

    print("[ok] Pipeline summary:")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btp",
        description="Delta-GRAG CLI: build call graph and run PR impact review pipeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # build-graph
    p_graph = sub.add_parser(
        "build-graph",
        help="Build and save a static call graph from a repository.",
    )
    p_graph.add_argument("--repo", required=True, help="Repository root path.")
    p_graph.add_argument("--output", required=True, help="Output .pkl graph file.")
    p_graph.set_defaults(func=cmd_build_graph)

    # review
    p_review = sub.add_parser(
        "review",
        help="Run end-to-end review pipeline from graph + PR diff.",
    )
    p_review.add_argument("--graph", required=True, help="Path to input graph .pkl.")
    p_review.add_argument("--diff", required=True, help="Path to unified diff file.")
    p_review.add_argument(
        "--pr-metadata",
        help="Optional path to JSON object with PR metadata.",
    )
    p_review.add_argument(
        "--output",
        help="Optional output file path. If omitted, prints to stdout.",
    )
    p_review.add_argument(
        "--summary-output",
        help="Optional output path for JSON summary.",
    )

    # Retrieval controls
    p_review.add_argument("--k-up", type=int, default=2)
    p_review.add_argument("--k-down", type=int, default=3)
    p_review.add_argument("--max-nodes", type=int, default=180)
    p_review.add_argument("--max-edges", type=int, default=320)
    p_review.add_argument("--max-per-anchor", type=int, default=60)
    p_review.add_argument(
        "--traversal-time-ms",
        type=int,
        help="Optional traversal wall-clock budget in milliseconds.",
    )
    p_review.add_argument("--max-chars", type=int, default=12000)
    p_review.add_argument(
        "--repo-root",
        help="Optional repository root used to resolve relative source file paths for code snippets.",
    )
    p_review.add_argument(
        "--no-code",
        action="store_true",
        help="Exclude code blocks from linearized context.",
    )
    p_review.add_argument(
        "--include-diff-in-context",
        action="store_true",
        help="Include full PR diff inside linearized context (disabled by default to avoid prompt duplication).",
    )

    # Full-review / LLM controls
    p_review.add_argument(
        "--full-review",
        action="store_true",
        help="Run prompt + LLM + postprocessing; otherwise retrieval-only mode.",
    )
    p_review.add_argument(
        "--output-format",
        choices=("markdown", "json"),
        default="markdown",
        help="Formatted output for full-review mode.",
    )
    p_review.add_argument(
        "--llm-backend",
        help="LLM backend for --full-review, e.g. hf_pipeline.",
    )
    p_review.add_argument(
        "--llm-model",
        help="Model name for --full-review.",
    )
    p_review.add_argument("--llm-temperature", type=float, default=0.1)
    p_review.add_argument("--llm-max-new-tokens", type=int, default=2048)
    p_review.add_argument(
        "--non-strict-json",
        action="store_true",
        help="Disable strict JSON output requirement in prompt.",
    )

    # Postprocess controls
    p_review.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Disable finding deduplication.",
    )
    p_review.add_argument(
        "--dedupe-include-fix",
        action="store_true",
        help="Include suggested_fix in dedupe key.",
    )

    p_review.set_defaults(func=cmd_review)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n[error] Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
