"""
BFS-based subgraph linearizer for PR-aware LLM code review prompts.

Serializes a NetworkX impact subgraph into Markdown-friendly structured text:
- MODIFIED: anchor blocks
- CALLERS (depth k): upstream context in BFS order
- CALLEES (depth m): downstream context in BFS order
- PR DIFF HUNK: raw diff payload

Function:
    linearize_subgraph(G_prime, pr_diff, anchors, ...)

Design goals:
- Deterministic ordering.
- Preserve locality by BFS layering from anchors.
- Budget-aware packing with a strict character ceiling.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import networkx as nx

# Conservative ceiling for "< 128k tokens" proxy.
# (Token->char varies by model; this keeps a strict char guard.)
DEFAULT_MAX_CHARS = 500_000


def linearize_subgraph(
    G_prime: nx.DiGraph,
    pr_diff: str,
    anchors: Iterable[str],
    max_chars: int = DEFAULT_MAX_CHARS,
    include_code: bool = True,
    include_diff_section: bool = True,
    repo_root: Optional[str] = None,
    code_attr_candidates: Sequence[str] = ("code", "source", "snippet", "text"),
    file_attr: str = "file",
    start_line_attr: str = "start_line",
    end_line_attr: str = "end_line",
    qualified_name_attr: str = "qualified_name",
    name_attr: str = "name",
    encoding: str = "utf-8",
) -> str:
    """
    Convert impact subgraph into structured prompt text for LLM code review.

    Parameters
    ----------
    G_prime:
        Impact subgraph (caller -> callee).
    pr_diff:
        Raw PR diff hunk text to include.
    anchors:
        Anchor node IDs (modified functions).
    max_chars:
        Hard character budget for the final output.
    include_code:
        Include function code blocks when available.
    code_attr_candidates:
        Node attribute names checked in order for inline source text.
    file_attr/start_line_attr/end_line_attr/qualified_name_attr/name_attr:
        Node attribute keys used for formatting and fallback code loading.
    encoding:
        File decode encoding for code fallback reads.

    Returns
    -------
    str
        Markdown-friendly serialized prompt context.
    """
    if max_chars < 256:
        raise ValueError("max_chars is too small to produce meaningful output")
    if not isinstance(pr_diff, str):
        raise TypeError("pr_diff must be a string")

    anchor_list = _sorted_present_nodes(G_prime, anchors)
    if not anchor_list:
        # Still emit diff and explicit empty sections for contract stability.
        anchor_list = []

    # Region sets with deterministic BFS locality:
    # - callers: predecessors traversal from anchors
    # - callees: successors traversal from anchors
    callers_order, callers_depth = _multi_source_bfs_directional(
        G_prime, anchor_list, reverse=True
    )
    callees_order, callees_depth = _multi_source_bfs_directional(
        G_prime, anchor_list, reverse=False
    )

    anchor_set = set(anchor_list)
    callers_order = [n for n in callers_order if n not in anchor_set]
    callees_order = [n for n in callees_order if n not in anchor_set]

    # Remove overlap from callees to avoid duplicate blocks across sections.
    callers_set = set(callers_order)
    callees_order = [n for n in callees_order if n not in callers_set]

    builders: List[str] = []

    truncated_due_to_budget = False

    def append_with_budget(chunk: str) -> bool:
        nonlocal truncated_due_to_budget
        current = sum(len(x) for x in builders)
        if current + len(chunk) <= max_chars:
            builders.append(chunk)
            return True
        truncated_due_to_budget = True
        return False

    # Header
    append_with_budget("# Delta-GRAG Linearized Context\n\n")

    # Diff first for immediate review framing (optional, to avoid duplication in prompts).
    if include_diff_section:
        diff_section = _format_diff_section(pr_diff)
        if not append_with_budget(diff_section):
            # If diff alone is too large, include truncated diff and stop.
            truncated = _truncate_to_budget(
                diff_section, max_chars - sum(len(x) for x in builders)
            )
            builders.append(truncated)
            return "".join(builders)

    # MODIFIED section
    if not append_with_budget("## MODIFIED\n\n"):
        return "".join(builders)

    if anchor_list:
        for node_id in anchor_list:
            block = _format_node_block(
                G_prime,
                node_id,
                section_label="MODIFIED",
                depth=None,
                include_code=include_code,
                code_attr_candidates=code_attr_candidates,
                file_attr=file_attr,
                start_line_attr=start_line_attr,
                end_line_attr=end_line_attr,
                qualified_name_attr=qualified_name_attr,
                name_attr=name_attr,
                encoding=encoding,
                repo_root=repo_root,
            )
            if not append_with_budget(block):
                break
    else:
        append_with_budget("- None (no anchors found in subgraph)\n\n")

    # CALLERS section
    if not append_with_budget("## CALLERS (depth k)\n\n"):
        return "".join(builders)

    if callers_order:
        for node_id in callers_order:
            block = _format_node_block(
                G_prime,
                node_id,
                section_label="CALLERS",
                depth=callers_depth.get(node_id),
                include_code=include_code,
                code_attr_candidates=code_attr_candidates,
                file_attr=file_attr,
                start_line_attr=start_line_attr,
                end_line_attr=end_line_attr,
                qualified_name_attr=qualified_name_attr,
                name_attr=name_attr,
                encoding=encoding,
                repo_root=repo_root,
            )
            if not append_with_budget(block):
                break
    else:
        append_with_budget("- None\n\n")

    # CALLEES section
    if not append_with_budget("## CALLEES (depth m)\n\n"):
        return "".join(builders)

    if callees_order:
        for node_id in callees_order:
            block = _format_node_block(
                G_prime,
                node_id,
                section_label="CALLEES",
                depth=callees_depth.get(node_id),
                include_code=include_code,
                code_attr_candidates=code_attr_candidates,
                file_attr=file_attr,
                start_line_attr=start_line_attr,
                end_line_attr=end_line_attr,
                qualified_name_attr=qualified_name_attr,
                name_attr=name_attr,
                encoding=encoding,
                repo_root=repo_root,
            )
            if not append_with_budget(block):
                break
    else:
        append_with_budget("- None\n\n")

    # Explicit budget note if we clipped content.
    final_text = "".join(builders)
    if len(final_text) >= max_chars:
        final_text = _truncate_to_budget(final_text, max_chars)
    elif truncated_due_to_budget:
        final_text = _append_truncation_marker_if_possible(final_text, max_chars)

    return final_text


# --------------------------
# Formatting helpers
# --------------------------


def _format_diff_section(pr_diff: str) -> str:
    safe_diff = pr_diff.strip() if pr_diff else "(empty diff)"
    return f"## PR DIFF HUNK\n\n```diff\n{safe_diff}\n```\n\n"


def _format_node_block(
    G: nx.DiGraph,
    node_id: str,
    section_label: str,
    depth: Optional[int],
    include_code: bool,
    code_attr_candidates: Sequence[str],
    file_attr: str,
    start_line_attr: str,
    end_line_attr: str,
    qualified_name_attr: str,
    name_attr: str,
    encoding: str,
    repo_root: Optional[str],
) -> str:
    data: Mapping[str, object] = G.nodes[node_id]

    fn_name = (
        _string(data.get(qualified_name_attr))
        or _string(data.get(name_attr))
        or str(node_id)
    )
    file_path = _string(data.get(file_attr)) or "unknown_file.py"
    start_line = _int_or_none(data.get(start_line_attr))
    end_line = _int_or_none(data.get(end_line_attr))

    depth_text = f", depth={depth}" if depth is not None else ""
    header = f"### {section_label}: `{fn_name}` `{file_path}`{depth_text}\n\n"

    code_text = ""
    if include_code:
        code_text = _extract_code(
            data=data,
            code_attr_candidates=code_attr_candidates,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            encoding=encoding,
            repo_root=repo_root,
        )

    if not code_text:
        code_text = "# Code unavailable"

    meta_line = f"- node_id: `{node_id}`\n"
    line_span = ""
    if start_line is not None:
        line_span = f"- lines: `{start_line}`-`{end_line if end_line is not None else start_line}`\n"

    block = (
        header
        + meta_line
        + line_span
        + "\n"
        + "```python\n"
        + f"{code_text.rstrip()}\n"
        + "```\n\n"
    )
    return block


# --------------------------
# BFS + ordering helpers
# --------------------------


def _multi_source_bfs_directional(
    G: nx.DiGraph,
    anchors: Sequence[str],
    reverse: bool,
) -> Tuple[List[str], Dict[str, int]]:
    """
    Deterministic multi-source BFS.

    Returns
    -------
    (order, depth_map):
        order includes anchors first (sorted deterministic),
        then discovered nodes in BFS-layered order.
    """
    if not anchors:
        return [], {}

    q: deque[Tuple[str, int]] = deque()
    visited: Set[str] = set()
    depth_map: Dict[str, int] = {}

    sorted_anchors = sorted(anchors, key=str)
    order: List[str] = []

    for a in sorted_anchors:
        if a not in G or a in visited:
            continue
        visited.add(a)
        depth_map[a] = 0
        q.append((a, 0))
        order.append(a)

    while q:
        node, d = q.popleft()
        neigh = G.predecessors(node) if reverse else G.successors(node)
        for nb in sorted(neigh, key=str):
            if nb in visited:
                continue
            visited.add(nb)
            depth_map[nb] = d + 1
            q.append((nb, d + 1))
            order.append(nb)

    return order, depth_map


def _sorted_present_nodes(G: nx.DiGraph, nodes: Iterable[str]) -> List[str]:
    present = [n for n in nodes if n in G]
    return sorted(set(present), key=str)


# --------------------------
# Code extraction + safety
# --------------------------


def _extract_code(
    data: Mapping[str, object],
    code_attr_candidates: Sequence[str],
    file_path: str,
    start_line: Optional[int],
    end_line: Optional[int],
    encoding: str,
    repo_root: Optional[str],
) -> str:
    # 1) Prefer inline code attributes on node
    for key in code_attr_candidates:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value

    # 2) Fallback: read from file with optional line slicing
    try:
        p = _resolve_source_path(file_path=file_path, repo_root=repo_root)
        if not p.exists() or not p.is_file():
            return ""
        raw = p.read_text(encoding=encoding, errors="replace")
        if start_line is None:
            return raw
        lines = raw.splitlines()
        s = max(1, start_line)
        e = end_line if end_line is not None else start_line
        e = max(s, e)
        if s > len(lines):
            return ""
        e = min(e, len(lines))
        return "\n".join(lines[s - 1 : e])
    except Exception:
        return ""


def _resolve_source_path(file_path: str, repo_root: Optional[str]) -> Path:
    p = Path(file_path)
    if p.is_absolute():
        return p
    if repo_root:
        return Path(repo_root) / p
    return p


def _truncate_to_budget(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    suffix = "\n\n<!-- TRUNCATED: character budget reached -->\n"

    if len(text) <= budget:
        return text

    if budget <= len(suffix):
        return suffix[-budget:]

    keep = max(0, budget - len(suffix))
    return text[:keep] + suffix


def _append_truncation_marker_if_possible(text: str, budget: int) -> str:
    """
    Ensure truncation marker is present when content was omitted due to budget.
    """
    marker = "<!-- TRUNCATED: character budget reached -->"

    if budget <= 0:
        return ""

    # If marker already present and within budget, keep as-is.
    if marker in text and len(text) <= budget:
        return text

    suffix = "\n\n<!-- TRUNCATED: character budget reached -->\n"

    # If there's no room for full suffix, preserve rightmost marker fragment.
    if budget <= len(suffix):
        return suffix[-budget:]

    # If we can append full suffix without exceeding budget, do it.
    if len(text) + len(suffix) <= budget:
        return text + suffix

    # Otherwise trim and append marker suffix.
    keep = max(0, budget - len(suffix))
    return text[:keep] + suffix


def _string(v: object) -> Optional[str]:
    if isinstance(v, str):
        return v
    return None


def _int_or_none(v: object) -> Optional[int]:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return int(s)
    return None


__all__ = ["linearize_subgraph", "DEFAULT_MAX_CHARS"]
