"""
End-to-end review pipeline orchestration for Delta-GRAG.

Flow:
1) Parse unified diff -> changed files / changed hunks
2) Resolve anchors in call graph from changed hunks
3) Extract bounded impact subgraph (bidirectional BFS)
4) Linearize into structured context for downstream LLM review
5) Build review prompt
6) Invoke LLM
7) Postprocess findings (normalize, dedupe, score, format)

This module supports:
- retrieval-only mode
- full review mode with prompt + LLM + postprocessing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

import networkx as nx

from src.graph.impact_subgraph import extract_impact_subgraph
from src.ingestion.anchor_resolver import AnchorSet, resolve_anchors_from_parsed_diff
from src.ingestion.diff_parser import DiffParseResult, parse_unified_diff
from src.linearization.bfs_linearizer import DEFAULT_MAX_CHARS, linearize_subgraph
from src.llm.prompt_builder import PromptBuildConfig, build_review_prompt
from src.llm.transformers_client import TransformersClient, TransformersClientConfig
from src.postprocess.finding_deduper import (
    Citation as DedupCitation,
)
from src.postprocess.finding_deduper import (
    DeduperConfig,
    dedupe_findings,
)
from src.postprocess.finding_deduper import (
    Finding as DedupFinding,
)
from src.postprocess.formatter import format_review_json, format_review_markdown
from src.postprocess.review_types import (
    Finding as NormalizedFinding,
)
from src.postprocess.review_types import (
    normalize_review_output,
)
from src.postprocess.scoring import (
    aggregate_risk_level,
    score_findings,
    score_from_mapping,
)


@dataclass(frozen=True)
class PipelineConfig:
    """Configuration for PR impact retrieval + linearization + review generation."""

    # Retrieval / context shaping
    k_up: int = 2
    k_down: int = 3
    max_nodes: int = 180
    max_edges: int = 320
    max_per_anchor: int = 60
    traversal_time_ms: int | None = None
    max_chars: int = DEFAULT_MAX_CHARS
    include_code: bool = True
    include_diff_in_context: bool = False
    repo_root: str | None = None

    # Prompt/LLM settings
    run_full_review: bool = False
    strict_json_output: bool = True
    llm_backend: Optional[str] = None  # e.g. hf_pipeline
    llm_model_name: Optional[str] = None
    llm_temperature: float = 0.1
    llm_max_new_tokens: int = 2048

    # Optional deterministic mock override for local/CI smoke
    llm_mock_response_text: Optional[str] = None
    allow_dev_mock_controls: bool = False

    # Output formatting
    output_format: str = "markdown"  # markdown|json

    # Postprocessing controls
    dedupe_findings_enabled: bool = True
    include_suggested_fix_in_dedupe_key: bool = False


@dataclass
class ReviewPipelineResult:
    """
    Output bundle from pipeline run.

    Attributes
    ----------
    parsed_diff:
        Structured diff parse output.
    anchors:
        Anchor resolution result.
    impact_subgraph:
        Bounded induced subgraph around resolved anchors.
    node_order:
        Deterministic node order used for subgraph construction and traceability.
    linearized_context:
        Final serialized context for prompting/review.
    prompt:
        Final LLM prompt (when full review mode is enabled).
    raw_model_output:
        Raw text returned by LLM backend.
    normalized_review:
        Final normalized + deduped + scored review payload.
    formatted_review:
        Final user-facing review artifact (markdown/json).
    metadata:
        Extra execution metadata for observability/debugging.
    """

    parsed_diff: DiffParseResult
    anchors: AnchorSet
    impact_subgraph: nx.DiGraph
    node_order: List[str]
    linearized_context: str

    prompt: Optional[str] = None
    raw_model_output: Optional[str] = None

    normalized_review: Optional[Dict[str, Any]] = None
    formatted_review: Optional[str] = None

    metadata: Dict[str, Any] = field(default_factory=dict)


def run_review_pipeline(
    *,
    call_graph: nx.DiGraph,
    pr_diff: str,
    config: Optional[PipelineConfig] = None,
    pr_metadata: Optional[Mapping[str, Any]] = None,
) -> ReviewPipelineResult:
    """
    Execute full PR-aware retrieval + context shaping pipeline.
    """
    cfg = config or PipelineConfig()
    _validate_config(cfg)

    parsed = parse_unified_diff(pr_diff)
    return _run_core_pipeline(
        call_graph=call_graph,
        parsed_diff=parsed,
        raw_pr_diff=pr_diff,
        config=cfg,
        pr_metadata=pr_metadata,
    )


def run_review_pipeline_from_parsed_diff(
    *,
    call_graph: nx.DiGraph,
    parsed_diff: DiffParseResult,
    raw_pr_diff: str,
    config: Optional[PipelineConfig] = None,
    pr_metadata: Optional[Mapping[str, Any]] = None,
) -> ReviewPipelineResult:
    """
    Variant when parsed diff is already available from upstream ingestion.
    """
    cfg = config or PipelineConfig()
    _validate_config(cfg)

    return _run_core_pipeline(
        call_graph=call_graph,
        parsed_diff=parsed_diff,
        raw_pr_diff=raw_pr_diff,
        config=cfg,
        pr_metadata=pr_metadata,
    )


def _run_core_pipeline(
    *,
    call_graph: nx.DiGraph,
    parsed_diff: DiffParseResult,
    raw_pr_diff: str,
    config: PipelineConfig,
    pr_metadata: Optional[Mapping[str, Any]],
) -> ReviewPipelineResult:
    anchors = resolve_anchors_from_parsed_diff(
        call_graph,
        parsed_diff,
        pr_metadata=pr_metadata,
    )

    subgraph, node_order = extract_impact_subgraph(
        call_graph,
        anchors=anchors.anchor_node_ids,
        k_up=config.k_up,
        k_down=config.k_down,
        max_nodes=config.max_nodes,
        max_edges=config.max_edges,
        max_per_anchor=config.max_per_anchor,
        time_ms=config.traversal_time_ms,
    )

    context = linearize_subgraph(
        subgraph,
        pr_diff=raw_pr_diff,
        anchors=anchors.anchor_node_ids,
        max_chars=config.max_chars,
        include_code=config.include_code,
        include_diff_section=config.include_diff_in_context,
        repo_root=config.repo_root,
    )

    prompt: Optional[str] = None
    raw_model_output: Optional[str] = None
    normalized_review_payload: Optional[Dict[str, Any]] = None
    formatted_review: Optional[str] = None

    if config.run_full_review:
        prompt, raw_model_output = _generate_model_output(
            pr_diff=raw_pr_diff,
            linearized_context=context,
            pr_metadata=pr_metadata,
            cfg=config,
        )

        normalized = normalize_review_output(raw_model_output)
        post = _postprocess_findings(normalized.findings, cfg=config)

        overall_risk = aggregate_risk_level(post["scored_findings"])
        normalized_review_payload = {
            "overall_risk": overall_risk,
            "warnings": list(normalized.warnings),
            "findings": post["formatted_findings"],
        }

        if config.output_format == "json":
            formatted_review = format_review_json(
                findings=normalized_review_payload["findings"],
                metadata={
                    "pipeline": "review_pipeline",
                    "mode": "full_review",
                    "llm_backend": config.llm_backend,
                    "llm_model_name": config.llm_model_name,
                    "dedupe_enabled": config.dedupe_findings_enabled,
                },
                parse_warnings=normalized_review_payload["warnings"],
                raw_model_output=raw_model_output,
            )
        else:
            formatted_review = format_review_markdown(
                findings=normalized_review_payload["findings"],
                metadata={
                    "pipeline": "review_pipeline",
                    "mode": "full_review",
                    "llm_backend": config.llm_backend,
                    "llm_model_name": config.llm_model_name,
                    "dedupe_enabled": config.dedupe_findings_enabled,
                },
                parse_warnings=normalized_review_payload["warnings"],
                raw_model_output=raw_model_output,
            )

    metadata = _build_metadata(
        call_graph=call_graph,
        parsed=parsed_diff,
        anchors=anchors,
        impact_subgraph=subgraph,
        node_order=node_order,
        cfg=config,
        normalized_review=normalized_review_payload,
    )

    return ReviewPipelineResult(
        parsed_diff=parsed_diff,
        anchors=anchors,
        impact_subgraph=subgraph,
        node_order=node_order,
        linearized_context=context,
        prompt=prompt,
        raw_model_output=raw_model_output,
        normalized_review=normalized_review_payload,
        formatted_review=formatted_review,
        metadata=metadata,
    )


def _generate_model_output(
    *,
    pr_diff: str,
    linearized_context: str,
    pr_metadata: Optional[Mapping[str, Any]],
    cfg: PipelineConfig,
) -> tuple[str, str]:
    prompt_cfg = PromptBuildConfig(
        strict_json_output=cfg.strict_json_output,
        max_prompt_chars=max(512, cfg.max_chars),
    )
    prompt_result = build_review_prompt(
        pr_diff=pr_diff,
        linearized_context=linearized_context,
        pr_metadata=pr_metadata,
        config=prompt_cfg,
    )
    prompt = prompt_result.prompt

    client_cfg = TransformersClientConfig(
        backend=cfg.llm_backend,
        model_name=cfg.llm_model_name,
        temperature=cfg.llm_temperature,
        max_new_tokens=cfg.llm_max_new_tokens,
        mock_response_text=cfg.llm_mock_response_text,
        allow_dev_mock_controls=cfg.allow_dev_mock_controls,
    )
    client = TransformersClient(config=client_cfg)

    gen = client.generate(
        prompt,
        max_new_tokens=cfg.llm_max_new_tokens,
        temperature=cfg.llm_temperature,
        do_sample=False,
    )
    return prompt, gen.text


def _postprocess_findings(
    findings: Sequence[NormalizedFinding],
    *,
    cfg: PipelineConfig,
) -> Dict[str, Any]:
    """
    Normalize -> score -> dedupe -> score (final) pipeline.

    We intentionally perform scoring before dedupe so that dedupe decisions can use
    calibrated values, then score again after dedupe to keep consistency.
    """
    # Step 1: convert normalized findings to scored candidates.
    scored_candidates = [
        score_from_mapping(
            {
                "category": f.category,
                "severity": f.severity.value,
                "confidence": f.confidence,
                "summary": f.summary,
                "technical_reasoning": f.technical_reasoning,
                "suggested_fix": f.suggested_fix,
                "evidence": [
                    {
                        "node_id": ev.node_id,
                        "file_path": ev.file_path,
                        "start_line": ev.start_line,
                        "end_line": ev.end_line,
                    }
                    for ev in f.evidence
                ],
            }
        )
        for f in findings
    ]

    # Step 2: build dedupe entities preserving citations.
    dedupe_candidates = [
        DedupFinding(
            category=s.category,
            severity=s.severity,
            confidence=s.confidence,
            summary=s.summary,
            technical_reasoning=s.technical_reasoning,
            suggested_fix=s.suggested_fix,
            evidence=tuple(
                DedupCitation(
                    node_id=ev.node_id,
                    file_path=ev.file_path,
                    start_line=ev.start_line,
                    end_line=ev.end_line,
                )
                for ev in f.evidence
            ),
        )
        for s, f in zip(scored_candidates, findings)
    ]

    # Step 3: dedupe (optional).
    if cfg.dedupe_findings_enabled:
        deduped = dedupe_findings(
            dedupe_candidates,
            config=DeduperConfig(
                include_suggested_fix_in_key=cfg.include_suggested_fix_in_dedupe_key
            ),
        )
    else:
        deduped = list(dedupe_candidates)

    # Step 4: final scoring/ranking after dedupe for stable ordering.
    rescored = score_findings(
        [
            score_from_mapping(
                {
                    "category": d.category,
                    "severity": d.severity,
                    "confidence": d.confidence,
                    "summary": d.summary,
                    "technical_reasoning": d.technical_reasoning,
                    "suggested_fix": d.suggested_fix,
                    "evidence": [
                        {
                            "node_id": ev.node_id,
                            "file_path": ev.file_path,
                            "start_line": ev.start_line,
                            "end_line": ev.end_line,
                        }
                        for ev in d.evidence
                    ],
                }
            )
            for d in deduped
        ]
    )

    # Step 5: formatter-ready findings, preserving evidence from deduped items.
    by_identity: Dict[tuple[str, str, str], DedupFinding] = {
        (d.category, d.summary, d.technical_reasoning): d for d in deduped
    }

    formatted_findings: List[Dict[str, Any]] = []
    for s in rescored:
        d = by_identity.get((s.category, s.summary, s.technical_reasoning))
        evidence = []
        if d is not None:
            evidence = [
                {
                    "node_id": ev.node_id,
                    "file_path": ev.file_path,
                    "start_line": ev.start_line,
                    "end_line": ev.end_line,
                }
                for ev in d.evidence
            ]

        formatted_findings.append(
            {
                "category": s.category,
                "severity": s.severity,
                "confidence": s.confidence,
                "summary": s.summary,
                "technical_reasoning": s.technical_reasoning,
                "suggested_fix": s.suggested_fix,
                "evidence": evidence,
            }
        )

    return {
        "scored_findings": rescored,
        "formatted_findings": formatted_findings,
        "input_count": len(findings),
        "deduped_count": len(deduped),
        "output_count": len(formatted_findings),
    }


def summarize_pipeline_result(result: ReviewPipelineResult) -> Dict[str, Any]:
    """
    Produce a compact summary suitable for logs/telemetry/UI.
    """
    unresolved = len(result.anchors.unresolved_hunks)
    resolved = len(result.anchors.anchor_node_ids)
    changed_files = list(result.parsed_diff.changed_files)

    finding_count = 0
    overall_risk = None
    if isinstance(result.normalized_review, Mapping):
        findings = result.normalized_review.get("findings", [])
        if isinstance(findings, list):
            finding_count = len(findings)
        overall_risk = result.normalized_review.get("overall_risk")

    return {
        "changed_file_count": len(changed_files),
        "changed_files": changed_files,
        "resolved_anchor_count": resolved,
        "unresolved_hunk_count": unresolved,
        "impact_nodes": result.impact_subgraph.number_of_nodes(),
        "impact_edges": result.impact_subgraph.number_of_edges(),
        "node_order_count": len(result.node_order),
        "context_chars": len(result.linearized_context),
        "has_prompt": bool(result.prompt),
        "has_raw_model_output": bool(result.raw_model_output),
        "has_formatted_review": bool(result.formatted_review),
        "finding_count": finding_count,
        "overall_risk": overall_risk,
    }


def _validate_config(cfg: PipelineConfig) -> None:
    if cfg.k_up < 0 or cfg.k_down < 0:
        raise ValueError("k_up and k_down must be >= 0")
    if cfg.max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")
    if cfg.max_edges < 1:
        raise ValueError("max_edges must be >= 1")
    if cfg.max_per_anchor < 1:
        raise ValueError("max_per_anchor must be >= 1")
    if cfg.traversal_time_ms is not None and cfg.traversal_time_ms < 1:
        raise ValueError("traversal_time_ms must be >= 1 when provided")
    if cfg.max_chars < 256:
        raise ValueError("max_chars must be >= 256")
    if cfg.llm_max_new_tokens < 1:
        raise ValueError("llm_max_new_tokens must be >= 1")
    if cfg.llm_temperature < 0:
        raise ValueError("llm_temperature must be >= 0")
    if cfg.output_format not in {"markdown", "json"}:
        raise ValueError("output_format must be 'markdown' or 'json'")
    if cfg.llm_mock_response_text is not None and not cfg.allow_dev_mock_controls:
        raise ValueError(
            "llm_mock_response_text is disabled on the production pipeline path; "
            "set allow_dev_mock_controls=True for tests/dev-only usage"
        )
    if cfg.run_full_review:
        if not cfg.llm_backend or not str(cfg.llm_backend).strip():
            raise ValueError(
                "llm_backend must be configured when run_full_review is enabled"
            )
        if not cfg.llm_model_name or not str(cfg.llm_model_name).strip():
            raise ValueError(
                "llm_model_name must be configured when run_full_review is enabled"
            )


def _build_metadata(
    *,
    call_graph: nx.DiGraph,
    parsed: DiffParseResult,
    anchors: AnchorSet,
    impact_subgraph: nx.DiGraph,
    node_order: Sequence[str],
    cfg: PipelineConfig,
    normalized_review: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    changed_files = list(parsed.changed_files)
    findings = []
    overall_risk = None
    warnings = []
    if isinstance(normalized_review, Mapping):
        f = normalized_review.get("findings")
        if isinstance(f, list):
            findings = f
        overall_risk = normalized_review.get("overall_risk")
        w = normalized_review.get("warnings")
        if isinstance(w, list):
            warnings = w

    return {
        "config": {
            "k_up": cfg.k_up,
            "k_down": cfg.k_down,
            "max_nodes": cfg.max_nodes,
            "max_edges": cfg.max_edges,
            "max_per_anchor": cfg.max_per_anchor,
            "traversal_time_ms": cfg.traversal_time_ms,
            "max_chars": cfg.max_chars,
            "include_code": cfg.include_code,
            "include_diff_in_context": cfg.include_diff_in_context,
            "repo_root": cfg.repo_root,
            "run_full_review": cfg.run_full_review,
            "strict_json_output": cfg.strict_json_output,
            "llm_backend": cfg.llm_backend,
            "llm_model_name": cfg.llm_model_name,
            "llm_temperature": cfg.llm_temperature,
            "llm_max_new_tokens": cfg.llm_max_new_tokens,
            "output_format": cfg.output_format,
            "allow_dev_mock_controls": cfg.allow_dev_mock_controls,
            "dedupe_findings_enabled": cfg.dedupe_findings_enabled,
            "include_suggested_fix_in_dedupe_key": cfg.include_suggested_fix_in_dedupe_key,
        },
        "graph": {
            "total_nodes": call_graph.number_of_nodes(),
            "total_edges": call_graph.number_of_edges(),
        },
        "diff": {
            "changed_files": changed_files,
            "changed_file_count": len(changed_files),
            "hunk_count": sum(len(f.hunks) for f in parsed.files),
        },
        "anchors": {
            "resolved_ids": list(anchors.anchor_node_ids),
            "resolved_count": len(anchors.anchor_node_ids),
            "unresolved_count": len(anchors.unresolved_hunks),
            "hunk_to_anchor_count": len(anchors.hunk_to_anchor),
        },
        "impact_subgraph": {
            "nodes": impact_subgraph.number_of_nodes(),
            "edges": impact_subgraph.number_of_edges(),
            "node_order_count": len(node_order),
            "cutoff_reasons": list(impact_subgraph.graph.get("cutoff_reasons", ())),
            "cutoff_reason_count": len(impact_subgraph.graph.get("cutoff_reasons", ())),
        },
        "review": {
            "run_full_review": cfg.run_full_review,
            "finding_count": len(findings),
            "overall_risk": overall_risk,
            "warning_count": len(warnings),
        },
    }


__all__ = [
    "PipelineConfig",
    "ReviewPipelineResult",
    "run_review_pipeline",
    "run_review_pipeline_from_parsed_diff",
    "summarize_pipeline_result",
]
