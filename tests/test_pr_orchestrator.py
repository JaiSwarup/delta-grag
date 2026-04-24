from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from src.pipeline.pr_orchestrator import PRInfo, ReviewConfig, parse_github_pr_url, review_pr


class _StubPRInfoProvider:
    def __init__(self, info: PRInfo) -> None:
        self.info = info
        self.calls = 0

    async def get_pr_info(self, pr_url: str) -> PRInfo:
        self.calls += 1
        return self.info


def _git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_repo_with_pr_like_history(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.name", "Test User"], repo)
    _git(["config", "user.email", "test@example.com"], repo)

    _write(
        repo / "app.py",
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    return helper()\n",
    )
    _git(["add", "."], repo)
    _git(["commit", "-m", "base"], repo)
    base_sha = _git(["rev-parse", "HEAD"], repo)

    _write(
        repo / "app.py",
        "def helper():\n"
        "    return 2\n"
        "\n"
        "def run():\n"
        "    return helper()\n",
    )
    _git(["add", "."], repo)
    _git(["commit", "-m", "head"], repo)
    head_sha = _git(["rev-parse", "HEAD"], repo)
    diff_text = _git(["diff", base_sha, head_sha], repo)
    return repo, base_sha, head_sha, diff_text


def test_parse_github_pr_url_validates_shape() -> None:
    parsed = parse_github_pr_url("https://github.com/acme/widgets/pull/42")
    assert parsed == {"owner": "acme", "repo": "widgets", "number": "42"}


def test_review_pr_orchestrates_local_repo_and_caches_result(tmp_path: Path) -> None:
    repo, base_sha, head_sha, diff_text = _build_repo_with_pr_like_history(tmp_path)
    provider = _StubPRInfoProvider(
        PRInfo(
            pr_id="42",
            repo_url=str(repo),
            base_sha=base_sha,
            head_sha=head_sha,
            diff_text=diff_text,
            metadata={"title": "Update helper"},
        )
    )
    pr_url = "https://github.com/acme/widgets/pull/42"
    cache_dir = tmp_path / "cache"

    result = asyncio.run(
        review_pr(
            pr_url=pr_url,
            config=ReviewConfig(k_up=1, k_down=1, max_nodes=20),
            cache_dir=cache_dir,
            provider=provider,
        )
    )

    assert result.cache_hit is False
    assert result.pr_id == "42"
    assert result.subgraph_stats.anchor_count >= 1
    assert result.subgraph_stats.node_count >= 1
    assert result.context_tokens > 0
    assert "build_graph_ms" in result.timing_breakdown

    cached = asyncio.run(
        review_pr(
            pr_url=pr_url,
            config=ReviewConfig(k_up=1, k_down=1, max_nodes=20),
            cache_dir=cache_dir,
            provider=provider,
        )
    )

    assert cached.cache_hit is True
    assert provider.calls == 1
    assert cached.subgraph_stats.anchor_count == result.subgraph_stats.anchor_count
