from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.eval.benchmarks.impact_accuracy import run as run_impact_accuracy
from src.eval.benchmarks.token_efficiency import run as run_token_efficiency


@dataclass(frozen=True)
class EvalRunResult:
    json_path: Path
    markdown_path: Path
    payload: dict[str, Any]


def run_benchmarks(
    *,
    fixture_path: str | Path,
    output_dir: str | Path = "artifacts/eval",
) -> EvalRunResult:
    fixture_file = Path(fixture_path).expanduser().resolve()
    fixtures = json.loads(fixture_file.read_text(encoding="utf-8"))
    cases = list(fixtures.get("cases", []))

    impact = run_impact_accuracy(cases)
    efficiency = run_token_efficiency(cases)

    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture_path": str(fixture_file),
        "case_count": len(cases),
        "benchmarks": {
            "impact_accuracy": impact,
            "token_efficiency": efficiency,
        },
    }

    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "eval_report.json"
    markdown_path = out_dir / "eval_report.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    markdown_path.write_text(_to_markdown(payload), encoding="utf-8")

    return EvalRunResult(
        json_path=json_path,
        markdown_path=markdown_path,
        payload=payload,
    )


def _to_markdown(payload: dict[str, Any]) -> str:
    impact = payload["benchmarks"]["impact_accuracy"]
    efficiency = payload["benchmarks"]["token_efficiency"]
    lines = [
        "# Delta-GRAG Offline Evaluation Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Fixture: `{payload['fixture_path']}`",
        f"- Cases: **{payload['case_count']}**",
        "",
        "## Impact Accuracy",
        f"- Macro precision: **{impact['macro']['precision']:.4f}**",
        f"- Macro recall: **{impact['macro']['recall']:.4f}**",
        f"- Macro F1: **{impact['macro']['f1']:.4f}**",
        "",
        "## Token Efficiency",
        f"- Average context reduction: **{efficiency['average_reduction']:.4f}**",
        "",
    ]
    return "\n".join(lines)


__all__ = ["EvalRunResult", "run_benchmarks"]
