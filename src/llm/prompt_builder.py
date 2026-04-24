"""
Prompt builder for assembling structured PR review prompts.

This module takes:
- PR metadata
- Diff text
- Linearized graph context (from retrieval/linearization pipeline)
- Optional review rubric / policy

and produces a deterministic, structured prompt string suitable for code-review LLMs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

DEFAULT_REVIEW_RUBRIC: Sequence[str] = (
    "Correctness and logical bugs",
    "Regression risk and blast radius",
    "API/contract changes and compatibility",
    "Security concerns (auth, injection, secrets, unsafe defaults)",
    "Performance and scalability impact",
    "Concurrency / race conditions / ordering hazards",
    "Tests and observability gaps",
)


DEFAULT_OUTPUT_SCHEMA = {
    "findings": [
        {
            "category": "string",
            "severity": "LOW|MEDIUM|HIGH|CRITICAL",
            "confidence": "0.0-1.0",
            "summary": "string",
            "technical_reasoning": "string",
            "evidence": [
                {
                    "node_id": "string",
                    "file_path": "string",
                    "start_line": 1,
                    "end_line": 2,
                }
            ],
            "suggested_fix": "string",
        }
    ],
    "overall_risk": "LOW|MEDIUM|HIGH|CRITICAL",
}


@dataclass(frozen=True)
class PromptBuildConfig:
    """
    Configuration for prompt generation.
    """

    include_system_header: bool = True
    include_schema: bool = True
    include_rubric: bool = True
    max_prompt_chars: int = 500_000
    strict_json_output: bool = True
    rubric_items: Sequence[str] = field(
        default_factory=lambda: tuple(DEFAULT_REVIEW_RUBRIC)
    )


@dataclass
class PromptBuildResult:
    """
    Built prompt + metadata useful for tracing/debugging.
    """

    prompt: str
    metadata: Dict[str, Any] = field(default_factory=dict)


def build_review_prompt(
    *,
    pr_diff: str,
    linearized_context: str,
    pr_metadata: Optional[Mapping[str, Any]] = None,
    review_policy: Optional[str] = None,
    config: Optional[PromptBuildConfig] = None,
) -> PromptBuildResult:
    """
    Build a deterministic review prompt for PR-aware code review.
    """
    cfg = config or PromptBuildConfig()
    _validate_config(cfg)

    if not isinstance(pr_diff, str):
        raise TypeError("pr_diff must be a string")
    if not isinstance(linearized_context, str):
        raise TypeError("linearized_context must be a string")

    meta = dict(pr_metadata or {})
    sections: List[str] = []

    if cfg.include_system_header:
        sections.append(_build_system_header())

    sections.append(_build_task_instructions(cfg.strict_json_output))
    sections.append(_build_pr_metadata_section(meta))
    sections.append(_build_diff_section(pr_diff))
    sections.append(_build_context_section(linearized_context))

    if cfg.include_rubric:
        sections.append(_build_rubric_section(cfg.rubric_items))

    if review_policy:
        sections.append(_build_policy_section(review_policy))

    if cfg.include_schema:
        sections.append(_build_output_schema_section(cfg.strict_json_output))

    prompt = "\n\n".join(s for s in sections if s.strip())
    prompt = _truncate_if_needed(prompt, cfg.max_prompt_chars)

    result_meta = {
        "prompt_chars": len(prompt),
        "diff_chars": len(pr_diff),
        "context_chars": len(linearized_context),
        "has_policy": bool(review_policy and review_policy.strip()),
        "metadata_keys": sorted(meta.keys()),
        "strict_json_output": cfg.strict_json_output,
    }

    return PromptBuildResult(prompt=prompt, metadata=result_meta)


def build_prompt_from_pipeline_result(
    *,
    pipeline_result: Any,
    review_policy: Optional[str] = None,
    config: Optional[PromptBuildConfig] = None,
) -> PromptBuildResult:
    """
    Convenience wrapper that accepts pipeline result (duck-typed).

    Expected attributes:
    - pipeline_result.linearized_context: str
    - pipeline_result.parsed_diff.files (optional, for metadata extraction only)
    """
    if pipeline_result is None:
        raise ValueError("pipeline_result cannot be None")

    linearized_context = getattr(pipeline_result, "linearized_context", "")
    if not isinstance(linearized_context, str):
        raise TypeError("pipeline_result.linearized_context must be a string")

    parsed_diff = getattr(pipeline_result, "parsed_diff", None)
    diff_text = _reconstruct_diff_hint(parsed_diff)

    pipeline_meta = _extract_pipeline_metadata(pipeline_result)

    return build_review_prompt(
        pr_diff=diff_text,
        linearized_context=linearized_context,
        pr_metadata=pipeline_meta,
        review_policy=review_policy,
        config=config,
    )


def _build_system_header() -> str:
    return (
        "# SYSTEM ROLE\n"
        "You are a senior software engineer performing PR-aware code review.\n"
        "Use only evidence from the provided diff and graph-context sections.\n"
        "Prioritize high-signal, actionable findings."
    )


def _build_task_instructions(strict_json_output: bool) -> str:
    tail = (
        "Return ONLY valid JSON that matches the requested schema."
        if strict_json_output
        else "Return structured findings following the requested schema."
    )
    return (
        "# TASK\n"
        "Review the pull request for correctness, risk, and maintainability.\n"
        "Focus on changed logic and its upstream/downstream impact.\n"
        f"{tail}"
    )


def _build_pr_metadata_section(meta: Mapping[str, Any]) -> str:
    if not meta:
        return "# PR METADATA\n- (none provided)"

    lines = ["# PR METADATA"]
    for k in sorted(meta.keys()):
        lines.append(f"- {k}: {meta[k]}")
    return "\n".join(lines)


def _build_diff_section(pr_diff: str) -> str:
    safe = pr_diff.strip() if pr_diff.strip() else "(empty diff)"
    return f"# PR DIFF\n```diff\n{safe}\n```"


def _build_context_section(linearized_context: str) -> str:
    safe = (
        linearized_context.strip() if linearized_context.strip() else "(empty context)"
    )
    return f"# LINEARIZED IMPACT CONTEXT\n{safe}"


def _build_rubric_section(items: Iterable[str]) -> str:
    lines = ["# REVIEW RUBRIC"]
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {item}")
    return "\n".join(lines)


def _build_policy_section(review_policy: str) -> str:
    return f"# REVIEW POLICY\n{review_policy.strip()}"


def _build_output_schema_section(strict_json_output: bool) -> str:
    schema_lines = [
        "# OUTPUT SCHEMA",
        "Use this shape exactly:",
        "{",
        '  "findings": [',
        "    {",
        '      "category": "string",',
        '      "severity": "LOW|MEDIUM|HIGH|CRITICAL",',
        '      "confidence": "0.0-1.0",',
        '      "summary": "string",',
        '      "technical_reasoning": "string",',
        '      "evidence": [',
        "        {",
        '          "node_id": "string",',
        '          "file_path": "string",',
        '          "start_line": 1,',
        '          "end_line": 2',
        "        }",
        "      ],",
        '      "suggested_fix": "string"',
        "    }",
        "  ],",
        '  "overall_risk": "LOW|MEDIUM|HIGH|CRITICAL"',
        "}",
    ]
    if strict_json_output:
        schema_lines.append("Do not include markdown code fences in the output.")
    return "\n".join(schema_lines)


def _truncate_if_needed(text: str, max_chars: int) -> str:
    if max_chars < 512:
        raise ValueError("max_prompt_chars must be >= 512")
    if len(text) <= max_chars:
        return text

    suffix = "\n\n<!-- PROMPT TRUNCATED: max_prompt_chars reached -->\n"
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix


def _validate_config(cfg: PromptBuildConfig) -> None:
    if cfg.max_prompt_chars < 512:
        raise ValueError("max_prompt_chars must be >= 512")


def _extract_pipeline_metadata(pipeline_result: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    meta = getattr(pipeline_result, "metadata", None)
    if isinstance(meta, Mapping):
        for k, v in meta.items():
            out[f"pipeline_{k}"] = v

    anchors = getattr(pipeline_result, "anchors", None)
    if anchors is not None:
        anchor_ids = getattr(anchors, "anchor_node_ids", None)
        unresolved = getattr(anchors, "unresolved_hunks", None)
        if isinstance(anchor_ids, list):
            out["anchor_count"] = len(anchor_ids)
        if isinstance(unresolved, list):
            out["unresolved_hunk_count"] = len(unresolved)

    return out


def _reconstruct_diff_hint(parsed_diff: Any) -> str:
    """
    Build a lightweight diff hint from parsed diff when raw diff text
    is not directly available.
    """
    if parsed_diff is None:
        return "(raw diff not provided)"

    files = getattr(parsed_diff, "files", None)
    if not files:
        return "(raw diff not provided)"

    lines: List[str] = []
    for f in files:
        path = (
            getattr(f, "path", None)
            or getattr(f, "new_path", None)
            or getattr(f, "old_path", None)
        )
        if path:
            lines.append(f"diff --git a/{path} b/{path}")
        hunks = getattr(f, "hunks", None) or []
        for h in hunks:
            old_start = getattr(h, "old_start", 0)
            old_count = getattr(h, "old_count", 0)
            new_start = getattr(h, "new_start", 0)
            new_count = getattr(h, "new_count", 0)
            lines.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@")

    return "\n".join(lines) if lines else "(raw diff not provided)"


__all__ = [
    "PromptBuildConfig",
    "PromptBuildResult",
    "build_review_prompt",
    "build_prompt_from_pipeline_result",
]
