"""
Ablation study runner for Delta-GRAG retrieval depth sweeps.

This module is intentionally corpus-driven and reproducible. It evaluates
(k_up, k_down, parser) configurations against a JSON corpus of explicit
ablation cases, computes aggregate metrics via the Task 26 metrics engine,
writes a CSV table, and renders a structural-recall heatmap.

Expected corpus format (JSON):

{
  "cases": [
    {
      "pr_id": "123",
      "parser": "default",
      "ground_truth_fqns": ["pkg.a.alpha", "pkg.b.beta"],
      "ground_truth_cross_file_fqns": ["pkg.b.beta"],
      "known_function_registry": ["pkg.a.alpha", "pkg.b.beta", "pkg.c.gamma"],
      "baseline_context_tokens": 300,
      "reference_review_text": "Potential regression in helper flow.",
      "variants": [
        {
          "k": 1,
          "m": 1,
          "retrieved_fqns": ["pkg.a.alpha"],
          "context_tokens": 120,
          "detected_cross_file_fqns": [],
          "issue_fqns": ["pkg.a.alpha"],
          "generated_review_text": "Potential regression in helper flow."
        }
      ]
    }
  ]
}

Notes:
- `parser` is attached at the PR-case level.
- Multiple variants can exist for the same PR and parser.
- A variant is selected by exact (k, m) match.
- If no variant exists for a requested configuration, that PR is skipped for that run.
"""

from __future__ import annotations

import asyncio
import json
from itertools import product
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.eval.metrics import EvalCase, build_metrics_table
from src.pipeline.pr_orchestrator import ReviewConfig

DEFAULT_OUTPUT_CSV = "results/ablation_results.csv"
DEFAULT_HEATMAP_PATH = "results/ablation_heatmap.png"
DEFAULT_MAX_CONCURRENCY = 3


class AblationVariant(BaseModel):
    """One precomputed PR result for a specific (k, m) configuration."""

    model_config = ConfigDict(extra="forbid")

    k: int
    m: int
    retrieved_fqns: tuple[str, ...] = Field(default_factory=tuple)
    context_tokens: int = 0
    detected_cross_file_fqns: tuple[str, ...] = Field(default_factory=tuple)
    issue_fqns: tuple[str, ...] = Field(default_factory=tuple)
    generated_review_text: str = ""

    @field_validator("k", "m", "context_tokens")
    @classmethod
    def _validate_non_negative_ints(cls, value: int) -> int:
        value = int(value)
        if value < 0:
            raise ValueError("k, m, and context_tokens must be >= 0")
        return value


