"""
Postprocessing review types and normalization parser.

This module provides:
- Typed models for normalized review findings.
- Severity/confidence coercion helpers.
- Citation normalization.
- Parser utilities to convert raw model output into strongly-typed findings.

It is intentionally provider-agnostic and supports:
1) dict/list payloads,
2) JSON strings,
3) markdown-fenced JSON text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Citation:
    node_id: str
    file_path: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclass(frozen=True)
class Finding:
    category: str
    severity: Severity
    confidence: float
    summary: str
    technical_reasoning: str = ""
    suggested_fix: str = ""
    evidence: tuple[Citation, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NormalizedReview:
    findings: tuple[Finding, ...]
    overall_risk: Severity = Severity.MEDIUM
    warnings: tuple[str, ...] = field(default_factory=tuple)
    raw_payload: Any = None


def normalize_review_output(raw: Any) -> NormalizedReview:
    """
    Normalize raw review output into typed findings.

    Accepted raw forms:
    - dict payload containing "findings"
    - JSON string payload
    - markdown text containing fenced JSON or embedded JSON object
    """
    warnings: list[str] = []
    payload = _coerce_payload(raw, warnings)

    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        warnings.append("Missing or invalid 'findings' list; defaulting to empty list.")
        raw_findings = []

    findings: list[Finding] = []
    for idx, item in enumerate(raw_findings):
        f = _normalize_finding(item, idx, warnings)
        if f is not None:
            findings.append(f)

    overall_risk = _normalize_severity(
        payload.get("overall_risk"), default=Severity.MEDIUM
    )

    return NormalizedReview(
        findings=tuple(findings),
        overall_risk=overall_risk,
        warnings=tuple(warnings),
        raw_payload=raw,
    )


def findings_to_markdown(findings: Sequence[Finding]) -> str:
    """
    Convert normalized findings to a concise markdown summary.
    """
    if not findings:
        return "## Findings\n\n- No findings.\n"

    lines: list[str] = ["## Findings", ""]
    for i, f in enumerate(findings, start=1):
        lines.append(f"### {i}. [{f.severity.value.upper()}] {f.summary}")
        lines.append(f"- Category: `{f.category}`")
        lines.append(f"- Confidence: `{f.confidence:.2f}`")
        if f.technical_reasoning:
            lines.append(f"- Reasoning: {f.technical_reasoning}")
        if f.suggested_fix:
            lines.append(f"- Suggested fix: {f.suggested_fix}")

        if f.evidence:
            lines.append("- Evidence:")
            for ev in f.evidence:
                span = _format_span(ev.start_line, ev.end_line)
                file_part = ev.file_path or "unknown_file"
                lines.append(f"  - `{ev.node_id}` @ `{file_part}` {span}".rstrip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _normalize_finding(item: Any, idx: int, warnings: list[str]) -> Optional[Finding]:
    if not isinstance(item, Mapping):
        warnings.append(f"Skipping finding[{idx}]: expected object.")
        return None

    summary = _as_nonempty_str(item.get("summary"))
    if not summary:
        warnings.append(f"Skipping finding[{idx}]: missing summary.")
        return None

    category = _as_nonempty_str(item.get("category"), default="unknown")
    severity = _normalize_severity(item.get("severity"), default=Severity.MEDIUM)
    confidence = _normalize_confidence(item.get("confidence"), default=0.5)
    technical_reasoning = _as_nonempty_str(item.get("technical_reasoning"), default="")
    suggested_fix = _as_nonempty_str(item.get("suggested_fix"), default="")
    evidence = _normalize_evidence(item.get("evidence"), idx, warnings)

    return Finding(
        category=category,
        severity=severity,
        confidence=confidence,
        summary=summary,
        technical_reasoning=technical_reasoning,
        suggested_fix=suggested_fix,
        evidence=tuple(evidence),
    )


def _normalize_evidence(raw: Any, idx: int, warnings: list[str]) -> list[Citation]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        warnings.append(f"finding[{idx}].evidence is not a list; ignoring.")
        return []

    out: list[Citation] = []
    for j, ev in enumerate(raw):
        if not isinstance(ev, Mapping):
            warnings.append(f"finding[{idx}].evidence[{j}] is not an object; skipping.")
            continue

        node_id = _as_nonempty_str(ev.get("node_id"))
        if not node_id:
            warnings.append(f"finding[{idx}].evidence[{j}] missing node_id; skipping.")
            continue

        start_line = _as_optional_pos_int(ev.get("start_line"))
        end_line = _as_optional_pos_int(ev.get("end_line"))

        # Backward-compat: allow {"file": "...", "lines": "10-12"} shape.
        if start_line is None and end_line is None:
            legacy_start, legacy_end = _parse_legacy_lines(ev.get("lines"))
            if legacy_start is not None or legacy_end is not None:
                start_line, end_line = legacy_start, legacy_end

        # If only one line is present, mirror it for a stable range.
        if start_line is not None and end_line is None:
            end_line = start_line
        if end_line is not None and start_line is None:
            start_line = end_line
        if start_line is not None and end_line is not None and end_line < start_line:
            start_line, end_line = end_line, start_line

        out.append(
            Citation(
                node_id=node_id,
                file_path=_as_optional_str(ev.get("file_path"))
                or _as_optional_str(ev.get("file")),
                start_line=start_line,
                end_line=end_line,
            )
        )

    return out


def _coerce_payload(raw: Any, warnings: list[str]) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            warnings.append("Empty model output; using empty payload.")
            return {}

        # Direct JSON parse.
        try:
            obj = json.loads(text)
            if isinstance(obj, Mapping):
                return dict(obj)
        except Exception:
            pass

        # Fenced JSON extraction.
        fenced = _extract_fenced_json(text)
        if fenced is not None:
            try:
                obj = json.loads(fenced)
                if isinstance(obj, Mapping):
                    return dict(obj)
            except Exception:
                warnings.append("Failed to parse fenced JSON block.")

        # Embedded object extraction.
        embedded = _extract_json_object(text)
        if embedded is not None:
            try:
                obj = json.loads(embedded)
                if isinstance(obj, Mapping):
                    return dict(obj)
            except Exception:
                warnings.append("Failed to parse embedded JSON object.")

        warnings.append("Could not parse JSON payload from string output.")
        return {}

    warnings.append("Unsupported raw payload type; expected mapping or string.")
    return {}


def _extract_fenced_json(text: str) -> Optional[str]:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1)
    return None


def _extract_json_object(text: str) -> Optional[str]:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_str = False
    esc = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def _normalize_severity(v: Any, default: Severity) -> Severity:
    s = _as_nonempty_str(v, default=default.value).strip().lower()
    if s in {"low", "l"}:
        return Severity.LOW
    if s in {"medium", "med", "m"}:
        return Severity.MEDIUM
    if s in {"high", "h"}:
        return Severity.HIGH
    if s in {"critical", "crit", "c"}:
        return Severity.CRITICAL
    return default


def _normalize_confidence(v: Any, default: float) -> float:
    try:
        f = float(v)
    except Exception:
        return default
    if f < 0:
        return 0.0
    if f > 1:
        return 1.0
    return f


def _as_nonempty_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    s = str(v)
    return s if s.strip() else default


def _as_optional_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _as_optional_pos_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        i = int(v)
    except Exception:
        return None
    return i if i >= 1 else None


def _parse_legacy_lines(v: Any) -> tuple[Optional[int], Optional[int]]:
    if v is None:
        return None, None

    s = str(v).strip()
    if not s:
        return None, None

    m = re.match(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$", s)
    if not m:
        return None, None

    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) is not None else start
    return start, end


def _format_span(start_line: Optional[int], end_line: Optional[int]) -> str:
    if start_line is None and end_line is None:
        return ""
    if start_line is None:
        return f"lines:{end_line}"
    if end_line is None:
        return f"lines:{start_line}"
    if start_line == end_line:
        return f"lines:{start_line}"
    return f"lines:{start_line}-{end_line}"


__all__ = [
    "Severity",
    "Citation",
    "Finding",
    "NormalizedReview",
    "normalize_review_output",
    "findings_to_markdown",
]
