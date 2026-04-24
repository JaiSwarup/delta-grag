from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from src.github_api import (
    GitHubAuthConfig,
    GitHubIntegrationError,
    GitHubPRService,
    default_provider_from_env,
)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeHTTPError(HTTPError):
    def __init__(self, body: str, code: int = 403) -> None:
        super().__init__(
            url="https://example.test/diff",
            code=code,
            msg="forbidden",
            hdrs=None,
            fp=None,
        )
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body


class _FakeIssue:
    def __init__(self) -> None:
        self.comments: list[str] = []

    def create_comment(self, body: str) -> None:
        self.comments.append(body)


class _FakeRepo:
    def __init__(
        self,
        pull=None,
        issue=None,
        clone_url: str = "https://github.com/acme/widgets.git",
    ) -> None:
        self._pull = pull
        self._issue = issue or _FakeIssue()
        self.clone_url = clone_url

    def get_pull(self, number: int):
        assert number == 42
        return self._pull

    def get_issue(self, number: int):
        assert number == 42
        return self._issue


class _FakeGithubClient:
    def __init__(self, repo: _FakeRepo) -> None:
        self._repo = repo
        self.requested_full_names: list[str] = []

    def get_repo(self, full_name: str) -> _FakeRepo:
        self.requested_full_names.append(full_name)
        return self._repo


def _make_pull(
    *,
    number: int = 42,
    diff_url: str = "https://example.test/diff",
    clone_url: str = "https://github.com/acme/widgets.git",
    base_sha: str = "base123",
    head_sha: str = "head456",
):
    return SimpleNamespace(
        number=number,
        diff_url=diff_url,
        title="Improve helper flow",
        state="open",
        html_url="https://github.com/acme/widgets/pull/42",
        user=SimpleNamespace(login="octocat"),
        merged=False,
        base=SimpleNamespace(sha=base_sha, ref="main"),
        head=SimpleNamespace(
            sha=head_sha,
            ref="feature/refactor",
            repo=SimpleNamespace(clone_url=clone_url),
        ),
    )


