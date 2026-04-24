from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterator

import networkx as nx
import pytest

from src.call_graph_builder import build_call_graph
from src.ingestion.diff_parser import parse_unified_diff
from src.pipeline.pr_orchestrator import PRInfo
from src.pipeline.review_pipeline import PipelineConfig


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


@pytest.fixture
def tiny_repo_snapshot(tmp_path: Path) -> Path:
    """
    Build a tiny git-backed Python repository suitable for integration-style tests.
    """
    repo = tmp_path / "tiny_repo"
    repo.mkdir()

    _git(["init"], repo)
    _git(["config", "user.name", "Test User"], repo)
    _git(["config", "user.email", "test@example.com"], repo)

    _write(
        repo / "helpers.py",
        "def helper(value):\n    return value + 1\n",
    )
    _write(
        repo / "app.py",
        "from helpers import helper\n\ndef run(value):\n    return helper(value)\n",
    )

    _git(["add", "."], repo)
    _git(["commit", "-m", "initial"], repo)

    return repo


@pytest.fixture
def tiny_repo_with_pr_history(tmp_path: Path) -> dict[str, object]:
    """
    Build a tiny repo with base/head commits plus a unified diff for PR-style tests.
    """
    repo = tmp_path / "repo_with_history"
    repo.mkdir()

    _git(["init"], repo)
    _git(["config", "user.name", "Test User"], repo)
    _git(["config", "user.email", "test@example.com"], repo)

    _write(
        repo / "app.py",
        "def helper():\n    return 1\n\ndef run():\n    return helper()\n",
    )
    _git(["add", "."], repo)
    _git(["commit", "-m", "base"], repo)
    base_sha = _git(["rev-parse", "HEAD"], repo)

    _write(
        repo / "app.py",
        "def helper():\n    return 2\n\ndef run():\n    return helper()\n",
    )
    _git(["add", "."], repo)
    _git(["commit", "-m", "head"], repo)
    head_sha = _git(["rev-parse", "HEAD"], repo)

    diff_text = _git(["diff", base_sha, head_sha], repo)

    return {
        "repo": repo,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "diff_text": diff_text,
    }


@pytest.fixture
def sample_diff_text() -> str:
    return """\
diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1,5 +1,5 @@
 def helper():
-    return 1
+    return 2

 def run():
     return helper()
"""


@pytest.fixture
def sample_parsed_diff(sample_diff_text: str):
    return parse_unified_diff(sample_diff_text)


@pytest.fixture
def sample_call_graph(tiny_repo_snapshot: Path) -> nx.DiGraph:
    return build_call_graph(tiny_repo_snapshot)


@pytest.fixture
def sample_pipeline_config() -> PipelineConfig:
    return PipelineConfig(
        k_up=1,
        k_down=1,
        max_nodes=25,
        max_edges=50,
        max_per_anchor=20,
        max_chars=10_000,
        include_code=True,
        include_diff_in_context=True,
    )


@pytest.fixture
def mock_review_payload() -> dict[str, object]:
    return {
        "overall_risk": "medium",
        "findings": [
            {
                "category": "correctness",
                "severity": "medium",
                "confidence": 0.9,
                "summary": "Potential regression in helper flow",
                "technical_reasoning": "The helper return value changed and callers may depend on previous behavior.",
                "suggested_fix": "Confirm callers expect the new return value.",
                "evidence": [
                    {
                        "node_id": "app.helper",
                        "file_path": "app.py",
                        "start_line": 1,
                        "end_line": 2,
                    }
                ],
            }
        ],
    }


@pytest.fixture
def mock_llm_response_text(mock_review_payload: dict[str, object]) -> str:
    return json.dumps(mock_review_payload)


@pytest.fixture
def sample_pr_info(tiny_repo_with_pr_history: dict[str, object]) -> PRInfo:
    return PRInfo(
        pr_id="42",
        repo_url=str(tiny_repo_with_pr_history["repo"]),
        base_sha=str(tiny_repo_with_pr_history["base_sha"]),
        head_sha=str(tiny_repo_with_pr_history["head_sha"]),
        diff_text=str(tiny_repo_with_pr_history["diff_text"]),
        metadata={"title": "Update helper"},
    )


class StubPRInfoProvider:
    def __init__(self, info: PRInfo) -> None:
        self.info = info
        self.calls = 0

    async def get_pr_info(self, pr_url: str) -> PRInfo:
        self.calls += 1
        return self.info


@pytest.fixture
def stub_pr_info_provider(sample_pr_info: PRInfo) -> StubPRInfoProvider:
    return StubPRInfoProvider(sample_pr_info)


class StubAsyncLLMProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

    async def generate(
        self,
        *,
        prompt: str,
        model_name: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "model_name": model_name,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            }
        )
        return self.text


@pytest.fixture
def stub_async_llm_provider(mock_llm_response_text: str) -> StubAsyncLLMProvider:
    return StubAsyncLLMProvider(mock_llm_response_text)


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.fixture
def sample_repo_files(tiny_repo_snapshot: Path) -> Iterator[list[Path]]:
    files = sorted(path for path in tiny_repo_snapshot.rglob("*.py") if path.is_file())
    yield files
