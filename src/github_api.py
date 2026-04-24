"""
GitHub PR service adapters for orchestrator and webhook integration.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from github import Github
from github.GithubException import GithubException

from src.pipeline.pr_orchestrator import PRInfo, PRInfoProvider, parse_github_pr_url


class GitHubIntegrationError(RuntimeError):
    """Raised when GitHub API operations fail."""


@dataclass(frozen=True)
class GitHubAuthConfig:
    token: str

    @classmethod
    def from_env(cls, env_var: str = "GITHUB_TOKEN") -> "GitHubAuthConfig":
        token = os.getenv(env_var, "").strip()
        if not token:
            raise GitHubIntegrationError(
                f"Missing GitHub API token. Set the `{env_var}` environment variable."
            )
        return cls(token=token)


class GitHubPRService(PRInfoProvider):
    """
    GitHub-backed PR service used by the CLI and webhook integration.

    Responsibilities:
    - fetch PR metadata and unified diff for `review_pr(...)`
    - post review results back to a PR as a comment
    """

    def __init__(
        self, token: str, *, api_base_url: str = "https://api.github.com"
    ) -> None:
        token = str(token).strip()
        if not token:
            raise ValueError("token must be a non-empty string")
        self._token = token
        self._api_base_url = api_base_url.rstrip("/")
        self._client = Github(login_or_token=token)

    @classmethod
    def from_env(cls, env_var: str = "GITHUB_TOKEN") -> "GitHubPRService":
        auth = GitHubAuthConfig.from_env(env_var=env_var)
        return cls(token=auth.token)

    async def get_pr_info(self, pr_url: str) -> PRInfo:
        return await asyncio.to_thread(self._get_pr_info_sync, pr_url)

    def _get_pr_info_sync(self, pr_url: str) -> PRInfo:
        parsed = parse_github_pr_url(pr_url)
        owner = parsed["owner"]
        repo_name = parsed["repo"]
        pr_number = int(parsed["number"])
        full_name = f"{owner}/{repo_name}"

        try:
            repo = self._client.get_repo(full_name)
            pull = repo.get_pull(pr_number)
        except GithubException as exc:
            raise GitHubIntegrationError(
                f"Failed to fetch PR metadata for `{pr_url}`: {exc.data if hasattr(exc, 'data') else exc}"
            ) from exc

        diff_text = self._fetch_pr_diff_sync(diff_url=getattr(pull, "diff_url", ""))
        head_repo = getattr(getattr(pull, "head", None), "repo", None)
        clone_url = getattr(head_repo, "clone_url", None) or getattr(
            repo, "clone_url", None
        )

        if not clone_url:
            raise GitHubIntegrationError(
                f"Could not determine clone URL for `{full_name}`."
            )

        base = getattr(pull, "base", None)
        head = getattr(pull, "head", None)
        base_sha = str(getattr(base, "sha", "") or "").strip()
        head_sha = str(getattr(head, "sha", "") or "").strip()

        if not base_sha or not head_sha:
            raise GitHubIntegrationError(f"Missing base/head SHA for `{pr_url}`.")

        metadata = {
            "title": getattr(pull, "title", None),
            "state": getattr(pull, "state", None),
            "html_url": getattr(pull, "html_url", pr_url),
            "author": getattr(getattr(pull, "user", None), "login", None),
            "base_ref": str(getattr(base, "ref", "") or "").strip(),
            "head_ref": str(getattr(head, "ref", "") or "").strip(),
            "merged": getattr(pull, "merged", None),
        }

        return PRInfo(
            pr_id=str(pull.number),
            repo_url=clone_url,
            base_sha=base_sha,
            head_sha=head_sha,
            diff_text=diff_text,
            metadata=metadata,
        )

    def _fetch_pr_diff_sync(self, diff_url: str) -> str:
        diff_url = str(diff_url).strip()
        if not diff_url:
            raise GitHubIntegrationError("Pull request diff URL is missing.")

        request = Request(
            diff_url,
            headers={
                "Accept": "application/vnd.github.v3.diff",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "btp-dgrag",
            },
            method="GET",
        )

        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GitHubIntegrationError(
                f"GitHub diff fetch failed with HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise GitHubIntegrationError(f"GitHub diff fetch failed: {exc}") from exc

        text = body.decode("utf-8", errors="replace")
        if not text.strip():
            raise GitHubIntegrationError("GitHub diff response was empty.")
        return text

    async def post_review_comment(self, pr_url: str, body: str) -> None:
        await asyncio.to_thread(self._post_review_comment_sync, pr_url, body)

    def _post_review_comment_sync(self, pr_url: str, body: str) -> None:
        parsed = parse_github_pr_url(pr_url)
        owner = parsed["owner"]
        repo_name = parsed["repo"]
        pr_number = int(parsed["number"])
        full_name = f"{owner}/{repo_name}"

        text = str(body).strip()
        if not text:
            raise ValueError("body must be a non-empty string")

        try:
            repo = self._client.get_repo(full_name)
            issue = repo.get_issue(number=pr_number)
            issue.create_comment(text)
        except GithubException as exc:
            raise GitHubIntegrationError(
                f"Failed to post PR comment for `{pr_url}`: {exc.data if hasattr(exc, 'data') else exc}"
            ) from exc

    def build_review_comment(self, review_payload: Mapping[str, Any]) -> str:
        findings = review_payload.get("findings", [])
        overall_risk = review_payload.get("overall_risk", "unknown")

        lines = [
            "## D-GRAG Review",
            "",
            f"- Overall risk: **{overall_risk}**",
            f"- Findings: **{len(findings) if isinstance(findings, list) else 0}**",
            "",
        ]

        if isinstance(findings, list) and findings:
            for idx, finding in enumerate(findings, start=1):
                if not isinstance(finding, Mapping):
                    continue
                summary = (
                    str(finding.get("summary", "Untitled finding")).strip()
                    or "Untitled finding"
                )
                severity = str(finding.get("severity", "unknown")).strip() or "unknown"
                reasoning = str(finding.get("technical_reasoning", "")).strip()
                lines.append(f"{idx}. **[{severity}]** {summary}")
                if reasoning:
                    lines.append(f"   - Reasoning: {reasoning}")
        else:
            lines.extend(
                [
                    "No findings were produced by the current review run.",
                ]
            )

        metadata = review_payload.get("metadata")
        if isinstance(metadata, Mapping) and metadata:
            lines.extend(
                [
                    "",
                    "<details>",
                    "<summary>Metadata</summary>",
                    "",
                    "```json",
                    json.dumps(dict(metadata), indent=2, ensure_ascii=False),
                    "```",
                    "</details>",
                ]
            )

        return "\n".join(lines)


def default_provider_from_env(env_var: str = "GITHUB_TOKEN") -> GitHubPRService:
    """Construct the default GitHub provider from environment configuration."""
    return GitHubPRService.from_env(env_var=env_var)


__all__ = [
    "GitHubAuthConfig",
    "GitHubIntegrationError",
    "GitHubPRService",
    "default_provider_from_env",
]
