"""
Typer-based CLI for D-GRAG PR review and benchmark execution.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.github_api import GitHubIntegrationError, default_provider_from_env
from src.pipeline.pr_orchestrator import (
    PipelineError,
    PipelineResult,
    ReviewConfig,
    parse_github_pr_url,
    review_pr,
)

app = typer.Typer(
    name="dgrag",
    help="Delta-GRAG CLI for PR review and benchmarking.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)


def _print_error(message: str) -> None:
    err_console.print(f"[bold red]Error:[/bold red] {message}")


def _result_to_json_payload(result: PipelineResult) -> dict[str, Any]:
    return {
        "pr_id": result.pr_id,
        "pr_url": result.pr_url,
        "review": dict(result.review),
        "subgraph_stats": {
            "node_count": result.subgraph_stats.node_count,
            "edge_count": result.subgraph_stats.edge_count,
            "anchor_count": result.subgraph_stats.anchor_count,
            "caller_count": result.subgraph_stats.caller_count,
            "callee_count": result.subgraph_stats.callee_count,
            "shared_count": result.subgraph_stats.shared_count,
            "cutoff_reasons": list(result.subgraph_stats.cutoff_reasons),
        },
        "timing_breakdown": dict(result.timing_breakdown),
        "context_tokens": result.context_tokens,
        "cache_hit": result.cache_hit,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _render_review_table(result: PipelineResult, model: str) -> None:
    table = Table(title="D-GRAG PR Review Summary")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")

    table.add_row("PR ID", result.pr_id)
    table.add_row("PR URL", result.pr_url)
    table.add_row("Model", model)
    table.add_row("Cache Hit", "yes" if result.cache_hit else "no")
    table.add_row("Context Tokens", str(result.context_tokens))
    table.add_row("Overall Risk", str(result.review.get("overall_risk", "unknown")))
    table.add_row("Anchors", str(result.subgraph_stats.anchor_count))
    table.add_row("Nodes", str(result.subgraph_stats.node_count))
    table.add_row("Edges", str(result.subgraph_stats.edge_count))

    timings = ", ".join(
        f"{key}={value:.1f}ms" for key, value in sorted(result.timing_breakdown.items())
    )
    table.add_row("Timings", timings or "n/a")

    console.print(table)

    findings = result.review.get("findings", [])
    if isinstance(findings, list) and findings:
        findings_table = Table(title="Issues")
        findings_table.add_column("#", style="cyan", no_wrap=True)
        findings_table.add_column("Severity", style="magenta", no_wrap=True)
        findings_table.add_column("Category", style="green", no_wrap=True)
        findings_table.add_column("Summary", style="white")

        for idx, finding in enumerate(findings, start=1):
            if not isinstance(finding, dict):
                continue
            findings_table.add_row(
                str(idx),
                str(finding.get("severity", "unknown")),
                str(finding.get("category", "unknown")),
                str(finding.get("summary", "")),
            )
        console.print(findings_table)
    else:
        console.print("[yellow]No findings were produced by this review run.[/yellow]")


async def _run_review_command(
    *,
    pr_url: str,
    depth_k: int,
    depth_m: int,
    model: str,
    output_json: Optional[Path],
    cache_dir: Path,
    token_env_var: str,
) -> int:
    try:
        parse_github_pr_url(pr_url)
    except ValueError as exc:
        _print_error(str(exc))
        return 2

    try:
        provider = default_provider_from_env(env_var=token_env_var)
    except GitHubIntegrationError as exc:
        _print_error(str(exc))
        return 2

    config = ReviewConfig(
        k_up=depth_k,
        k_down=depth_m,
    )

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task("Running PR review...", total=None)
            result = await review_pr(
                pr_url=pr_url,
                config=config,
                cache_dir=cache_dir,
                provider=provider,
            )
            progress.update(task_id, description="Review complete")

        _render_review_table(result, model=model)

        payload = _result_to_json_payload(result)
        payload["review"]["model"] = model

        if output_json is not None:
            _write_json(output_json, payload)
            console.print(f"[green]Wrote JSON output to[/green] {output_json}")

        return 0
    except PipelineError as exc:
        _print_error(str(exc))
        return 1
    except GitHubIntegrationError as exc:
        _print_error(str(exc))
        return 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        _print_error(f"Unexpected review failure: {exc}")
        return 1


@app.command("review")
def review_command(
    pr_url: str = typer.Option(
        ...,
        "--pr-url",
        help="GitHub pull request URL, e.g. https://github.com/owner/repo/pull/123",
    ),
    depth_k: int = typer.Option(
        2,
        "--depth-k",
        min=0,
        help="Maximum upstream caller traversal depth.",
    ),
    depth_m: int = typer.Option(
        3,
        "--depth-m",
        min=0,
        help="Maximum downstream callee traversal depth.",
    ),
    model: str = typer.Option(
        "retrieval-only",
        "--model",
        help="Display label for the review model or mode.",
    ),
    output_json: Optional[Path] = typer.Option(
        None,
        "--output-json",
        help="Optional path to write the review result as JSON.",
    ),
    cache_dir: Path = typer.Option(
        Path(".cache/dgrag"),
        "--cache-dir",
        help="Cache directory for cloned snapshots and orchestrator results.",
    ),
    token_env_var: str = typer.Option(
        "GITHUB_TOKEN",
        "--token-env-var",
        help="Environment variable name containing the GitHub token.",
    ),
) -> None:
    """
    Run end-to-end PR review preparation for a GitHub pull request URL.
    """
    code = asyncio.run(
        _run_review_command(
            pr_url=pr_url,
            depth_k=depth_k,
            depth_m=depth_m,
            model=model,
            output_json=output_json,
            cache_dir=cache_dir,
            token_env_var=token_env_var,
        )
    )
    raise typer.Exit(code=code)


@app.command("benchmark")
def benchmark_command(
    pytest_path: str = typer.Option(
        "pytest",
        "--pytest-path",
        help="Pytest executable to invoke for benchmark runs.",
    ),
    test_target: str = typer.Option(
        "tests",
        "--test-target",
        help="Pytest target path or node id for the benchmark suite.",
    ),
    extra_arg: list[str] = typer.Option(
        [],
        "--extra-arg",
        help="Additional argument to pass to pytest. Repeat for multiple values.",
    ),
) -> None:
    """
    Run the project's benchmark or evaluation suite through pytest.
    """
    command = [pytest_path, test_target, *extra_arg]
    console.print(f"[cyan]Running benchmark command:[/cyan] {' '.join(command)}")

    completed = subprocess.run(command, check=False)
    raise typer.Exit(code=completed.returncode)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
