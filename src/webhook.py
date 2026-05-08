"""
FastAPI GitHub webhook for orchestrated PR review.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.github_api import (
    GitHubIntegrationError,
    default_provider_from_env,
)
from src.pipeline.pr_orchestrator import PipelineResult, ReviewConfig, review_pr

logger = logging.getLogger(__name__)


class GitHubWebhookError(RuntimeError):
    """Raised when webhook configuration or processing fails."""


@dataclass(frozen=True)
class WebhookSettings:
    github_webhook_secret: str
    github_token_env_var: str = "GITHUB_TOKEN"
    cache_dir: str = ".cache/dgrag"
    default_k_up: int = 2
    default_k_down: int = 3
    review_timeout_seconds: int = 300

    @classmethod
    def from_env(cls) -> "WebhookSettings":
        secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise GitHubWebhookError(
                "Missing webhook secret. Set the `GITHUB_WEBHOOK_SECRET` environment variable."
            )

        token_env_var = (
            os.getenv("GITHUB_TOKEN_ENV_VAR", "GITHUB_TOKEN").strip() or "GITHUB_TOKEN"
        )
        cache_dir = (
            os.getenv("DGRAG_CACHE_DIR", ".cache/dgrag").strip() or ".cache/dgrag"
        )

        default_k_up = _read_int_env("DGRAG_DEPTH_K", 2)
        default_k_down = _read_int_env("DGRAG_DEPTH_M", 3)
        review_timeout_seconds = _read_int_env("DGRAG_REVIEW_TIMEOUT_SECONDS", 300)

        return cls(
            github_webhook_secret=secret,
            github_token_env_var=token_env_var,
            cache_dir=cache_dir,
            default_k_up=default_k_up,
            default_k_down=default_k_down,
            review_timeout_seconds=review_timeout_seconds,
        )


class GitHubCommenter(Protocol):
    async def post_review_comment(self, pr_url: str, body: str) -> None: ...
    def build_review_comment(self, review_payload: Mapping[str, Any]) -> str: ...


class GitHubWebhookRepositoryRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str | None = None
    clone_url: str | None = None
    html_url: str | None = None


class GitHubWebhookUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str | None = None


class GitHubWebhookPullRequestHead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: str | None = None
    ref: str | None = None
    repo: GitHubWebhookRepositoryRef | None = None


class GitHubWebhookPullRequestBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: str | None = None
    ref: str | None = None
    repo: GitHubWebhookRepositoryRef | None = None


class GitHubWebhookPullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int | None = None
    html_url: str | None = None
    title: str | None = None
    state: str | None = None
    merged: bool | None = None
    user: GitHubWebhookUser | None = None
    head: GitHubWebhookPullRequestHead | None = None
    base: GitHubWebhookPullRequestBase | None = None


class GitHubWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str
    pull_request: GitHubWebhookPullRequest | None = Field(default=None)
    repository: GitHubWebhookRepositoryRef | None = Field(default=None)


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise GitHubWebhookError(
            f"Environment variable `{name}` must be an integer."
        ) from exc
    if value < 0:
        raise GitHubWebhookError(f"Environment variable `{name}` must be >= 0.")
    return value


def _signature_for(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_github_signature(
    *, secret: str, body: bytes, signature_header: str | None
) -> bool:
    if not signature_header:
        return False
    expected = _signature_for(secret, body)
    return hmac.compare_digest(expected, signature_header.strip())


def _is_supported_action(action: str) -> bool:
    return action in {"opened", "reopened", "synchronize"}


def _result_to_response_payload(result: PipelineResult) -> dict[str, Any]:
    return {
        "pr_id": result.pr_id,
        "pr_url": result.pr_url,
        "cache_hit": result.cache_hit,
        "context_tokens": result.context_tokens,
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
    }


async def _default_review_runner(
    *,
    pr_url: str,
    config: ReviewConfig,
    cache_dir: str,
    provider: Any,
) -> PipelineResult:
    return await review_pr(
        pr_url=pr_url,
        config=config,
        cache_dir=cache_dir,
        provider=provider,
    )


def create_app(
    *,
    settings: WebhookSettings | None = None,
    provider: Any | None = None,
    review_runner: Any | None = None,
    commenter: GitHubCommenter | None = None,
) -> FastAPI:
    app = FastAPI(title="D-GRAG GitHub Webhook", version="0.1.0")
    resolved_settings = settings or WebhookSettings.from_env()
    resolved_provider = provider or default_provider_from_env(
        env_var=resolved_settings.github_token_env_var
    )
    resolved_review_runner = review_runner or _default_review_runner
    resolved_commenter = commenter or resolved_provider

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook/github")
    async def github_webhook(
        request: Request,
        x_hub_signature_256: str | None = Header(default=None),
        x_github_event: str | None = Header(default=None),
    ) -> dict[str, Any]:
        raw_body = await request.body()

        if not verify_github_signature(
            secret=resolved_settings.github_webhook_secret,
            body=raw_body,
            signature_header=x_hub_signature_256,
        ):
            logger.warning("Rejected GitHub webhook due to invalid signature.")
            raise HTTPException(status_code=403, detail="Invalid webhook signature")

        if (x_github_event or "").strip() != "pull_request":
            return {
                "ok": True,
                "ignored": True,
                "reason": "unsupported_event",
                "event": x_github_event,
            }

        try:
            payload = GitHubWebhookPayload.model_validate_json(raw_body)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid webhook payload: {exc}"
            ) from exc

        if not _is_supported_action(payload.action):
            return {
                "ok": True,
                "ignored": True,
                "reason": "unsupported_action",
                "action": payload.action,
            }

        pull_request = payload.pull_request
        if pull_request is None or not pull_request.html_url:
            raise HTTPException(
                status_code=400, detail="Payload is missing pull_request.html_url"
            )

        pr_url = pull_request.html_url
        config = ReviewConfig(
            k_up=resolved_settings.default_k_up,
            k_down=resolved_settings.default_k_down,
        )

        try:
            result = await asyncio.wait_for(
                resolved_review_runner(
                    pr_url=pr_url,
                    config=config,
                    cache_dir=resolved_settings.cache_dir,
                    provider=resolved_provider,
                ),
                timeout=float(resolved_settings.review_timeout_seconds),
            )
            comment_body = resolved_commenter.build_review_comment(result.review)
            await resolved_commenter.post_review_comment(pr_url, comment_body)
        except TimeoutError as exc:
            logger.exception("Webhook review timed out.")
            raise HTTPException(
                status_code=504,
                detail="Review timed out before completion.",
            ) from exc
        except GitHubIntegrationError as exc:
            logger.exception("GitHub integration failure while processing webhook.")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Unhandled webhook processing failure.")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return {
            "ok": True,
            "action": payload.action,
            "event": "pull_request",
            "review": _result_to_response_payload(result),
        }

    return app


try:
    app = create_app()
except GitHubWebhookError:
    app = FastAPI(title="D-GRAG GitHub Webhook", version="0.1.0")

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "config_error"}

    @app.post("/webhook/github")
    async def github_webhook_not_configured() -> None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Webhook app is not configured. Set the "
                "`GITHUB_WEBHOOK_SECRET` environment variable."
            ),
        )


__all__ = [
    "GitHubWebhookError",
    "GitHubWebhookPayload",
    "WebhookSettings",
    "app",
    "create_app",
    "verify_github_signature",
]
