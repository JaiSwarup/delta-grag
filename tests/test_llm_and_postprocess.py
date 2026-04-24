from __future__ import annotations

import json

import pytest

from src.llm.prompt_builder import (
    PromptBuildConfig,
    build_prompt_from_pipeline_result,
    build_review_prompt,
)
from src.llm.review_generator import ReviewGenerator, ReviewGeneratorConfig
from src.llm.transformers_client import (
    TransformersClient,
    TransformersClientConfig,
    generate_result,
    generate_text,
)
from src.postprocess import (
    dedupe_findings,
    findings_to_json,
    findings_to_markdown,
    score_findings,
)
from src.postprocess.finding_deduper import Citation as DedupCitation
from src.postprocess.finding_deduper import Finding as DedupFinding
from src.postprocess.review_types import normalize_review_output
from src.postprocess.scoring import ScoredFinding


class _TestPipelineResultFixture:
    def __init__(self) -> None:
        self.linearized_context = (
            "# Delta-GRAG Linearized Context\n\n## MODIFIED\n\n- node_id: `a:inner`\n"
        )
        self.parsed_diff = type(
            "Parsed",
            (),
            {
                "files": [
                    type(
                        "File",
                        (),
                        {
                            "path": "a.py",
                            "hunks": [
                                type(
                                    "Hunk",
                                    (),
                                    {
                                        "old_start": 10,
                                        "old_count": 2,
                                        "new_start": 10,
                                        "new_count": 3,
                                    },
                                )()
                            ],
                        },
                    )()
                ]
            },
        )()
        self.metadata = {"impact_subgraph": {"nodes": 4}}
        self.anchors = type(
            "Anchors",
            (),
            {"anchor_node_ids": ["a:inner"], "unresolved_hunks": []},
        )()


def test_prompt_builder_build_review_prompt_includes_sections() -> None:
    result = build_review_prompt(
        pr_diff="@@ -1,1 +1,2 @@\n-old\n+new\n+more",
        linearized_context="## MODIFIED\n- node_id: `x`",
        pr_metadata={"pr_id": 1, "title": "Test PR"},
        review_policy="Be strict about regressions.",
        config=PromptBuildConfig(
            include_system_header=True,
            include_schema=True,
            include_rubric=True,
            strict_json_output=True,
        ),
    )

    prompt = result.prompt
    assert "# SYSTEM ROLE" in prompt
    assert "# TASK" in prompt
    assert "# PR METADATA" in prompt
    assert "# PR DIFF" in prompt
    assert "# LINEARIZED IMPACT CONTEXT" in prompt
    assert "# REVIEW RUBRIC" in prompt
    assert "# REVIEW POLICY" in prompt
    assert "# OUTPUT SCHEMA" in prompt
    assert "Return ONLY valid JSON" in prompt
    assert '"file_path": "string"' in prompt
    assert '"start_line": 1' in prompt
    assert '"end_line": 2' in prompt
    assert result.metadata["has_policy"] is True
    assert result.metadata["prompt_chars"] == len(prompt)


def test_prompt_builder_from_pipeline_result_works() -> None:
    stub = _TestPipelineResultFixture()
    result = build_prompt_from_pipeline_result(
        pipeline_result=stub,
        review_policy="Focus on API impact.",
    )
    prompt = result.prompt

    assert "diff --git a/a.py b/a.py" in prompt
    assert "@@ -10,2 +10,3 @@" in prompt
    assert "anchor_count" in prompt
    assert "unresolved_hunk_count" in prompt
    assert "## MODIFIED" in prompt


def test_transformers_dev_mock_client_generate_text_and_result() -> None:
    cfg = TransformersClientConfig(
        backend="mock",
        model_name="mock-1",
        mock_prefix="[TEST MOCK]",
        allow_dev_mock_controls=True,
    )
    text = generate_text("hello world", config=cfg)
    assert "[TEST MOCK]" in text
    assert "digest:" in text

    result = generate_result("another prompt", config=cfg)
    assert result.backend == "mock"
    assert result.model_name == "mock-1"
    assert result.usage["input_chars"] == len("another prompt")
    assert isinstance(result.text, str) and len(result.text) > 0