class AblationCorpusCase(BaseModel):
    """Corpus entry for one PR under one parser label."""

    model_config = ConfigDict(extra="forbid")

    pr_id: str
    parser: str = "default"
    ground_truth_fqns: tuple[str, ...] = Field(default_factory=tuple)
    ground_truth_cross_file_fqns: tuple[str, ...] = Field(default_factory=tuple)
    known_function_registry: tuple[str, ...] = Field(default_factory=tuple)
    baseline_context_tokens: int = 0
    reference_review_text: str = ""
    variants: tuple[AblationVariant, ...] = Field(default_factory=tuple)

    @field_validator("pr_id", "parser")
    @classmethod
    def _validate_required_strings(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("pr_id and parser must be non-empty strings")
        return text

    @field_validator("baseline_context_tokens")
    @classmethod
    def _validate_non_negative_tokens(cls, value: int) -> int:
        value = int(value)
        if value < 0:
            raise ValueError("baseline_context_tokens must be >= 0")
        return value

    def variant_for(self, *, k: int, m: int) -> AblationVariant | None:
        for variant in self.variants:
            if variant.k == k and variant.m == m:
                return variant
        return None


class AblationCorpus(BaseModel):
    """Top-level corpus model."""

    model_config = ConfigDict(extra="forbid")

    cases: tuple[AblationCorpusCase, ...] = Field(default_factory=tuple)


def load_ablation_corpus(path: str | Path) -> AblationCorpus:
    """Load and validate the ablation corpus JSON file."""
    corpus_path = Path(path).expanduser().resolve()
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    return AblationCorpus.model_validate(payload)


async def run_ablation_sweep(
    pr_corpus_path: str | Path,
    base_config: ReviewConfig,
    k_values: Sequence[int] = (1, 2, 3),
    m_values: Sequence[int] = (1, 2, 3),
    *,
    parser_values: Sequence[str] | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    output_csv_path: str | Path = DEFAULT_OUTPUT_CSV,
    heatmap_path: str | Path = DEFAULT_HEATMAP_PATH,
) -> pd.DataFrame:
    """
    Run an ablation sweep over (k, m, parser) configurations.

    Parameters
    ----------
    pr_corpus_path:
        Path to the JSON corpus described in this module docstring.
    base_config:
        Present for API compatibility with the roadmap. The current corpus-driven
        implementation records the requested depths but does not execute the live
        pipeline from this module.
    k_values, m_values:
        Depth values to sweep. `0` is allowed and must not crash.
    parser_values:
        Optional parser labels to evaluate. If omitted, all parser labels found in
        the corpus are used.
    max_concurrency:
        Maximum number of configuration jobs run concurrently.
    output_csv_path:
        Output path for the aggregated ablation CSV.
    heatmap_path:
        Output path for the structural-recall heatmap.
    """
    _validate_review_config(base_config)
    k_values = _validate_depth_values(k_values, "k_values")
    m_values = _validate_depth_values(m_values, "m_values")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")

    corpus = load_ablation_corpus(pr_corpus_path)
    parser_scope = _resolve_parser_values(corpus, parser_values)

    semaphore = asyncio.Semaphore(max_concurrency)
    tasks = [
        _run_configuration(
            semaphore=semaphore,
            corpus=corpus,
            parser=parser,
            k=k,
            m=m,
        )
        for parser in parser_scope
        for k, m in product(k_values, m_values)
    ]
    rows = await asyncio.gather(*tasks)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(by=["parser", "k", "m"], kind="stable").reset_index(
            drop=True
        )

    output_csv = Path(output_csv_path).expanduser().resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)

    render_ablation_heatmap(df, output_path=heatmap_path)
    return df


