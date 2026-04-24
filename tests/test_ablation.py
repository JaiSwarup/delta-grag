from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd

from src.eval.ablation import (
    load_ablation_corpus,
    render_ablation_heatmap,
    run_ablation_sweep,
)
from src.pipeline.pr_orchestrator import ReviewConfig


def _write_corpus(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "pr_id": "pr-1",
                        "parser": "tree_sitter",
                        "ground_truth_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                        "ground_truth_cross_file_fqns": ["pkg.b.beta"],
                        "known_function_registry": [
                            "pkg.a.alpha",
                            "pkg.b.beta",
                            "pkg.c.gamma",
                        ],
                        "baseline_context_tokens": 300,
                        "reference_review_text": "Potential regression in helper flow.",
                        "variants": [
                            {
                                "k": 0,
                                "m": 0,
                                "retrieved_fqns": ["pkg.a.alpha"],
                                "context_tokens": 80,
                                "detected_cross_file_fqns": [],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 1,
                                "m": 1,
                                "retrieved_fqns": ["pkg.a.alpha"],
                                "context_tokens": 100,
                                "detected_cross_file_fqns": [],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 1,
                                "m": 2,
                                "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                                "context_tokens": 120,
                                "detected_cross_file_fqns": ["pkg.b.beta"],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 1,
                                "m": 3,
                                "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                                "context_tokens": 130,
                                "detected_cross_file_fqns": ["pkg.b.beta"],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 2,
                                "m": 1,
                                "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                                "context_tokens": 125,
                                "detected_cross_file_fqns": ["pkg.b.beta"],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 2,
                                "m": 2,
                                "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                                "context_tokens": 140,
                                "detected_cross_file_fqns": ["pkg.b.beta"],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 2,
                                "m": 3,
                                "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                                "context_tokens": 150,
                                "detected_cross_file_fqns": ["pkg.b.beta"],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 3,
                                "m": 1,
                                "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                                "context_tokens": 145,
                                "detected_cross_file_fqns": ["pkg.b.beta"],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 3,
                                "m": 2,
                                "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                                "context_tokens": 155,
                                "detected_cross_file_fqns": ["pkg.b.beta"],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                            {
                                "k": 3,
                                "m": 3,
                                "retrieved_fqns": ["pkg.a.alpha", "pkg.b.beta"],
                                "context_tokens": 160,
                                "detected_cross_file_fqns": ["pkg.b.beta"],
                                "issue_fqns": ["pkg.a.alpha"],
                                "generated_review_text": "Potential regression in helper flow.",
                            },
                        ],
                    },
                    {
                        "pr_id": "pr-2",
                        "parser": "tree_sitter",
                        "ground_truth_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                        "ground_truth_cross_file_fqns": ["pkg.d.delta"],
                        "known_function_registry": [
                            "pkg.c.gamma",
                            "pkg.d.delta",
                            "pkg.e.epsilon",
                        ],
                        "baseline_context_tokens": 400,
                        "reference_review_text": "Cross-file API impact detected.",
                        "variants": [
                            {
                                "k": 0,
                                "m": 0,
                                "retrieved_fqns": ["pkg.c.gamma"],
                                "context_tokens": 90,
                                "detected_cross_file_fqns": [],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 1,
                                "m": 1,
                                "retrieved_fqns": ["pkg.c.gamma"],
                                "context_tokens": 110,
                                "detected_cross_file_fqns": [],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 1,
                                "m": 2,
                                "retrieved_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                                "context_tokens": 130,
                                "detected_cross_file_fqns": ["pkg.d.delta"],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 1,
                                "m": 3,
                                "retrieved_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                                "context_tokens": 145,
                                "detected_cross_file_fqns": ["pkg.d.delta"],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 2,
                                "m": 1,
                                "retrieved_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                                "context_tokens": 140,
                                "detected_cross_file_fqns": ["pkg.d.delta"],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 2,
                                "m": 2,
                                "retrieved_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                                "context_tokens": 155,
                                "detected_cross_file_fqns": ["pkg.d.delta"],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 2,
                                "m": 3,
                                "retrieved_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                                "context_tokens": 165,
                                "detected_cross_file_fqns": ["pkg.d.delta"],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 3,
                                "m": 1,
                                "retrieved_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                                "context_tokens": 150,
                                "detected_cross_file_fqns": ["pkg.d.delta"],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 3,
                                "m": 2,
                                "retrieved_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                                "context_tokens": 170,
                                "detected_cross_file_fqns": ["pkg.d.delta"],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                            {
                                "k": 3,
                                "m": 3,
                                "retrieved_fqns": ["pkg.c.gamma", "pkg.d.delta"],
                                "context_tokens": 180,
                                "detected_cross_file_fqns": ["pkg.d.delta"],
                                "issue_fqns": ["pkg.c.gamma"],
                                "generated_review_text": "Cross-file API impact detected.",
                            },
                        ],
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_load_ablation_corpus_reads_expected_cases(tmp_path: Path) -> None:
    corpus_path = tmp_path / "ablation_corpus.json"
    _write_corpus(corpus_path)

    corpus = load_ablation_corpus(corpus_path)

    assert len(corpus.cases) == 2
    assert corpus.cases[0].pr_id == "pr-1"
    assert corpus.cases[0].parser == "tree_sitter"
    assert len(corpus.cases[0].variants) == 10
    assert corpus.cases[0].variant_for(k=0, m=0) is not None
    assert corpus.cases[0].variant_for(k=9, m=9) is None


def test_run_ablation_sweep_writes_nine_rows_and_heatmap(tmp_path: Path) -> None:
    corpus_path = tmp_path / "ablation_corpus.json"
    output_csv = tmp_path / "ablation_results.csv"
    heatmap_path = tmp_path / "ablation_heatmap.png"
    _write_corpus(corpus_path)

    df = asyncio.run(
        run_ablation_sweep(
            pr_corpus_path=corpus_path,
            base_config=ReviewConfig(k_up=1, k_down=1, max_nodes=20),
            k_values=[1, 2, 3],
            m_values=[1, 2, 3],
            parser_values=["tree_sitter"],
            max_concurrency=2,
            output_csv_path=output_csv,
            heatmap_path=heatmap_path,
        )
    )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 9
    assert output_csv.exists()
    assert heatmap_path.exists()
    assert heatmap_path.stat().st_size > 0

    expected_columns = {
        "k",
        "m",
        "parser",
        "case_count",
        "structural_recall",
        "token_reduction",
        "token_reduction_pct",
        "hallucination_rate",
        "cross_file_detection_rate",
        "bleu",
        "rouge_l",
    }
    assert expected_columns.issubset(df.columns)

    for column in [
        "structural_recall",
        "token_reduction",
        "token_reduction_pct",
        "hallucination_rate",
        "bleu",
        "rouge_l",
    ]:
        assert bool(df[column].notna().all())

    saved = pd.read_csv(output_csv)
    assert len(saved) == 9


def test_run_ablation_sweep_supports_k0_m0_without_crashing(tmp_path: Path) -> None:
    corpus_path = tmp_path / "ablation_corpus.json"
    output_csv = tmp_path / "ablation_zero.csv"
    heatmap_path = tmp_path / "ablation_zero.png"
    _write_corpus(corpus_path)

    df = asyncio.run(
        run_ablation_sweep(
            pr_corpus_path=corpus_path,
            base_config=ReviewConfig(k_up=0, k_down=0, max_nodes=10),
            k_values=[0],
            m_values=[0],
            parser_values=["tree_sitter"],
            max_concurrency=1,
            output_csv_path=output_csv,
            heatmap_path=heatmap_path,
        )
    )

    assert len(df) == 1
    row = df.to_dict(orient="records")[0]
    assert row["k"] == 0
    assert row["m"] == 0
    assert row["parser"] == "tree_sitter"
    assert row["case_count"] == 2
    assert 0.0 <= float(row["structural_recall"]) <= 1.0
    assert output_csv.exists()
    assert heatmap_path.exists()


def test_render_ablation_heatmap_succeeds_with_dataframe(tmp_path: Path) -> None:
    df = pd.DataFrame(
        [
            {
                "k": 1,
                "m": 1,
                "parser": "tree_sitter",
                "structural_recall": 0.25,
            },
            {
                "k": 1,
                "m": 2,
                "parser": "tree_sitter",
                "structural_recall": 0.50,
            },
            {
                "k": 2,
                "m": 1,
                "parser": "tree_sitter",
                "structural_recall": 0.75,
            },
            {
                "k": 2,
                "m": 2,
                "parser": "tree_sitter",
                "structural_recall": 1.00,
            },
        ]
    )
    output_path = tmp_path / "heatmap.png"

    rendered = render_ablation_heatmap(df, output_path=output_path)

    assert rendered == output_path.resolve()
    assert output_path.exists()
    assert output_path.stat().st_size > 0
