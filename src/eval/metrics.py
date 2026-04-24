"""
Evaluation metrics engine for Delta-GRAG and baseline systems.

This module is intentionally corpus-agnostic: it computes reproducible metrics
from explicit evaluation inputs and can serialize a metrics table to CSV.

Task 26 target metrics:
- structural_recall
- context_token_reduction
- cross_file_detection_rate
- hallucination_rate
- BLEU
- ROUGE-L

The code supports the existing project result objects by exposing lightweight
extractors instead of hard-coding a single upstream corpus format.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator
from rouge_score import rouge_scorer
from sacrebleu.metrics import BLEU

from src.baselines.diff_only_reviewer import DiffOnlyReviewOutput
from src.baselines.file_context_reviewer import FileContextResult
from src.baselines.semantic_rag import SemanticRetrievalResult
from src.pipeline.pr_orchestrator import PipelineResult


class EvalResult(BaseModel):
    """
    Row-level evaluation output suitable for DataFrame/CSV export.
    """

    model_config = ConfigDict(extra="forbid")

    system: str
    pr_id: str
    structural_recall: float
    token_reduction_pct: float
    cross_file_detection_rate: float | None
    hallucination_rate: float
    bleu: float
    rouge_l: float

    @field_validator("structural_recall", "hallucination_rate", "bleu", "rouge_l")
    @classmethod
    def _validate_unit_interval(cls, value: float, info) -> float:
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{info.field_name} must be in [0, 1]")
        return float(value)

    @field_validator("token_reduction_pct")
    @classmethod
    def _validate_token_reduction(cls, value: float) -> float:
        if not -100.0 <= float(value) <= 100.0:
            raise ValueError("token_reduction_pct must be in [-100, 100]")
        return float(value)

    @field_validator("cross_file_detection_rate")
    @classmethod
    def _validate_optional_unit_interval(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(
                "cross_file_detection_rate must be in [0, 1] when provided"
            )
        return float(value)


class EvalCase(BaseModel):
    """
    Explicit evaluation input for one PR/system pair.

    This keeps the metrics engine deterministic and reusable, even before a
    fully wired ground-truth corpus loader exists in the repository.
    """

    model_config = ConfigDict(extra="forbid")

    system: str
    pr_id: str

    # Retrieval metrics
    retrieved_fqns: tuple[str, ...] = Field(default_factory=tuple)
    ground_truth_fqns: tuple[str, ...] = Field(default_factory=tuple)

    # Token efficiency metric
    context_tokens: int = 0
    baseline_context_tokens: int = 0

    # Cross-file behavior metric
    detected_cross_file_fqns: tuple[str, ...] = Field(default_factory=tuple)
    ground_truth_cross_file_fqns: tuple[str, ...] = Field(default_factory=tuple)

    # Hallucination metric
    issue_fqns: tuple[str, ...] = Field(default_factory=tuple)
    known_function_registry: tuple[str, ...] = Field(default_factory=tuple)

    # Text overlap metrics
    generated_review_text: str = ""
    reference_review_text: str = ""

    @field_validator("system", "pr_id")
    @classmethod
    def _validate_required_strings(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("value must be a non-empty string")
        return text

    @field_validator("context_tokens", "baseline_context_tokens")
    @classmethod
    def _validate_non_negative_ints(cls, value: int) -> int:
        value = int(value)
        if value < 0:
            raise ValueError("token counts must be >= 0")
        return value


def compute_structural_recall(
    retrieved: Iterable[str],
    ground_truth: Iterable[str],
) -> float:
    """
    Compute |R ∩ I| / |I|.

    Returns 0.0 when ground truth is empty so the metric stays defined and
    reproducible for degenerate cases.
    """
    retrieved_set = _normalized_set(retrieved)
    ground_truth_set = _normalized_set(ground_truth)
    if not ground_truth_set:
        return 0.0
    return len(retrieved_set & ground_truth_set) / len(ground_truth_set)


def compute_token_reduction(
    baseline_context_tokens: int,
    candidate_context_tokens: int,
) -> float:
    """
    Compute percentage reduction relative to the baseline context size.

    Formula:
        ((baseline - candidate) / baseline) * 100

    Returns 0.0 when the baseline is 0 to keep the metric defined.
    """
    baseline = int(baseline_context_tokens)
    candidate = int(candidate_context_tokens)
    if baseline < 0 or candidate < 0:
        raise ValueError("token counts must be >= 0")
    if baseline == 0:
        return 0.0
    return ((baseline - candidate) / baseline) * 100.0


def compute_cross_file_detection_rate(
    detected_cross_file_fqns: Iterable[str],
    ground_truth_cross_file_fqns: Iterable[str],
) -> float | None:
    """
    Compute cross-file detection rate over only true cross-file impacts.

    Edge case required by Task 26:
    - if a PR has 0 cross-file impacts in ground truth, return None (N/A)
    """
    detected = _normalized_set(detected_cross_file_fqns)
    ground_truth = _normalized_set(ground_truth_cross_file_fqns)
    if not ground_truth:
        return None
    return len(detected & ground_truth) / len(ground_truth)


def compute_hallucination_rate(
    issue_fqns: Iterable[str],
    known_fqns: Iterable[str],
) -> float:
    """
    Compute hallucination rate as:
        (# issue FQNs not in known registry) / (total issues with an FQN)

    Empty or missing issue FQNs are ignored. If no issue FQNs are present,
    returns 0.0.
    """
    issue_list = [item for item in (_normalize_text(v) for v in issue_fqns) if item]
    if not issue_list:
        return 0.0

    known = _normalized_set(known_fqns)
    hallucinated = sum(1 for fqn in issue_list if fqn not in known)
    return hallucinated / len(issue_list)


def compute_bleu(
    candidate_text: str,
    reference_text: str,
) -> float:
    """
    Compute sentence/corpus BLEU normalized into [0, 1].
    """
    candidate = _normalize_text(candidate_text)
    reference = _normalize_text(reference_text)
    if not candidate or not reference:
        return 0.0

    bleu = BLEU(effective_order=True)
    score = bleu.corpus_score([candidate], [[reference]])
    return max(0.0, min(1.0, float(score.score) / 100.0))


def compute_rouge_l(
    candidate_text: str,
    reference_text: str,
) -> float:
    """
    Compute ROUGE-L F1 normalized into [0, 1].
    """
    candidate = _normalize_text(candidate_text)
    reference = _normalize_text(reference_text)
    if not candidate or not reference:
        return 0.0

    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    score = scorer.score(reference, candidate)["rougeL"].fmeasure
    return max(0.0, min(1.0, float(score)))


def evaluate_case(case: EvalCase) -> EvalResult:
    """
    Compute all metrics for a single explicit evaluation case.
    """
    return EvalResult(
        system=case.system,
        pr_id=case.pr_id,
        structural_recall=compute_structural_recall(
            case.retrieved_fqns,
            case.ground_truth_fqns,
        ),
        token_reduction_pct=compute_token_reduction(
            case.baseline_context_tokens,
            case.context_tokens,
        ),
        cross_file_detection_rate=compute_cross_file_detection_rate(
            case.detected_cross_file_fqns,
            case.ground_truth_cross_file_fqns,
        ),
        hallucination_rate=compute_hallucination_rate(
            case.issue_fqns,
            case.known_function_registry,
        ),
        bleu=compute_bleu(
            case.generated_review_text,
            case.reference_review_text,
        ),
        rouge_l=compute_rouge_l(
            case.generated_review_text,
            case.reference_review_text,
        ),
    )


def build_metrics_table(cases: Sequence[EvalCase]) -> pd.DataFrame:
    """
    Evaluate a sequence of cases into a deterministic metrics table.

    Rows are sorted by (system, pr_id) for reproducibility.
    """
    rows = [
        evaluate_case(case).model_dump()
        for case in sorted(cases, key=lambda item: (item.system, item.pr_id))
    ]
    columns = [
        "system",
        "pr_id",
        "structural_recall",
        "token_reduction_pct",
        "cross_file_detection_rate",
        "hallucination_rate",
        "bleu",
        "rouge_l",
    ]
    return pd.DataFrame(rows, columns=columns)


def save_metrics_table(
    cases: Sequence[EvalCase],
    output_path: str | Path = "results/metrics_table.csv",
) -> pd.DataFrame:
    """
    Build the metrics table and save it to CSV.
    """
    df = build_metrics_table(cases)
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def case_from_pipeline_result(
    *,
    system: str,
    result: PipelineResult,
    ground_truth_fqns: Iterable[str],
    baseline_context_tokens: int,
    known_function_registry: Iterable[str],
    issue_fqns: Iterable[str] = (),
    detected_cross_file_fqns: Iterable[str] = (),
    ground_truth_cross_file_fqns: Iterable[str] = (),
    generated_review_text: str | None = None,
    reference_review_text: str = "",
) -> EvalCase:
    """
    Build an EvalCase from the orchestrator PipelineResult.

    Since Task 24's orchestrator result is retrieval-focused, callers can supply
    additional evaluation fields such as issue FQNs and reference review text.
    """
    review = _mapping(result.review)
    findings = review.get("findings", [])
    extracted_issue_fqns = tuple(issue_fqns) or extract_issue_fqns(findings)

    return EvalCase(
        system=system,
        pr_id=str(result.pr_id),
        retrieved_fqns=_coerce_anchor_like_fqns(review),
        ground_truth_fqns=tuple(_normalized_set(ground_truth_fqns)),
        context_tokens=int(result.context_tokens),
        baseline_context_tokens=int(baseline_context_tokens),
        detected_cross_file_fqns=tuple(_normalized_set(detected_cross_file_fqns)),
        ground_truth_cross_file_fqns=tuple(
            _normalized_set(ground_truth_cross_file_fqns)
        ),
        issue_fqns=tuple(_normalized_sequence(extracted_issue_fqns)),
        known_function_registry=tuple(_normalized_set(known_function_registry)),
        generated_review_text=generated_review_text or render_review_text(findings),
        reference_review_text=reference_review_text,
    )


def case_from_semantic_result(
    *,
    system: str,
    pr_id: str,
    result: SemanticRetrievalResult,
    ground_truth_fqns: Iterable[str],
    baseline_context_tokens: int,
    context_tokens: int | None = None,
    known_function_registry: Iterable[str] = (),
    issue_fqns: Iterable[str] = (),
    detected_cross_file_fqns: Iterable[str] = (),
    ground_truth_cross_file_fqns: Iterable[str] = (),
    generated_review_text: str = "",
    reference_review_text: str = "",
) -> EvalCase:
    """
    Build an EvalCase from the semantic retrieval baseline.
    """
    retrieved_fqns = [fqn for fqn, _score in result.retrieved]
    candidate_tokens = (
        result.query_tokens if context_tokens is None else int(context_tokens)
    )

    return EvalCase(
        system=system,
        pr_id=str(pr_id),
        retrieved_fqns=tuple(_normalized_sequence(retrieved_fqns)),
        ground_truth_fqns=tuple(_normalized_set(ground_truth_fqns)),
        context_tokens=candidate_tokens,
        baseline_context_tokens=int(baseline_context_tokens),
        detected_cross_file_fqns=tuple(_normalized_set(detected_cross_file_fqns)),
        ground_truth_cross_file_fqns=tuple(
            _normalized_set(ground_truth_cross_file_fqns)
        ),
        issue_fqns=tuple(_normalized_sequence(issue_fqns)),
        known_function_registry=tuple(_normalized_set(known_function_registry)),
        generated_review_text=generated_review_text,
        reference_review_text=reference_review_text,
    )


def case_from_diff_only_output(
    *,
    system: str,
    pr_id: str,
    result: DiffOnlyReviewOutput,
    ground_truth_fqns: Iterable[str],
    baseline_context_tokens: int,
    known_function_registry: Iterable[str],
    detected_cross_file_fqns: Iterable[str] = (),
    ground_truth_cross_file_fqns: Iterable[str] = (),
    reference_review_text: str = "",
) -> EvalCase:
    """
    Build an EvalCase from the diff-only baseline output.
    """
    findings = getattr(result.review, "review", None)
    normalized_findings = tuple(getattr(findings, "findings", ()))
    issue_fqns = extract_issue_fqns(normalized_findings)

    return EvalCase(
        system=system,
        pr_id=str(pr_id),
        retrieved_fqns=tuple(_normalized_set(ground_truth_fqns)),
        ground_truth_fqns=tuple(_normalized_set(ground_truth_fqns)),
        context_tokens=int(result.total_tokens),
        baseline_context_tokens=int(baseline_context_tokens),
        detected_cross_file_fqns=tuple(_normalized_set(detected_cross_file_fqns)),
        ground_truth_cross_file_fqns=tuple(
            _normalized_set(ground_truth_cross_file_fqns)
        ),
        issue_fqns=tuple(_normalized_sequence(issue_fqns)),
        known_function_registry=tuple(_normalized_set(known_function_registry)),
        generated_review_text=result.review.raw_text,
        reference_review_text=reference_review_text,
    )


def case_from_file_context_result(
    *,
    system: str,
    pr_id: str,
    result: FileContextResult,
    ground_truth_fqns: Iterable[str],
    baseline_context_tokens: int,
    known_function_registry: Iterable[str],
    retrieved_fqns: Iterable[str] = (),
    detected_cross_file_fqns: Iterable[str] = (),
    ground_truth_cross_file_fqns: Iterable[str] = (),
    reference_review_text: str = "",
) -> EvalCase:
    """
    Build an EvalCase from the file-context baseline output.
    """
    findings = getattr(result.review, "review", None)
    normalized_findings = tuple(getattr(findings, "findings", ()))
    issue_fqns = extract_issue_fqns(normalized_findings)

    return EvalCase(
        system=system,
        pr_id=str(pr_id),
        retrieved_fqns=tuple(_normalized_sequence(retrieved_fqns)),
        ground_truth_fqns=tuple(_normalized_set(ground_truth_fqns)),
        context_tokens=int(result.total_tokens),
        baseline_context_tokens=int(baseline_context_tokens),
        detected_cross_file_fqns=tuple(_normalized_set(detected_cross_file_fqns)),
        ground_truth_cross_file_fqns=tuple(
            _normalized_set(ground_truth_cross_file_fqns)
        ),
        issue_fqns=tuple(_normalized_sequence(issue_fqns)),
        known_function_registry=tuple(_normalized_set(known_function_registry)),
        generated_review_text=result.review.raw_text,
        reference_review_text=reference_review_text,
    )


def extract_issue_fqns(findings: Any) -> tuple[str, ...]:
    """
    Extract issue-linked FQNs or node identifiers from heterogeneous finding shapes.

    Supported sources:
    - mapping findings with "fqn"
    - mapping findings with evidence[].node_id
    - dataclass/object findings with .evidence[].node_id
    """
    out: list[str] = []

    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        return ()

    for finding in findings:
        mapped = _mapping_or_none(finding)
        if mapped is not None:
            fqn = _normalize_text(mapped.get("fqn"))
            if fqn:
                out.append(fqn)

            evidence = mapped.get("evidence", [])
            if isinstance(evidence, Sequence) and not isinstance(
                evidence, (str, bytes)
            ):
                for ev in evidence:
                    ev_map = _mapping_or_none(ev)
                    if ev_map is None:
                        continue
                    node_id = _normalize_text(ev_map.get("node_id"))
                    if node_id:
                        out.append(node_id)
            continue

        evidence = getattr(finding, "evidence", ())
        for ev in evidence:
            node_id = _normalize_text(getattr(ev, "node_id", ""))
            if node_id:
                out.append(node_id)

    return tuple(dict.fromkeys(out))


def render_review_text(findings: Any) -> str:
    """
    Render findings into deterministic plain text for BLEU/ROUGE evaluation when
    no upstream formatted review artifact is available.
    """
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        return ""

    lines: list[str] = []
    for idx, finding in enumerate(findings, start=1):
        mapped = _mapping_or_none(finding)
        if mapped is not None:
            severity = _normalize_text(mapped.get("severity")) or "unknown"
            summary = _normalize_text(mapped.get("summary")) or "Untitled finding"
            reasoning = _normalize_text(mapped.get("technical_reasoning"))
            lines.append(f"{idx}. [{severity}] {summary}")
            if reasoning:
                lines.append(f"reason: {reasoning}")
            continue

        severity = _normalize_text(getattr(finding, "severity", "")) or "unknown"
        summary = _normalize_text(getattr(finding, "summary", "")) or "Untitled finding"
        reasoning = _normalize_text(getattr(finding, "technical_reasoning", ""))
        lines.append(f"{idx}. [{severity}] {summary}")
        if reasoning:
            lines.append(f"reason: {reasoning}")

    return "\n".join(lines)


def _coerce_anchor_like_fqns(review: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Best-effort extraction for retrieval identifiers from orchestrator-style review payloads.

    The Task 24 result is intentionally lightweight, so this function only uses
    explicit review payload fields when present.
    """
    candidates: list[str] = []

    for key in ("retrieved_fqns", "anchor_fqns", "anchors", "node_order"):
        raw = review.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            candidates.extend(_normalized_sequence(raw))

    findings = review.get("findings", [])
    candidates.extend(extract_issue_fqns(findings))
    return tuple(dict.fromkeys(candidates))


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        raw = asdict(value)
        return raw if isinstance(raw, dict) else {}
    return {}


def _mapping_or_none(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        raw = asdict(value)
        return raw if isinstance(raw, dict) else None
    if hasattr(value, "__dict__"):
        raw = {
            key: item for key, item in vars(value).items() if not key.startswith("_")
        }
        return raw
    return None


def _normalized_set(values: Iterable[str]) -> set[str]:
    return set(_normalized_sequence(values))


def _normalized_sequence(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = _normalize_text(value)
        if text:
            out.append(text)
    return list(dict.fromkeys(out))


def _normalize_text(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return " ".join(text.split())


__all__ = [
    "EvalCase",
    "EvalResult",
    "build_metrics_table",
    "case_from_diff_only_output",
    "case_from_file_context_result",
    "case_from_pipeline_result",
    "case_from_semantic_result",
    "compute_bleu",
    "compute_cross_file_detection_rate",
    "compute_hallucination_rate",
    "compute_rouge_l",
    "compute_structural_recall",
    "compute_token_reduction",
    "evaluate_case",
    "extract_issue_fqns",
    "render_review_text",
    "save_metrics_table",
]