def render_ablation_heatmap(
    results_df: pd.DataFrame,
    *,
    output_path: str | Path = DEFAULT_HEATMAP_PATH,
    metric_column: str = "structural_recall",
) -> Path:
    """
    Render a heatmap of the selected metric across k/m combinations.

    If multiple parser labels are present, the heatmap uses the mean metric value
    for each (k, m) cell across parsers.
    """
    if metric_column not in results_df.columns:
        raise ValueError(f"Missing metric column: {metric_column}")

    required = {"k", "m", metric_column}
    missing = required - set(results_df.columns)
    if missing:
        raise ValueError(f"Missing required columns for heatmap: {sorted(missing)}")

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if results_df.empty:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.set_title("Ablation Heatmap")
        ax.set_xlabel("m")
        ax.set_ylabel("k")
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        fig.tight_layout()
        fig.savefig(output, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return output

    grouped = results_df.groupby(["k", "m"], as_index=False)[metric_column].mean()
    pivot = (
        pd.pivot_table(
            grouped,
            index="k",
            columns="m",
            values=metric_column,
            aggfunc="mean",
        )
        .sort_index(axis=0)
        .sort_index(axis=1)
    )

    fig_width = max(6, 1.6 * max(1, len(pivot.columns)))
    fig_height = max(4, 1.2 * max(1, len(pivot.index)))
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    sns.heatmap(
        pivot,
        annot=True,
        fmt=".3f",
        cmap="viridis",
        cbar=True,
        vmin=0.0,
        vmax=1.0,
        ax=ax,
    )
    ax.set_title(f"Ablation Heatmap: {metric_column}")
    ax.set_xlabel("m")
    ax.set_ylabel("k")
    fig.tight_layout()
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output


async def _run_configuration(
    *,
    semaphore: asyncio.Semaphore,
    corpus: AblationCorpus,
    parser: str,
    k: int,
    m: int,
) -> dict[str, Any]:
    async with semaphore:
        return await asyncio.to_thread(
            _compute_configuration_row,
            corpus=corpus,
            parser=parser,
            k=k,
            m=m,
        )


def _compute_configuration_row(
    *,
    corpus: AblationCorpus,
    parser: str,
    k: int,
    m: int,
) -> dict[str, Any]:
    cases: list[EvalCase] = []

    for record in corpus.cases:
        if record.parser != parser:
            continue
        variant = record.variant_for(k=k, m=m)
        if variant is None:
            continue

        cases.append(
            EvalCase(
                system="dgrag",
                pr_id=record.pr_id,
                retrieved_fqns=variant.retrieved_fqns,
                ground_truth_fqns=record.ground_truth_fqns,
                context_tokens=variant.context_tokens,
                baseline_context_tokens=record.baseline_context_tokens,
                detected_cross_file_fqns=variant.detected_cross_file_fqns,
                ground_truth_cross_file_fqns=record.ground_truth_cross_file_fqns,
                issue_fqns=variant.issue_fqns,
                known_function_registry=record.known_function_registry,
                generated_review_text=variant.generated_review_text,
                reference_review_text=record.reference_review_text,
            )
        )

    metrics_df = build_metrics_table(cases)
    return _aggregate_metrics(
        metrics_df, parser=parser, k=k, m=m, case_count=len(cases)
    )


def _aggregate_metrics(
    metrics_df: pd.DataFrame,
    *,
    parser: str,
    k: int,
    m: int,
    case_count: int,
) -> dict[str, Any]:
    if metrics_df.empty:
        return {
            "k": int(k),
            "m": int(m),
            "parser": parser,
            "case_count": int(case_count),
            "structural_recall": 0.0,
            "token_reduction": 0.0,
            "token_reduction_pct": 0.0,
            "hallucination_rate": 0.0,
            "cross_file_detection_rate": None,
            "bleu": 0.0,
            "rouge_l": 0.0,
        }

    cross_file_values = metrics_df["cross_file_detection_rate"].dropna().tolist()
    cross_file_value = (
        float(sum(float(value) for value in cross_file_values) / len(cross_file_values))
        if cross_file_values
        else None
    )

    structural_recall_mean = float(metrics_df["structural_recall"].mean())
    token_reduction_mean = float(metrics_df["token_reduction_pct"].mean())
    hallucination_rate_mean = float(metrics_df["hallucination_rate"].mean())
    bleu_mean = float(metrics_df["bleu"].mean())
    rouge_l_mean = float(metrics_df["rouge_l"].mean())

    return {
        "k": int(k),
        "m": int(m),
        "parser": parser,
        "case_count": int(case_count),
        "structural_recall": structural_recall_mean,
        "token_reduction": token_reduction_mean,
        "token_reduction_pct": token_reduction_mean,
        "hallucination_rate": hallucination_rate_mean,
        "cross_file_detection_rate": cross_file_value,
        "bleu": bleu_mean,
        "rouge_l": rouge_l_mean,
    }


def _resolve_parser_values(
    corpus: AblationCorpus,
    parser_values: Sequence[str] | None,
) -> tuple[str, ...]:
    if parser_values is not None:
        parsed = tuple(
            _dedupe_preserve_order(_normalize_text(v) for v in parser_values)
        )
        if not parsed:
            raise ValueError("parser_values must contain at least one non-empty parser")
        return parsed

    discovered = tuple(
        _dedupe_preserve_order(
            record.parser for record in corpus.cases if record.parser
        )
    )
    if not discovered:
        return ("default",)
    return discovered


def _validate_depth_values(values: Sequence[int], label: str) -> tuple[int, ...]:
    normalized: list[int] = []
    for value in values:
        ivalue = int(value)
        if ivalue < 0:
            raise ValueError(f"{label} must contain only values >= 0")
        normalized.append(ivalue)
    if not normalized:
        raise ValueError(f"{label} must contain at least one value")
    return tuple(_dedupe_preserve_order(normalized))


def _validate_review_config(config: ReviewConfig) -> None:
    if config.k_up < 0 or config.k_down < 0:
        raise ValueError("base_config depths must be >= 0")
    if config.max_nodes < 1:
        raise ValueError("base_config.max_nodes must be >= 1")
    if config.max_edges is not None and config.max_edges < 1:
        raise ValueError("base_config.max_edges must be >= 1 when provided")
    if config.max_per_anchor is not None and config.max_per_anchor < 1:
        raise ValueError("base_config.max_per_anchor must be >= 1 when provided")


def _dedupe_preserve_order(values: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_text(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return " ".join(text.split())


__all__ = [
    "AblationCorpus",
    "AblationCorpusCase",
    "AblationVariant",
    "DEFAULT_HEATMAP_PATH",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_OUTPUT_CSV",
    "load_ablation_corpus",
    "render_ablation_heatmap",
    "run_ablation_sweep",
]
