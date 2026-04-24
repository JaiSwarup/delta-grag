"""
File-context LLM review baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.ast_extractor import FunctionNode
from src.file_indexer import FileIndex
from src.ingestion.diff_parser import DiffParseResult
from src.llm_caller import AsyncLLMProvider, LLMCallResult, LLMCallerConfig, call_llm_json
from src.token_budget import estimate_token_count


@dataclass(frozen=True)
class FileContextResult:
    included_files: list[Path]
    truncated_files: list[Path]
    prompt: str
    total_tokens: int
    review: LLMCallResult
    metadata: Mapping[str, Any] = field(default_factory=dict)


async def file_context_review(
    *,
    diff: DiffParseResult,
    diff_text: str,
    file_index: FileIndex,
    function_nodes: Sequence[FunctionNode],
    pr_metadata: Mapping[str, Any],
    provider: AsyncLLMProvider,
    config: LLMCallerConfig,
    token_budget: int = 8_000,
) -> FileContextResult:
    if token_budget < 1:
        raise ValueError("token_budget must be >= 1")

    context, included_files, truncated_files = build_file_context(
        diff=diff,
        file_index=file_index,
        function_nodes=function_nodes,
        token_budget=token_budget,
    )
    prompt = build_file_context_prompt(
        diff_text=diff_text,
        file_context=context,
        pr_metadata=pr_metadata,
    )
    review = await call_llm_json(
        prompt=prompt,
        provider=provider,
        config=config,
    )
    return FileContextResult(
        included_files=included_files,
        truncated_files=truncated_files,
        prompt=prompt,
        total_tokens=estimate_token_count(prompt),
        review=review,
        metadata={
            "context_tokens": estimate_token_count(context),
            "changed_file_count": len(diff.changed_files),
        },
    )


def build_file_context(
    *,
    diff: DiffParseResult,
    file_index: FileIndex,
    function_nodes: Sequence[FunctionNode],
    token_budget: int,
) -> tuple[str, list[Path], list[Path]]:
    changed_lines = diff.changed_lines_by_file
    candidates = []
    for rel_path, lines in changed_lines.items():
        metadata = file_index.files.get(rel_path)
        if metadata is None:
            continue
        density = len(lines) / max(metadata.loc, 1)
        candidates.append((rel_path, density, metadata.loc))
    candidates.sort(key=lambda item: (-item[1], item[0]))

    chunks: list[str] = []
    included_files: list[Path] = []
    truncated_files: list[Path] = []
    used_tokens = 0

    for rel_path, _, _ in candidates:
        abs_path = file_index.root_path / rel_path
        full_text = abs_path.read_text(encoding="utf-8", errors="replace")
        full_chunk = _format_file_chunk(rel_path, full_text)
        full_tokens = estimate_token_count(full_chunk)
        if used_tokens + full_tokens <= token_budget:
            chunks.append(full_chunk)
            used_tokens += full_tokens
            included_files.append(Path(rel_path))
            continue

        function_chunk = _format_modified_functions_chunk(
            rel_path=rel_path,
            changed_lines=set(changed_lines.get(rel_path, ())),
            function_nodes=function_nodes,
        )
        function_tokens = estimate_token_count(function_chunk)
        if function_chunk and used_tokens + function_tokens <= token_budget:
            chunks.append(function_chunk)
            used_tokens += function_tokens
            included_files.append(Path(rel_path))
            truncated_files.append(Path(rel_path))
        else:
            truncated_files.append(Path(rel_path))

    return "\n\n".join(chunks), included_files, truncated_files


def build_file_context_prompt(
    *,
    diff_text: str,
    file_context: str,
    pr_metadata: Mapping[str, Any],
) -> str:
    title = str(pr_metadata.get("title", "Untitled PR"))
    pr_id = str(pr_metadata.get("pr_id", pr_metadata.get("id", "unknown")))
    description = str(pr_metadata.get("description", ""))
    context_text = file_context.strip() if file_context.strip() else "(no file context)"
    return (
        "You are an expert code reviewer. Review the PR using the diff and file-scoped context only.\n"
        "Return ONLY valid JSON matching this schema:\n"
        '{"findings":[{"category":"string","severity":"low|medium|high|critical",'
        '"confidence":0.0,"summary":"string","technical_reasoning":"string",'
        '"suggested_fix":"string","evidence":[{"node_id":"string","file_path":"string",'
        '"start_line":1,"end_line":1}]}],"overall_risk":"low|medium|high|critical"}\n\n'
        f"PR ID: {pr_id}\n"
        f"PR Title: {title}\n"
        f"PR Description:\n{description}\n\n"
        f"PR Diff:\n```diff\n{diff_text.strip()}\n```\n\n"
        f"[FILE CONTEXT]\n{context_text}\n"
    )


def _format_file_chunk(rel_path: str, text: str) -> str:
    return f"### FILE: {rel_path}\n```python\n{text.rstrip()}\n```"


def _format_modified_functions_chunk(
    *,
    rel_path: str,
    changed_lines: set[int],
    function_nodes: Sequence[FunctionNode],
) -> str:
    functions = [
        function
        for function in function_nodes
        if function.file_path.as_posix().endswith(rel_path)
        and any(function.start_line <= line <= function.end_line for line in changed_lines)
    ]
    if not functions:
        return ""
    chunks = [f"### FILE: {rel_path} (modified functions only)"]
    for function in sorted(functions, key=lambda fn: (fn.start_line, fn.fqn)):
        chunks.append(
            f"#### FUNCTION: {function.fqn} lines {function.start_line}-{function.end_line}\n"
            f"```python\n{function.source_code.rstrip()}\n```"
        )
    return "\n".join(chunks)


__all__ = [
    "FileContextResult",
    "build_file_context",
    "build_file_context_prompt",
    "file_context_review",
]