def test_github_auth_config_from_env_reads_token(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")

    cfg = GitHubAuthConfig.from_env()

    assert cfg.token == "secret-token"


def test_github_auth_config_from_env_raises_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(GitHubIntegrationError) as exc_info:
        GitHubAuthConfig.from_env()

    assert "Missing GitHub API token" in str(exc_info.value)


def test_default_provider_from_env_constructs_service(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")

    service = default_provider_from_env()

    assert isinstance(service, GitHubPRService)


def test_github_pr_service_init_rejects_blank_token() -> None:
    with pytest.raises(ValueError) as exc_info:
        GitHubPRService("   ")

    assert "token must be a non-empty string" in str(exc_info.value)


def test_fetch_pr_diff_sync_returns_text(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.github_api.urlopen",
        lambda request, timeout=30: _FakeResponse(b"diff --git a/x b/x\n"),
    )

    service = GitHubPRService("secret-token")
    text = service._fetch_pr_diff_sync("https://example.test/diff")

    assert "diff --git" in text


def test_fetch_pr_diff_sync_raises_for_empty_url() -> None:
    service = GitHubPRService("secret-token")

    with pytest.raises(GitHubIntegrationError) as exc_info:
        service._fetch_pr_diff_sync("")

    assert "diff URL is missing" in str(exc_info.value)


def test_fetch_pr_diff_sync_raises_for_http_error(monkeypatch) -> None:
    def _raise_http_error(request, timeout=30):
        raise _FakeHTTPError("forbidden body", code=403)

    monkeypatch.setattr("src.github_api.urlopen", _raise_http_error)

    service = GitHubPRService("secret-token")
    with pytest.raises(GitHubIntegrationError) as exc_info:
        service._fetch_pr_diff_sync("https://example.test/diff")

    message = str(exc_info.value)
    assert "HTTP 403" in message
    assert "forbidden body" in message


def test_fetch_pr_diff_sync_raises_for_url_error(monkeypatch) -> None:
    def _raise_url_error(request, timeout=30):
        raise URLError("offline")

    monkeypatch.setattr("src.github_api.urlopen", _raise_url_error)

    service = GitHubPRService("secret-token")
    with pytest.raises(GitHubIntegrationError) as exc_info:
        service._fetch_pr_diff_sync("https://example.test/diff")

    assert "GitHub diff fetch failed" in str(exc_info.value)


def test_get_pr_info_sync_returns_pr_info(monkeypatch) -> None:
    pull = _make_pull()
    repo = _FakeRepo(pull=pull)
    fake_client = _FakeGithubClient(repo)

    service = GitHubPRService("secret-token")
    service._client = fake_client

    monkeypatch.setattr(
        service,
        "_fetch_pr_diff_sync",
        lambda diff_url: "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-1\n+2\n",
    )

    info = service._get_pr_info_sync("https://github.com/acme/widgets/pull/42")

    assert info.pr_id == "42"
    assert info.repo_url == "https://github.com/acme/widgets.git"
    assert info.base_sha == "base123"
    assert info.head_sha == "head456"
    assert "diff --git" in info.diff_text
    assert info.metadata["title"] == "Improve helper flow"
    assert info.metadata["author"] == "octocat"
    assert fake_client.requested_full_names == ["acme/widgets"]


def test_get_pr_info_sync_falls_back_to_repo_clone_url(monkeypatch) -> None:
    pull = _make_pull(clone_url="")
    pull.head.repo = None
    repo = _FakeRepo(
        pull=pull, clone_url="https://github.com/acme/widgets-fallback.git"
    )
    fake_client = _FakeGithubClient(repo)

    service = GitHubPRService("secret-token")
    service._client = fake_client

    monkeypatch.setattr(
        service, "_fetch_pr_diff_sync", lambda diff_url: "diff --git a/x b/x\n"
    )

    info = service._get_pr_info_sync("https://github.com/acme/widgets/pull/42")

    assert info.repo_url == "https://github.com/acme/widgets-fallback.git"


def test_get_pr_info_sync_raises_when_clone_url_missing(monkeypatch) -> None:
    pull = _make_pull(clone_url="")
    pull.head.repo = None
    repo = _FakeRepo(pull=pull, clone_url="")
    fake_client = _FakeGithubClient(repo)

    service = GitHubPRService("secret-token")
    service._client = fake_client

    monkeypatch.setattr(
        service, "_fetch_pr_diff_sync", lambda diff_url: "diff --git a/x b/x\n"
    )

    with pytest.raises(GitHubIntegrationError) as exc_info:
        service._get_pr_info_sync("https://github.com/acme/widgets/pull/42")

    assert "Could not determine clone URL" in str(exc_info.value)


def test_get_pr_info_sync_raises_when_base_or_head_sha_missing(monkeypatch) -> None:
    pull = _make_pull(base_sha="", head_sha="")
    repo = _FakeRepo(pull=pull)
    fake_client = _FakeGithubClient(repo)

    service = GitHubPRService("secret-token")
    service._client = fake_client

    monkeypatch.setattr(
        service, "_fetch_pr_diff_sync", lambda diff_url: "diff --git a/x b/x\n"
    )

    with pytest.raises(GitHubIntegrationError) as exc_info:
        service._get_pr_info_sync("https://github.com/acme/widgets/pull/42")

    assert "Missing base/head SHA" in str(exc_info.value)


def test_get_pr_info_async_wraps_sync_logic(monkeypatch) -> None:
    pull = _make_pull()
    repo = _FakeRepo(pull=pull)
    fake_client = _FakeGithubClient(repo)

    service = GitHubPRService("secret-token")
    service._client = fake_client

    monkeypatch.setattr(
        service, "_fetch_pr_diff_sync", lambda diff_url: "diff --git a/x b/x\n"
    )

    info = asyncio.run(service.get_pr_info("https://github.com/acme/widgets/pull/42"))

    assert info.pr_id == "42"
    assert info.metadata["base_ref"] == "main"
    assert info.metadata["head_ref"] == "feature/refactor"


def test_post_review_comment_sync_creates_issue_comment() -> None:
    issue = _FakeIssue()
    pull = _make_pull()
    repo = _FakeRepo(pull=pull, issue=issue)
    fake_client = _FakeGithubClient(repo)

    service = GitHubPRService("secret-token")
    service._client = fake_client

    service._post_review_comment_sync(
        "https://github.com/acme/widgets/pull/42",
        "hello review",
    )

    assert issue.comments == ["hello review"]


def test_post_review_comment_sync_rejects_blank_body() -> None:
    service = GitHubPRService("secret-token")

    with pytest.raises(ValueError) as exc_info:
        service._post_review_comment_sync(
            "https://github.com/acme/widgets/pull/42",
            "   ",
        )

    assert "body must be a non-empty string" in str(exc_info.value)


def test_post_review_comment_async_calls_sync_path() -> None:
    issue = _FakeIssue()
    pull = _make_pull()
    repo = _FakeRepo(pull=pull, issue=issue)
    fake_client = _FakeGithubClient(repo)

    service = GitHubPRService("secret-token")
    service._client = fake_client

    asyncio.run(
        service.post_review_comment(
            "https://github.com/acme/widgets/pull/42",
            "async comment",
        )
    )

    assert issue.comments == ["async comment"]


def test_build_review_comment_renders_findings_and_metadata() -> None:
    service = GitHubPRService("secret-token")

    body = service.build_review_comment(
        {
            "overall_risk": "medium",
            "findings": [
                {
                    "severity": "high",
                    "summary": "Potential null dereference",
                    "technical_reasoning": "A value is used before validation.",
                },
                {
                    "severity": "low",
                    "summary": "Minor cleanup",
                    "technical_reasoning": "",
                },
            ],
            "metadata": {"title": "Example PR", "author": "octocat"},
        }
    )

    assert "## D-GRAG Review" in body
    assert "- Overall risk: **medium**" in body
    assert "- Findings: **2**" in body
    assert "1. **[high]** Potential null dereference" in body
    assert "Reasoning: A value is used before validation." in body
    assert "<summary>Metadata</summary>" in body
    assert '"title": "Example PR"' in body


def test_build_review_comment_handles_empty_findings() -> None:
    service = GitHubPRService("secret-token")

    body = service.build_review_comment(
        {
            "overall_risk": "low",
            "findings": [],
        }
    )

    assert "- Findings: **0**" in body
    assert "No findings were produced by the current review run." in body
