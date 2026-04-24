"""
Post-processing scoring and calibration utilities for review findings.

This module provides deterministic helpers to:
- normalize severity labels
- derive severity from textual signals
- calibrate confidence from evidence quality and signal strength
- score and rank findings for downstream formatting/reporting

The implementation is intentionally lightweight and dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

SEVERITY_ORDER = ("low", "medium", "high", "critical")
SEVERITY_TO_SCORE = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


@dataclass(frozen=True)
class ScoredFinding:
    """
    Canonical finding shape used by scoring utilities.
    """

    category: str
    summary: str
    technical_reasoning: str
    suggested_fix: str
    severity: str = "medium"
    confidence: float = 0.5
    evidence_count: int = 0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScoringConfig:
    """
    Scoring and calibration configuration.
    """

    # confidence calibration weights
    evidence_weight: float = 0.25
    reason_weight: float = 0.20
    fix_weight: float = 0.10
    severity_weight: float = 0.25
    category_weight: float = 0.20

    # confidence bounds
    min_confidence: float = 0.05
    max_confidence: float = 0.99

    # evidence saturation
    evidence_saturation: int = 5

    # keyword-driven severity escalation
    enable_keyword_escalation: bool = True

    # tie-break behavior
    prefer_higher_confidence_on_equal_severity: bool = True


def normalize_severity(severity: Any, default: str = "medium") -> str:
    s = str(severity).strip().lower() if severity is not None else ""
    if s in SEVERITY_TO_SCORE:
        return s
    return default


def severity_score(severity: Any) -> int:
    return SEVERITY_TO_SCORE[normalize_severity(severity)]


def clamp_confidence(
    confidence: Any, min_value: float = 0.0, max_value: float = 1.0
) -> float:
    try:
        val = float(confidence)
    except Exception:
        return max(min_value, min(max_value, 0.5))
    if val < min_value:
        return min_value
    if val > max_value:
        return max_value
    return val


def infer_severity_from_text(
    summary: str,
    reasoning: str = "",
    category: str = "",
    default: str = "medium",
) -> str:
    """
    Infer severity from high-signal keywords and category hints.
    """
    text = f"{summary}\n{reasoning}\n{category}".lower()

    critical_hits = (
        "remote code execution",
        "rce",
        "privilege escalation",
        "sql injection",
        "auth bypass",
        "secret leak",
        "data corruption",
        "data loss",
        "critical outage",
    )
    high_hits = (
        "security",
        "vulnerability",
        "race condition",
        "deadlock",
        "breaking change",
        "panic",
        "crash",
        "memory leak",
    )
    low_hits = (
        "nit",
        "style",
        "readability",
        "naming",
        "minor",
        "optional",
    )

    if any(k in text for k in critical_hits):
        return "critical"
    if any(k in text for k in high_hits):
        return "high"
    if any(k in text for k in low_hits):
        return "low"
    return normalize_severity(default)


def calibrate_confidence(
    finding: ScoredFinding,
    config: ScoringConfig = ScoringConfig(),
) -> float:
    """
    Calibrate confidence based on evidence richness and textual quality.
    """
    sev = normalize_severity(finding.severity)
    sev_component = severity_score(sev) / 4.0

    evidence = max(0, int(finding.evidence_count))
    evidence_component = min(1.0, evidence / max(1, config.evidence_saturation))

    reason_component = _nonempty_score(finding.technical_reasoning)
    fix_component = _nonempty_score(finding.suggested_fix)
    category_component = _category_specificity_score(finding.category)

    raw = (
        config.evidence_weight * evidence_component
        + config.reason_weight * reason_component
        + config.fix_weight * fix_component
        + config.severity_weight * sev_component
        + config.category_weight * category_component
    )

    return clamp_confidence(raw, config.min_confidence, config.max_confidence)


def score_finding(
    finding: ScoredFinding,
    config: ScoringConfig = ScoringConfig(),
) -> ScoredFinding:
    """
    Normalize severity and confidence for a single finding.
    """
    severity = normalize_severity(finding.severity)

    if config.enable_keyword_escalation:
        inferred = infer_severity_from_text(
            summary=finding.summary,
            reasoning=finding.technical_reasoning,
            category=finding.category,
            default=severity,
        )
        if severity_score(inferred) > severity_score(severity):
            severity = inferred

    confidence = calibrate_confidence(
        replace(finding, severity=severity),
        config=config,
    )

    return replace(
        finding,
        severity=severity,
        confidence=confidence,
    )


def score_findings(
    findings: Sequence[ScoredFinding],
    config: ScoringConfig = ScoringConfig(),
) -> list[ScoredFinding]:
    """
    Score and rank findings deterministically.
    """
    scored = [score_finding(f, config=config) for f in findings]
    return rank_findings(scored, config=config)


def rank_findings(
    findings: Sequence[ScoredFinding],
    config: ScoringConfig = ScoringConfig(),
) -> list[ScoredFinding]:
    """
    Rank findings by severity then confidence, with deterministic tie-breakers.
    """
    if config.prefer_higher_confidence_on_equal_severity:
        return sorted(
            findings,
            key=lambda f: (
                -severity_score(f.severity),
                -clamp_confidence(f.confidence),
                str(f.category).lower(),
                str(f.summary).lower(),
            ),
        )
    return sorted(
        findings,
        key=lambda f: (
            -severity_score(f.severity),
            str(f.category).lower(),
            str(f.summary).lower(),
        ),
    )


def aggregate_risk_level(findings: Iterable[ScoredFinding]) -> str:
    """
    Compute overall risk level from a set of findings.
    """
    max_score = 0
    for f in findings:
        max_score = max(max_score, severity_score(f.severity))
    if max_score <= 1:
        return "low"
    if max_score == 2:
        return "medium"
    if max_score == 3:
        return "high"
    return "critical"


def score_from_mapping(
    data: Mapping[str, Any], config: ScoringConfig = ScoringConfig()
) -> ScoredFinding:
    """
    Convenience conversion from generic mapping payload.
    """
    finding = ScoredFinding(
        category=str(data.get("category", "unknown")),
        summary=str(data.get("summary", "")),
        technical_reasoning=str(data.get("technical_reasoning", "")),
        suggested_fix=str(data.get("suggested_fix", "")),
        severity=str(data.get("severity", "medium")),
        confidence=clamp_confidence(data.get("confidence", 0.5)),
        evidence_count=_extract_evidence_count(data.get("evidence")),
        metadata=dict(data.get("metadata", {}) or {}),
    )
    return score_finding(finding, config=config)


def _extract_evidence_count(evidence: Any) -> int:
    if evidence is None:
        return 0
    if isinstance(evidence, (list, tuple, set)):
        return len(evidence)
    return 1


def _nonempty_score(text: Any) -> float:
    s = str(text or "").strip()
    if not s:
        return 0.0
    if len(s) < 24:
        return 0.4
    if len(s) < 80:
        return 0.7
    return 1.0


def _category_specificity_score(category: Any) -> float:
    c = str(category or "").strip().lower()
    if not c:
        return 0.0
    generic = {"bug", "issue", "code_quality", "other", "unknown"}
    if c in generic:
        return 0.45
    return 1.0


__all__ = [
    "ScoredFinding",
    "ScoringConfig",
    "SEVERITY_ORDER",
    "SEVERITY_TO_SCORE",
    "normalize_severity",
    "severity_score",
    "infer_severity_from_text",
    "clamp_confidence",
    "calibrate_confidence",
    "score_finding",
    "score_findings",
    "rank_findings",
    "aggregate_risk_level",
    "score_from_mapping",
]
