"""
Evaluation package for Delta-GRAG metrics and analysis.
"""

from .ablation import (
    DEFAULT_HEATMAP_PATH,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_OUTPUT_CSV,
    AblationCorpus,
    AblationCorpusCase,
    AblationVariant,
    load_ablation_corpus,
    render_ablation_heatmap,
    run_ablation_sweep,
)
from .metrics import (
    EvalCase,
    EvalResult,
    build_metrics_table,
    compute_bleu,
    compute_cross_file_detection_rate,
    compute_hallucination_rate,
    compute_rouge_l,
    compute_structural_recall,
    compute_token_reduction,
    save_metrics_table,
)

__all__ = [
    "AblationCorpus",
    "AblationCorpusCase",
    "AblationVariant",
    "DEFAULT_HEATMAP_PATH",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_OUTPUT_CSV",
    "EvalCase",
    "EvalResult",
    "build_metrics_table",
    "compute_bleu",
    "compute_cross_file_detection_rate",
    "compute_hallucination_rate",
    "compute_rouge_l",
    "compute_structural_recall",
    "compute_token_reduction",
    "load_ablation_corpus",
    "render_ablation_heatmap",
    "run_ablation_sweep",
    "save_metrics_table",
]
