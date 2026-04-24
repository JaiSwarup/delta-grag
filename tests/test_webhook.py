from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from src.impact_subgraph import SubgraphStats
from src.pipeline.pr_orchestrator import PipelineResult, ReviewConfig
from src.webhook import WebhookSettings, create_app


class _StubCommenter:
    def __init__(self) -> None:
        self.comments: list[tuple[str, str]] = []

    def build_review_comment(self, review_payload):
        findings = review_payload.get("findings", [])
        return f"review findings={len(findings)}"

    async def post_review_comment(self, pr_url: str, body: str) -> None:
        self.comments.append((pr_url, body))


class _StubProvider:
    async def get_pr_info(self, pr_url: str):
        raise AssertionError("provider should not be called in these webhook tests")


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _payload(action: str = "opened") -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": 42,
            "html_url": "https://github.com/acme/widgets/pull/42",
            "title": "Example PR",
            "state": "open",
            "merged": False,
            "user": {"login": "octocat"},
            "head": {
                "sha": "headsha123",
                "ref": "feature-branch",
                "repo": {
                    "full_name": "acme/widgets",
                    "clone_url": "https://github.com/acme/widgets.git",
                    "html_url": "https://github.com/acme/widgets",
                },
            },
            "base": {
                "sha": "basesha123",
                "ref": "main",
                "repo": {
                    "full_name": "acme/widgets",
                    "clone_url": "https://github.com/acme/widgets.git",
                    "html_url": "https://github.com/acme/widgets",
                },
            },
        },
        "repository": {
            "full_name": "acme/widgets",
            "clone_url": "https://github.com/acme/widgets.git",
            "html_url": "https://github.com/acme/widgets",
        },
    }


def test_webhook_rejects_signature_mismatch() -> None:
    secret = "top-secret"
    settings = WebhookSettings(github_webhook_secret=secret)
    commenter = _StubCommenter()

    async def _unused_review_runner(*, pr_url, config, cache_dir, provider):
        raise AssertionError("review runner should not be called")

    app = create_app(
        settings=settings,
        provider=_StubProvider(),
        review_runner=_unused_review_runner,
        commenter=commenter,
    )
    client = TestClient(app)

    body = json.dumps(_payload()).encode("utf-8")
    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid webhook signature"
    assert commenter.comments == []


def test_webhook_processes_pull_request_event_and_returns_review_json() -> None:
    secret = "top-secret"
    settings = WebhookSettings(
        github_webhook_secret=secret,
        cache_dir=".cache/test-webhook",
        default_k_up=2,
        default_k_down=3,
    )
    commenter = _StubCommenter()
    calls: list[tuple[str, ReviewConfig, str, object]] = []

    async def _fake_review_runner(*, pr_url, config, cache_dir, provider):
        calls.append((pr_url, config, cache_dir, provider))
        return PipelineResult(
            pr_id="42",
            pr_url=pr_url,
            review={
                "findings": [
                    {
                        "severity": "medium",
                        "category": "correctness",
                        "summary": "Potential regression in helper flow",
                        "technical_reasoning": "Changed code path no longer validates input.",
                    }
                ],
                "overall_risk": "medium",
                "metadata": {"title": "Example PR"},
            },
            subgraph_stats=SubgraphStats(
                node_count=4,
                edge_count=3,
                anchor_count=1,
                caller_count=1,
                callee_count=2,
                shared_count=0,
                cutoff_reasons=(),
            ),
            timing_breakdown={"fetch_pr_info_ms": 1.2, "build_graph_ms": 5.8},
            context_tokens=321,
            cache_hit=False,
        )

    app = create_app(
        settings=settings,
        provider=_StubProvider(),
        review_runner=_fake_review_runner,
        commenter=commenter,
    )
    client = TestClient(app)

    body = json.dumps(_payload("synchronize")).encode("utf-8")
    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(secret, body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert payload["action"] == "synchronize"
    assert payload["event"] == "pull_request"
    assert payload["review"]["pr_id"] == "42"
    assert payload["review"]["pr_url"] == "https://github.com/acme/widgets/pull/42"
    assert payload["review"]["review"]["overall_risk"] == "medium"
    assert payload["review"]["subgraph_stats"]["anchor_count"] == 1
    assert payload["review"]["context_tokens"] == 321

    assert len(calls) == 1
    pr_url, config, cache_dir, provider = calls[0]
    assert pr_url == "https://github.com/acme/widgets/pull/42"
    assert config.k_up == 2
    assert config.k_down == 3
    assert cache_dir == ".cache/test-webhook"
    assert isinstance(provider, _StubProvider)

    assert commenter.comments == [
        (
            "https://github.com/acme/widgets/pull/42",
            "review findings=1",
        )
    ]
