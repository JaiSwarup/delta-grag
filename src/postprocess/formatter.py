"""
Formatter utilities for review output artifacts.

Provides:
- Markdown output for human-readable PR comments/reports.
- JSON output for machine-readable integrations.

Primary APIs:
- format_review_markdown(...)
- format_review_json(...)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class FormatConfig:
    include_header: bool = True
    include_summary: bool = True
    include_metadata: bool = True
    include_raw_model_output: bool = False
    json_indent: int = 2
    max_reasoning_chars: int = 2400
    max_fix_chars: int = 1200


def format_review_markdown(
    *,
    findings: Sequence[Any],
    metadata: Optional[Mapping[str, Any]] = None,
    parse_warnings: Optional[Sequence[str]] = None,
    raw_model_output: Optional[str] = None,
    config: Optional[FormatConfig] = None,
) -> str:
    """
    Format review findings into Markdown.

    Expected finding fields (object attrs or dict keys):
    - category
    - severity
    - confidence
    - summary
    - technical_reasoning
    - suggested_fix
    - evidence: list of evidence items with node_id/file_path/start_line/end_line
    """
    cfg = config or FormatConfig()
    meta = dict(metadata or {})
    warnings = list(parse_warnings or [])

    normalized_findings = [_normalize_finding(f) for f in findings]
    normalized_findings = _sort_findings(normalized_findings)

    lines: List[str] = []

    if cfg.include_header:
        lines.append("# PR Review Findings")
        lines.append("")
        lines.append(f"_Generated at {datetime.now(timezone.utc).isoformat()}_")
        lines.append("")

    if cfg.include_summary:
        sev_counts = _severity_counts(normalized_findings)
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- Total findings: **{len(normalized_findings)}**")
        lines.append(
            "- By severity: "
            f"critical={sev_counts['critical']}, "
            f"high={sev_counts['high']}, "
            f"medium={sev_counts['medium']}, "
            f"low={sev_counts['low']}"
        )
        if warnings:
            lines.append(f"- Parse warnings: **{len(warnings)}**")
        lines.append("")

    if warnings:
        lines.append("## Parser Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## Findings")
    lines.append("")

    if not normalized_findings:
        lines.append("- No findings.")
        lines.append("")
    else:
        for idx, f in enumerate(normalized_findings, start=1):
            lines.extend(_format_single_finding_md(idx, f, cfg))

    if cfg.include_metadata and meta:
        lines.append("## Metadata")
        lines.append("")
        for k in sorted(meta.keys()):
            lines.append(f"- **{k}**: `{_to_compact_str(meta[k])}`")
        lines.append("")

    if cfg.include_raw_model_output and raw_model_output is not None:
        lines.append("## Raw Model Output")
        lines.append("")
        lines.append("```text")
        lines.append(raw_model_output.rstrip())
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_review_json(
    *,
    findings: Sequence[Any],
    metadata: Optional[Mapping[str, Any]] = None,
    parse_warnings: Optional[Sequence[str]] = None,
    raw_model_output: Optional[str] = None,
    config: Optional[FormatConfig] = None,
) -> str:
    """
    Format review findings into JSON artifact.
    """
    cfg = config or FormatConfig()
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "findings": [_normalize_finding(f) for f in findings],
        "summary": {},
        "parse_warnings": list(parse_warnings or []),
        "metadata": dict(metadata or {}),
    }

    counts = _severity_counts(payload["findings"])
    payload["summary"] = {
        "finding_count": len(payload["findings"]),
        "severity_counts": counts,
    }

    if raw_model_output is not None:
        payload["raw_model_output"] = raw_model_output

    return json.dumps(payload, indent=cfg.json_indent, ensure_ascii=False)


def _format_single_finding_md(
    idx: int, f: Mapping[str, Any], cfg: FormatConfig
) -> List[str]:
    sev = str(f.get("severity", "medium")).lower()
    conf = f.get("confidence", 0.5)
    category = f.get("category", "unknown")
    summary = f.get("summary", "No summary provided")
    reasoning = _truncate(
        str(f.get("technical_reasoning", "")).strip(), cfg.max_reasoning_chars
    )
    fix = _truncate(str(f.get("suggested_fix", "")).strip(), cfg.max_fix_chars)
    evidence = f.get("evidence", []) or []

    icon = {
        "critical": "🛑",
        "high": "🔴",
        "medium": "🟠",
        "low": "🟢",
    }.get(sev, "⚪")

    out = [
        f"### {idx}. {icon} {summary}",
        "",
        f"- **Severity**: `{sev}`",
        f"- **Category**: `{category}`",
        f"- **Confidence**: `{conf}`",
    ]

    if reasoning:
        out.append(f"- **Technical reasoning**: {reasoning}")

    if fix:
        out.append(f"- **Suggested fix**: {fix}")

    out.append("- **Evidence**:")
    if not evidence:
        out.append("  - (none)")
    else:
        for ev in evidence:
            node_id = ev.get("node_id", "unknown")
            file_path = ev.get("file_path")
            s = ev.get("start_line")
            e = ev.get("end_line")
            loc = _format_location(file_path, s, e)
            out.append(f"  - node=`{node_id}`{loc}")

    out.append("")
    return out


def _format_location(file_path: Any, start_line: Any, end_line: Any) -> str:
    if not file_path:
        return ""
    if start_line is None:
        return f", file=`{file_path}`"
    if end_line is None:
        return f", file=`{file_path}:{start_line}`"
    return f", file=`{file_path}:{start_line}-{end_line}`"


def _normalize_finding(obj: Any) -> Dict[str, Any]:
    if is_dataclass(obj) and not isinstance(obj, type):
        raw = asdict(obj)
    elif isinstance(obj, Mapping):
        raw = dict(obj)
    else:
        raw = {
            "summary": str(obj),
            "category": "unknown",
            "severity": "medium",
            "confidence": 0.5,
            "technical_reasoning": "",
            "suggested_fix": "",
            "evidence": [],
        }

    evidence_raw = raw.get("evidence", []) or []
    evidence = [_normalize_evidence_item(e) for e in evidence_raw if e is not None]

    return {
        "category": str(raw.get("category", "unknown")),
        "severity": _normalize_severity(raw.get("severity", "medium")),
        "confidence": _normalize_confidence(raw.get("confidence", 0.5)),
        "summary": str(raw.get("summary", "No summary provided")),
        "technical_reasoning": str(raw.get("technical_reasoning", "")),
        "suggested_fix": str(raw.get("suggested_fix", "")),
        "evidence": evidence,
    }


def _normalize_evidence_item(ev: Any) -> Dict[str, Any]:
    if is_dataclass(ev) and not isinstance(ev, type):
        d = asdict(ev)
    elif isinstance(ev, Mapping):
        d = dict(ev)
    else:
        d = {"node_id": str(ev)}

    return {
        "node_id": str(d.get("node_id", "unknown")),
        "file_path": d.get("file_path"),
        "start_line": _to_pos_int_or_none(d.get("start_line")),
        "end_line": _to_pos_int_or_none(d.get("end_line")),
    }


def _severity_counts(findings: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = _normalize_severity(f.get("severity", "medium"))
        counts[sev] += 1
    return counts


def _sort_findings(findings: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        findings,
        key=lambda f: (
            rank.get(_normalize_severity(f.get("severity", "medium")), 9),
            -_normalize_confidence(f.get("confidence", 0.5)),
            str(f.get("category", "")),
            str(f.get("summary", "")),
        ),
    )


def _normalize_severity(v: Any) -> str:
    s = str(v).strip().lower()
    return s if s in {"critical", "high", "medium", "low"} else "medium"


def _normalize_confidence(v: Any) -> float:
    try:
        x = float(v)
    except Exception:
        return 0.5
    return max(0.0, min(1.0, x))


def _to_pos_int_or_none(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        i = int(v)
        return i if i >= 1 else None
    except Exception:
        return None


def _truncate(text: str, max_chars: int) -> str:
    if max_chars < 1:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _to_compact_str(v: Any) -> str:
    if isinstance(v, (dict, list, tuple)):
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)
    return str(v)


def findings_to_markdown(findings: Sequence[Any], **kwargs: Any) -> str:
    """
    Compatibility wrapper accepting positional findings.
    """
    return format_review_markdown(findings=findings, **kwargs)


def findings_to_json(findings: Sequence[Any], **kwargs: Any) -> str:
    """
    Compatibility wrapper accepting positional findings.
    """
    return format_review_json(findings=findings, **kwargs)


__all__ = [
    "FormatConfig",
    "format_review_markdown",
    "format_review_json",
    "findings_to_markdown",
    "findings_to_json",
]
