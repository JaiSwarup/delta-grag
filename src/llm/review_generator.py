"""
Review generator for Delta-GRAG.

This module is responsible for:
1) Building a review prompt payload from PR metadata + linearized context.
2) Invoking an LLM client abstraction.
3) Parsing/normalizing structured findings from model output.

The implementation is provider-agnostic:
- You can plug in a local Transformers client, remote API client, or a mock.
- The LLM is expected to return JSON (or JSON-like text) that follows the
  finding schema described below.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

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
    technical_reasoning: str
    suggested_fix: str
    evidence: Tuple[Citation, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReviewResult:
    findings: Tuple[Finding, ...]
    raw_model_output: str
    parse_warnings: Tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewGeneratorConfig:
    model_name: str = "gpt-review-model"
    temperature: float = 0.1
    max_output_tokens: int = 2048
    require_json_schema: bool = True


# -----------------------------
# LLM client protocol
# -----------------------------


class LLMClient(Protocol):
    """
    Minimal client interface required by ReviewGenerator.

    Implementations should return plain text model output.
    """

    def generate(
        self,
        *,
        prompt: str,
        model_name: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str: ...


# -----------------------------
# Review generator
# -----------------------------


class ReviewGenerator:
    def __init__(
        self,
        llm_client: LLMClient,
        config: Optional[ReviewGeneratorConfig] = None,
    ) -> None:
        self._client = llm_client
        self._config = config or ReviewGeneratorConfig()

    def generate_review(
        self,
        *,
        pr_metadata: Mapping[str, Any],
        pr_diff: str,
        linearized_context: str,
        review_rubric: Optional[Sequence[str]] = None,
    ) -> ReviewResult:
        """
        Build prompt -> invoke LLM -> parse structured findings.
        """
        prompt = self._build_prompt(
            pr_metadata=pr_metadata,
            pr_diff=pr_diff,
            linearized_context=linearized_context,
            review_rubric=review_rubric,
        )

        raw = self._client.generate(
            prompt=prompt,
            model_name=self._config.model_name,
            temperature=self._config.temperature,
            max_output_tokens=self._config.max_output_tokens,
        )

        findings, warnings = parse_findings_from_model_output(raw)
        return ReviewResult(
            findings=tuple(findings),
            raw_model_output=raw,
            parse_warnings=tuple(warnings),
            metadata={
                "model_name": self._config.model_name,
                "temperature": self._config.temperature,
                "max_output_tokens": self._config.max_output_tokens,
                "prompt_chars": len(prompt),
                "context_chars": len(linearized_context),
                "finding_count": len(findings),
            },
        )

    def _build_prompt(
        self,
        *,
        pr_metadata: Mapping[str, Any],
        pr_diff: str,
        linearized_context: str,
        review_rubric: Optional[Sequence[str]],
    ) -> str:
        rubric = list(
            review_rubric
            or ["correctness", "regression risk", "api impact", "security/perf"]
        )
        rubric_text = "\n".join(f"- {r}" for r in rubric)

        schema_hint = ""
        if self._config.require_json_schema:
            schema_hint = (
                "\nReturn ONLY valid JSON with this shape:\n"
                "{\n"
                '  "findings": [\n'
                "    {\n"
                '      "category": "string",\n'
                '      "severity": "low|medium|high|critical",\n'
                '      "confidence": 0.0,\n'
                '      "summary": "string",\n'
                '      "technical_reasoning": "string",\n'
                '      "suggested_fix": "string",\n'
                '      "evidence": [\n'
                "        {\n"
                '          "node_id": "string",\n'
                '          "file_path": "optional string",\n'
                '          "start_line": 1,\n'
                '          "end_line": 2\n'
                "        }\n"
                "      ]\n"
                "    }\n"
                "  ]\n"
                "}\n"
            )

        title = str(pr_metadata.get("title", "Untitled PR"))
        pr_id = str(pr_metadata.get("pr_id", pr_metadata.get("id", "unknown")))
        description = str(pr_metadata.get("description", ""))

        return (
            "You are an expert code reviewer. Analyze the PR carefully and produce high-signal findings.\n\n"
            f"PR ID: {pr_id}\n"
            f"PR Title: {title}\n"
            f"PR Description:\n{description}\n\n"
            f"Review Rubric:\n{rubric_text}\n\n"
            f"PR Diff:\n{pr_diff}\n\n"
            f"Linearized Impact Context:\n{linearized_context}\n"
            f"{schema_hint}\n"
        )


# -----------------------------
# Parsing helpers
# -----------------------------


def parse_findings_from_model_output(
    raw_output: str,
) -> Tuple[List[Finding], List[str]]:
    """
    Parse model output as structured findings.

    Strategy:
    1) Try direct JSON parse.
    2) If that fails, try extracting first JSON object from text.
    3) Normalize fields and coerce types conservatively.
    """
    warnings: List[str] = []
    payload: Dict[str, Any] = {}

    text = (raw_output or "").strip()
    if not text:
        return [], ["Model returned empty output"]

    # 1) direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            payload = obj
        else:
            warnings.append("Top-level JSON is not an object")
    except Exception:
        # 2) try embedded JSON object
        maybe = _extract_json_object(text)
        if maybe is None:
            return [], ["Could not parse JSON from model output"]
        try:
            obj = json.loads(maybe)
            if isinstance(obj, dict):
                payload = obj
            else:
                warnings.append("Extracted JSON top-level is not an object")
        except Exception as exc:
            return [], [f"Failed to parse extracted JSON: {exc}"]

    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        return [], ["JSON payload missing list field 'findings'"]

    findings: List[Finding] = []
    for idx, item in enumerate(raw_findings):
        if not isinstance(item, Mapping):
            warnings.append(f"Skipping finding[{idx}] because it is not an object")
            continue
        f, item_warnings = _normalize_finding(item, idx)
        warnings.extend(item_warnings)
        if f is not None:
            findings.append(f)

    return findings, warnings


def _normalize_finding(
    item: Mapping[str, Any], idx: int
) -> Tuple[Optional[Finding], List[str]]:
    warnings: List[str] = []

    category = _as_nonempty_str(item.get("category"), default="unknown")
    severity = _normalize_severity(item.get("severity"))
    confidence = _normalize_confidence(item.get("confidence"))
    summary = _as_nonempty_str(item.get("summary"), default="No summary provided")
    reasoning = _as_nonempty_str(item.get("technical_reasoning"), default="")
    suggested_fix = _as_nonempty_str(item.get("suggested_fix"), default="")

    evidence_raw = item.get("evidence", [])
    evidence = _normalize_evidence(evidence_raw, idx, warnings)

    if not summary.strip():
        warnings.append(f"Skipping finding[{idx}] because summary is empty")
        return None, warnings

    return (
        Finding(
            category=category,
            severity=severity,
            confidence=confidence,
            summary=summary,
            technical_reasoning=reasoning,
            suggested_fix=suggested_fix,
            evidence=tuple(evidence),
        ),
        warnings,
    )


def _normalize_evidence(raw: Any, idx: int, warnings: List[str]) -> List[Citation]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        warnings.append(f"finding[{idx}].evidence is not a list; ignoring")
        return []

    out: List[Citation] = []
    for j, ev in enumerate(raw):
        if not isinstance(ev, Mapping):
            warnings.append(f"finding[{idx}].evidence[{j}] is not an object; skipping")
            continue

        node_id = _as_nonempty_str(ev.get("node_id"), default="")
        if not node_id:
            warnings.append(f"finding[{idx}].evidence[{j}] missing node_id; skipping")
            continue

        out.append(
            Citation(
                node_id=node_id,
                file_path=_as_optional_str(ev.get("file_path")),
                start_line=_as_optional_pos_int(ev.get("start_line")),
                end_line=_as_optional_pos_int(ev.get("end_line")),
            )
        )
    return out


def _extract_json_object(text: str) -> Optional[str]:
    """
    Extract first JSON object block from arbitrary text.
    """
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

    # fallback: fenced JSON block
    m = re.search(r"```json\s*(\{.*\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _normalize_severity(v: Any) -> str:
    s = _as_nonempty_str(v, default="medium").lower().strip()
    if s in {"low", "medium", "high", "critical"}:
        return s
    return "medium"


def _normalize_confidence(v: Any) -> float:
    try:
        f = float(v)
    except Exception:
        return 0.5
    if f < 0.0:
        return 0.0
    if f > 1.0:
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
        return i if i >= 1 else None
    except Exception:
        return None


__all__ = [
    "Citation",
    "Finding",
    "ReviewResult",
    "ReviewGeneratorConfig",
    "LLMClient",
    "ReviewGenerator",
    "parse_findings_from_model_output",
]
