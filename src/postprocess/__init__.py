"""Postprocess package for review finding normalization, scoring, and formatting."""

from .finding_deduper import dedupe_findings
from .formatter import findings_to_json, findings_to_markdown
from .scoring import calibrate_confidence, score_findings

__all__ = [
    "dedupe_findings",
    "score_findings",
    "calibrate_confidence",
    "findings_to_markdown",
    "findings_to_json",
]
