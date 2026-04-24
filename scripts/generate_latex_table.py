from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, StrictUndefined

DEFAULT_INPUT_PATH = Path("results/metrics_table.csv")
DEFAULT_OUTPUT_PATH = Path("results/arxiv_table.tex")
DEFAULT_SYSTEM_ORDER = ("dgrag", "semantic_rag", "diff_only")
DEFAULT_METRIC_COLUMNS = (
    "structural_recall",
    "token_reduction_pct",
    "cross_file_detection_rate",
    "hallucination_rate",
    "bleu",
    "rouge_l",
)
DISPLAY_NAMES = {
    "dgrag": "D-GRAG",
    "semantic_rag": "Semantic RAG",
    "diff_only": "Diff-only",
}
METRIC_LABELS = {
    "structural_recall": "Structural Recall",
    "token_reduction_pct": "Token Reduction (\\%)",
    "cross_file_detection_rate": "Cross-file Detection",
    "hallucination_rate": "Hallucination Rate",
    "bleu": "BLEU",
    "rouge_l": "ROUGE-L",
}
LATEX_TEMPLATE = r"""
\begin{table}[t]
\centering
\small
\begin{tabular}{l{% for _ in systems %}c{% endfor %}}
\toprule
Metric{% for system in systems %} & {{ system.display_name }}{% endfor %} \\
\midrule
{% for row in rows -%}
{{ row.metric_label }}{% for cell in row.values %} & {{ cell }}{% endfor %} \\
{% endfor %}
\bottomrule
\end{tabular}
\caption{Aggregate D-GRAG evaluation results. Missing values are rendered as \textemdash.}
\label{tab:dgrag-results}
\end{table}
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an arXiv-friendly LaTeX table from results/metrics_table.csv."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help="Path to metrics CSV input.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to write the LaTeX table.",
    )
    parser.add_argument(
        "--caption",
        type=str,
        default="Aggregate D-GRAG evaluation results. Missing values are rendered as \\textemdash.",
        help="LaTeX table caption.",
    )
    parser.add_argument(
        "--label",
        type=str,
        default="tab:dgrag-results",
        help="LaTeX table label.",
    )
    return parser.parse_args()


def load_metrics_table(path: str | Path) -> pd.DataFrame:
    input_path = Path(path).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(
            f"Metrics table not found: {input_path}. Generate results/metrics_table.csv first."
        )

    df = pd.read_csv(input_path)
    required = {"system", *DEFAULT_METRIC_COLUMNS}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Metrics table is missing required columns: {sorted(missing)}"
        )
    return df


def build_summary_table(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()

    for column in DEFAULT_METRIC_COLUMNS:
        working[column] = pd.to_numeric(working[column], errors="coerce")

    grouped_raw = working.groupby("system", dropna=False)[
        list(DEFAULT_METRIC_COLUMNS)
    ].mean(numeric_only=True)
    grouped = pd.DataFrame(grouped_raw).reindex(DEFAULT_SYSTEM_ORDER)

    return grouped


def render_latex_table(
    summary_df: pd.DataFrame,
    *,
    caption: str,
    label: str,
) -> str:
    systems = [
        {
            "key": system,
            "display_name": DISPLAY_NAMES.get(system, system),
        }
        for system in summary_df.index.tolist()
    ]

    rows: list[dict[str, Any]] = []
    for metric in DEFAULT_METRIC_COLUMNS:
        rows.append(
            {
                "metric_key": metric,
                "metric_label": METRIC_LABELS.get(metric, metric),
                "values": [
                    format_metric_value(summary_df.loc[system["key"], metric])
                    for system in systems
                ],
            }
        )

    env = Environment(
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    template = env.from_string(
        LATEX_TEMPLATE.replace(
            r"\caption{Aggregate D-GRAG evaluation results. Missing values are rendered as \textemdash.}",
            rf"\caption{{{caption}}}",
        ).replace(
            r"\label{tab:dgrag-results}",
            rf"\label{{{label}}}",
        )
    )
    return template.render(systems=systems, rows=rows).strip() + "\n"


def format_metric_value(value: Any) -> str:
    if value is None:
        return r"\textemdash"

    try:
        numeric = float(value)
    except TypeError, ValueError:
        return r"\textemdash"

    if math.isnan(numeric):
        return r"\textemdash"

    if math.isinf(numeric):
        return r"\textemdash"

    return f"{numeric:.3f}"


def write_text(path: str | Path, content: str) -> Path:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def main() -> None:
    args = parse_args()

    df = load_metrics_table(args.input)
    summary = build_summary_table(df)
    latex = render_latex_table(
        summary,
        caption=args.caption,
        label=args.label,
    )
    output_path = write_text(args.output, latex)

    print(f"Wrote LaTeX table to: {output_path}")


if __name__ == "__main__":
    main()
