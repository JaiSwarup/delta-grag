"""
Finding deduplication utilities.

This module provides deterministic deduplication for review findings by building
a normalized semantic key from core finding attributes and evidence citations.

Design goals
------------
- Deterministic output ordering.
- Conservative merge behavior (keep the highest-signal variant).
- Stable semantic keys robust to minor phrasing/formatting differences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# -----------------------------
# Data models
# -----------------------------


@dataclass(frozen=True)
class Citation:
    node_id: str
    file_path: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclass(frozen=True)
class Finding:
    category: str
    severity: str
    confidence: float
    summary: str
    technical_reasoning: str = ""
    suggested_fix: str = ""
    evidence: Tuple[Citation, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DeduperConfig:
    """
    Configuration for deduplication behavior.

    - include_suggested_fix_in_key:
        If True, different suggested fixes can split otherwise-similar findings.
    - min_summary_token_len:
        Very short summaries are normalized but can be less reliable semantically.
    """

    include_suggested_fix_in_key: bool = False
    min_summary_token_len: int = 3


# -----------------------------
# Public API
# -----------------------------


def dedupe_findings(
    findings: Sequence[Finding],
    config: Optional[DeduperConfig] = None,
) -> List[Finding]:
    """
    Deduplicate findings using normalized semantic keys.

    Rules
    -----
    1) Build semantic key from:
       - normalized category
       - normalized summary signature
       - normalized evidence signature
       - (optional) suggested_fix signature

    2) For conflicting duplicates with same key, keep the "best" candidate by:
       - higher severity
       - higher confidence
       - richer evidence count
       - longer technical reasoning
       - first-seen tie break (stable)

    Returns
    -------
    List[Finding]
        Deduplicated findings in deterministic order.
    """
    cfg = config or DeduperConfig()
    if not findings:
        return []

    selected: Dict[str, Finding] = {}
    order: List[str] = []

    for f in findings:
        key = semantic_key_for_finding(f, cfg)

        if key not in selected:
            selected[key] = _normalized_finding(f)
            order.append(key)
            continue

        incumbent = selected[key]
        challenger = _normalized_finding(f)
        selected[key] = _pick_better(incumbent, challenger)

    return [selected[k] for k in order]


def semantic_key_for_finding(
    finding: Finding,
    config: Optional[DeduperConfig] = None,
) -> str:
    """
    Compute normalized semantic key for a finding.
    """
    cfg = config or DeduperConfig()

    category = _normalize_text(finding.category)
    summary_sig = _summary_signature(finding.summary, cfg.min_summary_token_len)
    evidence_sig = _evidence_signature(finding.evidence)

    parts = [category, summary_sig, evidence_sig]

    if cfg.include_suggested_fix_in_key:
        parts.append(_normalize_text(finding.suggested_fix))

    return "|".join(parts)


# -----------------------------
# Internal helpers
# -----------------------------


def _normalized_finding(f: Finding) -> Finding:
    return Finding(
        category=_normalize_text(f.category),
        severity=_normalize_severity(f.severity),
        confidence=_clamp_confidence(f.confidence),
        summary=_normalize_whitespace(f.summary).strip(),
        technical_reasoning=_normalize_whitespace(f.technical_reasoning).strip(),
        suggested_fix=_normalize_whitespace(f.suggested_fix).strip(),
        evidence=_normalize_evidence_tuple(f.evidence),
    )


def _pick_better(a: Finding, b: Finding) -> Finding:
    """
    Pick the better representative among duplicate findings.
    """
    rank_a = _finding_rank(a)
    rank_b = _finding_rank(b)

    if rank_b > rank_a:
        return b
    return a


def _finding_rank(f: Finding) -> Tuple[int, float, int, int]:
    """
    Ranking tuple for duplicate resolution:
    (severity_rank, confidence, evidence_count, reasoning_len)
    """
    return (
        _severity_rank(f.severity),
        _clamp_confidence(f.confidence),
        len(f.evidence),
        len(f.technical_reasoning or ""),
    )


def _severity_rank(sev: str) -> int:
    m = {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }
    return m.get(_normalize_severity(sev), 0)


def _normalize_severity(sev: str) -> str:
    s = _normalize_text(sev)
    if s in {"critical", "high", "medium", "low"}:
        return s
    if s in {"med", "moderate"}:
        return "medium"
    return "medium"


def _summary_signature(summary: str, min_len: int) -> str:
    """
    Normalize summary into a compact semantic signature.

    Strategy:
    - lowercase
    - strip punctuation/noise
    - collapse whitespace
    - drop tiny tokens
    - preserve token order
    """
    norm = _normalize_text(summary)
    tokens = [t for t in norm.split() if len(t) >= max(1, min_len)]
    if not tokens:
        return norm
    return " ".join(tokens)


def _evidence_signature(evidence: Iterable[Citation]) -> str:
    """
    Build deterministic evidence signature from normalized citations.
    """
    normalized = _normalize_evidence_tuple(tuple(evidence))
    chunks = []
    for c in normalized:
        fp = c.file_path or ""
        sl = str(c.start_line) if c.start_line is not None else ""
        el = str(c.end_line) if c.end_line is not None else ""
        chunks.append(f"{c.node_id}@{fp}:{sl}-{el}")
    return ";".join(chunks)


def _normalize_evidence_tuple(evidence: Tuple[Citation, ...]) -> Tuple[Citation, ...]:
    dedup_map: Dict[Tuple[str, str, Optional[int], Optional[int]], Citation] = {}

    for c in evidence:
        node_id = _normalize_text(c.node_id)
        file_path = _normalize_path(c.file_path) if c.file_path else None
        start_line = _pos_int_or_none(c.start_line)
        end_line = _pos_int_or_none(c.end_line)

        if start_line is not None and end_line is not None and end_line < start_line:
            start_line, end_line = end_line, start_line

        key = (node_id, file_path or "", start_line, end_line)
        dedup_map[key] = Citation(
            node_id=node_id,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
        )

    sorted_keys = sorted(
        dedup_map.keys(),
        key=lambda k: (k[1], k[0], k[2] or 0, k[3] or 0),
    )
    return tuple(dedup_map[k] for k in sorted_keys)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).lower()
    s = _normalize_whitespace(s)
    s = re.sub(r"[^\w\s:/.-]+", " ", s)
    s = _normalize_whitespace(s)
    return s.strip()


def _normalize_whitespace(s: str) -> str:
    return " ".join((s or "").split())


def _normalize_path(path: str) -> str:
    p = (path or "").strip().replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    return p.lower()


def _clamp_confidence(v: Any) -> float:
    try:
        f = float(v)
    except Exception:
        return 0.5
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _pos_int_or_none(v: Any) -> Optional[int]:
    try:
        i = int(v)
    except Exception:
        return None
    return i if i >= 1 else None


__all__ = [
    "Citation",
    "Finding",
    "DeduperConfig",
    "semantic_key_for_finding",
    "dedupe_findings",
]
