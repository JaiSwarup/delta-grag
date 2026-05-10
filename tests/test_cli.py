from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from src.cli import app
from src.impact_subgraph import SubgraphStats
from src.pipeline.pr_orchestrator import PipelineResult

runner = CliRunner()


class _StubProvider:
    async def get_pr_info(self, pr_url: str):  # pragma: no cover - never reached here
        raise AssertionError("provider should not be called in this test")


def test_cli_help_shows_review_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Delta-GRAG CLI for PR review" in result.stdout
    assert "review" in result.stdout


def test_cli_review_rejects_invalid_pr_url_without_stack_trace() -> None:
    result = runner.invoke(app, ["review", "--pr-url", "not-a-valid-pr-url"])

    assert result.exit_code == 2
    assert "pr_url must be a GitHub PR URL" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_review_writes_json_output_for_valid_pr_url(
    tmp_path: Path, monkeypatch
) -> None:
    output_path = tmp_path / "review.json"

    monkeypatch.setattr(
        "src.cli.default_provider_from_env",
        lambda env_var="GITHUB_TOKEN": _StubProvider(),
    )

    async def _fake_review_pr(*, pr_url, config, cache_dir, provider):
        assert pr_url == "https://github.com/acme/widgets/pull/42"
        assert config.k_up == 1
        assert config.k_down == 2
        assert Path(cache_dir) == tmp_path / "cache"

        return PipelineResult(
            pr_id="42",
            pr_url=pr_url,
            review={
                "findings": [
                    {
                        "severity": "medium",
                        "category": "correctness",
                        "summary": "Potential regression",
                    }
                ],
                "overall_risk": "medium",
                "metadata": {"title": "Example PR"},
            },
            subgraph_stats=SubgraphStats(
                node_count=3,
                edge_count=2,
                anchor_count=1,
                caller_count=1,
                callee_count=1,
                shared_count=0,
                cutoff_reasons=(),
            ),
            timing_breakdown={"fetch_pr_info_ms": 1.5, "build_graph_ms": 3.0},
            context_tokens=123,
            cache_hit=False,
        )

    monkeypatch.setattr("src.cli.review_pr", _fake_review_pr)

    result = runner.invoke(
        app,
        [
            "review",
            "--pr-url",
            "https://github.com/acme/widgets/pull/42",
            "--depth-k",
            "1",
            "--depth-m",
            "2",
            "--model",
            "test-model",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--output-json",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["pr_id"] == "42"
    assert payload["pr_url"] == "https://github.com/acme/widgets/pull/42"
    assert payload["review"]["overall_risk"] == "medium"
    assert payload["review"]["model"] == "test-model"
    assert payload["subgraph_stats"]["anchor_count"] == 1
    assert payload["context_tokens"] == 123