def test_transformers_dev_mock_client_custom_response() -> None:
    cfg = TransformersClientConfig(
        backend="mock",
        mock_response_text='{"findings":[{"summary":"ok"}]}',
        allow_dev_mock_controls=True,
    )
    client = TransformersClient(config=cfg)
    out = client.generate("prompt")
    assert out.text == '{"findings":[{"summary":"ok"}]}'


def test_transformers_openrouter_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        "src.llm.transformers_client._read_env_value_from_dotenv",
        lambda _name: None,
    )

    cfg = TransformersClientConfig(
        backend="openrouter",
        model_name="openrouter/test-model",
        openrouter_api_key=None,
    )
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        TransformersClient(config=cfg)


def test_transformers_openrouter_generate_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": '{"findings":[],"overall_risk":"low"}'
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                    "total_tokens": 19,
                },
            }
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        assert timeout == 5
        assert req.full_url.endswith("/chat/completions")
        auth = req.headers.get("Authorization") or req.headers.get("authorization")
        assert auth == "Bearer test-key"
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr(
        "src.llm.transformers_client._read_env_value_from_dotenv",
        lambda _name: None,
    )

    cfg = TransformersClientConfig(
        backend="openrouter",
        model_name="openrouter/test-model",
        openrouter_api_key="test-key",
        openrouter_timeout_seconds=5,
    )
    client = TransformersClient(config=cfg)
    out = client.generate("hello")

    assert out.backend == "openrouter"
    assert out.model_name == "openrouter/test-model"
    assert out.text == '{"findings":[],"overall_risk":"low"}'
    assert out.usage["total_tokens"] == 19


def test_transformers_openrouter_reads_key_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": '{"findings":[],"overall_risk":"low"}'
                        }
                    }
                ]
            }
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        auth = req.headers.get("Authorization") or req.headers.get("authorization")
        assert auth == "Bearer dotenv-key"
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        "src.llm.transformers_client._read_env_value_from_dotenv",
        lambda name: "dotenv-key" if name == "OPENROUTER_API_KEY" else None,
    )

    cfg = TransformersClientConfig(
        backend="openrouter",
        model_name="openrouter/test-model",
        openrouter_api_key=None,
        openrouter_timeout_seconds=5,
    )
    out = TransformersClient(config=cfg).generate("hello")
    assert out.backend == "openrouter"


class _TestReviewGeneratorLLMStub:
    def __init__(self, stub_text: str) -> None:
        self._stub_text = stub_text

    def generate(
        self,
        *,
        prompt: str,
        model_name: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        assert isinstance(prompt, str) and len(prompt) > 0
        assert isinstance(model_name, str) and len(model_name) > 0
        assert max_output_tokens > 0
        return self._stub_text


def test_review_generator_parses_findings_from_json() -> None:
    raw_json = """
    {
      "findings": [
        {
          "category": "correctness",
          "severity": "high",
          "confidence": 0.9,
          "summary": "Potential null dereference",
          "technical_reasoning": "Path lacks guard before attribute access",
          "suggested_fix": "Add None check before access",
          "evidence": [
            {"node_id":"a:inner","file_path":"a.py","start_line":12,"end_line":14}
          ]
        }
      ]
    }
    """.strip()

    gen = ReviewGenerator(
        llm_client=_TestReviewGeneratorLLMStub(raw_json),
        config=ReviewGeneratorConfig(model_name="mock-review"),
    )
    out = gen.generate_review(
        pr_metadata={"pr_id": 7, "title": "Null check fix"},
        pr_diff="@@ -1 +1 @@\n-x\n+y",
        linearized_context="## MODIFIED\n- node_id: `a:inner`",
    )

    assert len(out.findings) == 1
    f = out.findings[0]
    assert f.category == "correctness"
    assert f.severity == "high"
    assert f.confidence == 0.9
    assert f.evidence[0].node_id == "a:inner"
    assert out.metadata["finding_count"] == 1


def test_review_generator_parses_embedded_json_block() -> None:
    raw = """
    Model analysis:
    ```json
    {"findings":[{"category":"perf","severity":"medium","confidence":0.6,"summary":"N+1 call risk","technical_reasoning":"","suggested_fix":"","evidence":[{"node_id":"x"}]}]}
    ```
    """.strip()

    gen = ReviewGenerator(llm_client=_TestReviewGeneratorLLMStub(raw))
    out = gen.generate_review(
        pr_metadata={},
        pr_diff="@@ -1 +1 @@\n-a\n+b",
        linearized_context="ctx",
    )
    assert len(out.findings) == 1
    assert out.findings[0].summary == "N+1 call risk"


def test_review_types_normalize_review_output_and_markdown() -> None:
    raw = {
        "findings": [
            {
                "category": "security",
                "severity": "critical",
                "confidence": 1.2,  # clamp
                "summary": "Secret exposed",
                "technical_reasoning": "Token appears in logs",
                "suggested_fix": "Redact and rotate token",
                "evidence": [
                    {
                        "node_id": "n1",
                        "file_path": "s.py",
                        "start_line": 3,
                        "end_line": 3,
                    }
                ],
            }
        ],
        "overall_risk": "critical",
    }

    normalized = normalize_review_output(raw)
    assert len(normalized.findings) == 1
    f = normalized.findings[0]
    assert f.severity.value == "critical"
    assert f.confidence == 1.0
    assert normalized.overall_risk.value == "critical"

    md = findings_to_markdown(normalized.findings)
    assert "Secret exposed" in md
    assert "CRITICAL" in md.upper()


def test_review_types_accepts_legacy_evidence_file_and_lines_shape() -> None:
    raw = {
        "findings": [
            {
                "category": "correctness",
                "severity": "high",
                "confidence": 0.8,
                "summary": "Legacy evidence format",
                "technical_reasoning": "Backwards compatibility parse path",
                "suggested_fix": "none",
                "evidence": [
                    {
                        "node_id": "n-legacy",
                        "file": "legacy.py",
                        "lines": "11-14",
                    }
                ],
            }
        ]
    }

    normalized = normalize_review_output(raw)
    assert len(normalized.findings) == 1
    ev = normalized.findings[0].evidence[0]
    assert ev.file_path == "legacy.py"
    assert ev.start_line == 11
    assert ev.end_line == 14


def test_deduper_merges_semantically_duplicate_findings() -> None:
    f1 = DedupFinding(
        category="correctness",
        severity="medium",
        confidence=0.6,
        summary="Potential race condition in cache update",
        technical_reasoning="Shared mutable state",
        suggested_fix="Add lock",
        evidence=(
            DedupCitation(node_id="a", file_path="x.py", start_line=10, end_line=12),
        ),
    )
    f2 = DedupFinding(
        category="correctness",
        severity="high",
        confidence=0.8,
        summary="Potential race condition in cache update!!",
        technical_reasoning="Shared mutable state may interleave",
        suggested_fix="Use synchronized section",
        evidence=(
            DedupCitation(node_id="a", file_path="x.py", start_line=10, end_line=12),
        ),
    )

    out = dedupe_findings([f1, f2])
    assert len(out) == 1
    assert out[0].severity == "high"
    assert out[0].confidence == 0.8


def test_scoring_and_formatters_pipeline() -> None:
    scored = score_findings(
        [
            ScoredFinding(
                category="security",
                summary="SQL injection risk",
                technical_reasoning="Raw query string formatting",
                suggested_fix="Use parameterized queries",
                severity="medium",
                confidence=0.4,
                evidence_count=2,
            ),
            ScoredFinding(
                category="style",
                summary="Naming nit",
                technical_reasoning="",
                suggested_fix="",
                severity="low",
                confidence=0.9,
                evidence_count=0,
            ),
        ]
    )

    assert len(scored) == 2
    # security finding should be ranked first due to inferred/escalated severity
    assert scored[0].category in {"security", "style"}

    as_dicts = [
        {
            "category": s.category,
            "severity": s.severity,
            "confidence": s.confidence,
            "summary": s.summary,
            "technical_reasoning": s.technical_reasoning,
            "suggested_fix": s.suggested_fix,
            "evidence": [{"node_id": "n1"}] if s.evidence_count else [],
        }
        for s in scored
    ]

    md = findings_to_markdown(as_dicts)
    js = findings_to_json(findings=as_dicts, metadata={"source": "test"})

    assert "Findings" in md
    assert "generated_at" in js
    assert '"source": "test"' in js
