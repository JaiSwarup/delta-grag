"""
Anchor resolver for mapping PR diff hunks to function-level graph anchors.

This module resolves changed line ranges (hunks) to function nodes in a call graph.
Resolution strategy:

1) Exact enclosure in the same file:
   - A node anchors a hunk if its [start_line, end_line] encloses the changed span.
2) Nearest fallback in the same file:
   - If no enclosing node exists, pick the nearest function by line distance.
3) Optional conservative fallback:
   - If file has no known nodes, no anchor is produced for that hunk.

The implementation is deterministic:
- stable file ordering
- stable hunk ordering
- deterministic tie-breaking for nearest candidates
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import networkx as nx


@dataclass(frozen=True)
class ChangedHunk:
    """
    Represents one changed span in a file.
    """

    file_path: str
    start_line: int
    end_line: int
    hunk_id: Optional[str] = None

    def normalized(self) -> "ChangedHunk":
        s = max(1, int(self.start_line))
        e = max(1, int(self.end_line))
        if e < s:
            s, e = e, s
        return ChangedHunk(
            file_path=_normalize_path(self.file_path),
            start_line=s,
            end_line=e,
            hunk_id=self.hunk_id,
        )


@dataclass
class AnchorSet:
    """
    Resolved anchor output suitable for downstream traversal.
    """

    anchor_node_ids: List[str]
    changed_hunks: List[ChangedHunk]
    unresolved_hunks: List[ChangedHunk] = field(default_factory=list)
    hunk_to_anchor: Dict[str, str] = field(default_factory=dict)
    pr_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _NodeRef:
    node_id: str
    file_path: str
    start_line: int
    end_line: int
    qualified_name: str


def resolve_anchors(
    graph: nx.DiGraph,
    changed_hunks: Sequence[ChangedHunk],
    *,
    file_attr: str = "file",
    start_line_attr: str = "start_line",
    end_line_attr: str = "end_line",
    qualified_name_attr: str = "qualified_name",
    pr_metadata: Optional[Mapping[str, Any]] = None,
) -> AnchorSet:
    """
    Resolve changed hunks to function node IDs in `graph`.

    Parameters
    ----------
    graph:
        Directed call graph with node attributes containing file and line metadata.
    changed_hunks:
        Sequence of hunks to resolve.
    file_attr/start_line_attr/end_line_attr/qualified_name_attr:
        Node attribute keys.
    pr_metadata:
        Optional PR metadata copied into AnchorSet.

    Returns
    -------
    AnchorSet
        Includes resolved unique anchors, unresolved hunks, and hunk->anchor mapping.
    """
    normalized_hunks = [h.normalized() for h in changed_hunks]
    sorted_hunks = sorted(
        normalized_hunks,
        key=lambda h: (
            h.file_path,
            h.start_line,
            h.end_line,
            h.hunk_id or "",
        ),
    )

    by_file = _index_graph_nodes(
        graph,
        file_attr=file_attr,
        start_line_attr=start_line_attr,
        end_line_attr=end_line_attr,
        qualified_name_attr=qualified_name_attr,
    )

    resolved_ids: List[str] = []
    resolved_set: Set[str] = set()
    unresolved: List[ChangedHunk] = []
    hunk_to_anchor: Dict[str, str] = {}

    for idx, h in enumerate(sorted_hunks):
        candidates = by_file.get(h.file_path, [])
        anchor = _resolve_single_hunk(candidates, h)

        if anchor is None:
            unresolved.append(h)
            continue

        if anchor.node_id not in resolved_set:
            resolved_set.add(anchor.node_id)
            resolved_ids.append(anchor.node_id)

        key = (
            h.hunk_id
            if h.hunk_id
            else f"{h.file_path}:{h.start_line}-{h.end_line}#{idx}"
        )
        hunk_to_anchor[key] = anchor.node_id

    return AnchorSet(
        anchor_node_ids=resolved_ids,
        changed_hunks=sorted_hunks,
        unresolved_hunks=unresolved,
        hunk_to_anchor=hunk_to_anchor,
        pr_metadata=dict(pr_metadata or {}),
    )


def resolve_anchors_from_diff_map(
    graph: nx.DiGraph,
    diff_map: Mapping[str, Iterable[Tuple[int, int]]],
    *,
    pr_metadata: Optional[Mapping[str, Any]] = None,
    file_attr: str = "file",
    start_line_attr: str = "start_line",
    end_line_attr: str = "end_line",
    qualified_name_attr: str = "qualified_name",
) -> AnchorSet:
    """
    Convenience wrapper when diff input is already grouped by file.

    `diff_map` format:
        {
          "path/to/file.py": [(start_line, end_line), ...],
          ...
        }
    """
    hunks: List[ChangedHunk] = []
    for file_path, spans in sorted(
        diff_map.items(), key=lambda kv: _normalize_path(kv[0])
    ):
        for i, span in enumerate(spans):
            if len(span) != 2:
                continue
            s, e = span
            hunks.append(
                ChangedHunk(
                    file_path=file_path,
                    start_line=int(s),
                    end_line=int(e),
                    hunk_id=f"{_normalize_path(file_path)}:{i}",
                )
            )

    return resolve_anchors(
        graph,
        hunks,
        file_attr=file_attr,
        start_line_attr=start_line_attr,
        end_line_attr=end_line_attr,
        qualified_name_attr=qualified_name_attr,
        pr_metadata=pr_metadata,
    )


def _resolve_single_hunk(
    nodes: Sequence[_NodeRef], hunk: ChangedHunk
) -> Optional[_NodeRef]:
    if not nodes:
        return None

    enclosing = [
        n
        for n in nodes
        if n.start_line <= hunk.start_line and n.end_line >= hunk.end_line
    ]
    if enclosing:
        # Prefer smallest enclosing span, then earliest start line, then deterministic id.
        return min(
            enclosing,
            key=lambda n: (
                (n.end_line - n.start_line),
                n.start_line,
                n.qualified_name,
                n.node_id,
            ),
        )

    # Nearest fallback by line distance to interval [start, end].
    return min(
        nodes,
        key=lambda n: (
            _interval_distance(
                hunk.start_line, hunk.end_line, n.start_line, n.end_line
            ),
            abs(n.start_line - hunk.start_line),
            (n.end_line - n.start_line),
            n.qualified_name,
            n.node_id,
        ),
    )


def _interval_distance(a1: int, a2: int, b1: int, b2: int) -> int:
    """
    Distance between closed intervals [a1,a2] and [b1,b2].
    Zero if overlapping.
    """
    if a2 < b1:
        return b1 - a2
    if b2 < a1:
        return a1 - b2
    return 0


def _index_graph_nodes(
    graph: nx.DiGraph,
    *,
    file_attr: str,
    start_line_attr: str,
    end_line_attr: str,
    qualified_name_attr: str,
) -> Dict[str, List[_NodeRef]]:
    """
    Build file -> sorted NodeRef list from graph node attributes.
    """
    by_file: Dict[str, List[_NodeRef]] = {}

    for node_id, data in graph.nodes(data=True):
        if not isinstance(data, Mapping):
            continue

        file_path = _normalize_path(_str_or_empty(data.get(file_attr)))
        if not file_path:
            continue

        start_line = _to_pos_int(data.get(start_line_attr))
        end_line = _to_pos_int(data.get(end_line_attr))
        if start_line is None:
            continue
        if end_line is None:
            end_line = start_line
        if end_line < start_line:
            start_line, end_line = end_line, start_line

        qn = _str_or_empty(data.get(qualified_name_attr)) or str(node_id)

        by_file.setdefault(file_path, []).append(
            _NodeRef(
                node_id=str(node_id),
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                qualified_name=qn,
            )
        )

    # Deterministic ordering within each file.
    for file_path, arr in by_file.items():
        by_file[file_path] = sorted(
            arr,
            key=lambda n: (
                n.start_line,
                n.end_line,
                n.qualified_name,
                n.node_id,
            ),
        )

    return by_file


def _to_pos_int(v: Any) -> Optional[int]:
    try:
        i = int(v)
    except Exception:
        return None
    return i if i >= 1 else None


def _str_or_empty(v: Any) -> str:
    return str(v) if v is not None else ""


def _normalize_path(path: str) -> str:
    p = (path or "").replace("\\", "/").strip()
    # Collapse duplicate slashes conservatively.
    while "//" in p:
        p = p.replace("//", "/")
    return p


def resolve_anchors_from_parsed_diff(
    graph: nx.DiGraph,
    parsed_diff: Any,
    *,
    pr_metadata: Optional[Mapping[str, Any]] = None,
    file_attr: str = "file",
    start_line_attr: str = "start_line",
    end_line_attr: str = "end_line",
    qualified_name_attr: str = "qualified_name",
) -> AnchorSet:
    """
    Resolve anchors directly from a parsed diff object.

    Expected shape (duck-typed):
      parsed_diff.files -> iterable of file objects
      file.path or file.new_path -> file path
      file.hunks -> iterable of hunks
      hunk.new_start/new_count, or hunk.lines with line objects carrying `new_line`

    This keeps the anchor resolver decoupled from any specific diff parser class.
    """
    if parsed_diff is None:
        return AnchorSet(anchor_node_ids=[], changed_hunks=[])

    diff_map: Dict[str, List[Tuple[int, int]]] = {}

    files = getattr(parsed_diff, "files", None)
    if files is None:
        return AnchorSet(anchor_node_ids=[], changed_hunks=[])

    for f in files:
        file_path = _normalize_path(
            _str_or_empty(getattr(f, "path", None) or getattr(f, "new_path", None))
        )
        if not file_path or file_path == "/dev/null":
            continue

        spans: List[Tuple[int, int]] = []

        hunks = getattr(f, "hunks", None) or []
        for h in hunks:
            # Preferred path: hunk provides touched/changed new-file line numbers.
            touched = getattr(h, "touched_new_lines", None)
            if touched:
                span = _span_from_lines(touched)
                if span is not None:
                    spans.append(span)
                    continue

            # Fallback: infer from explicit new_start/new_count.
            new_start = getattr(h, "new_start", None)
            new_count = getattr(h, "new_count", None)
            if isinstance(new_start, int):
                count = int(new_count) if isinstance(new_count, int) else 1
                end = new_start + max(0, count - 1)
                spans.append((max(1, new_start), max(1, end)))
                continue

            # Last-resort fallback: inspect line objects with `new_line`.
            h_lines = getattr(h, "lines", None) or []
            touched_nums: List[int] = []
            for dl in h_lines:
                nl = getattr(dl, "new_line", None)
                if isinstance(nl, int) and nl >= 1:
                    touched_nums.append(nl)
            span = _span_from_lines(touched_nums)
            if span is not None:
                spans.append(span)

        if spans:
            diff_map.setdefault(file_path, []).extend(spans)

    return resolve_anchors_from_diff_map(
        graph,
        diff_map,
        pr_metadata=pr_metadata,
        file_attr=file_attr,
        start_line_attr=start_line_attr,
        end_line_attr=end_line_attr,
        qualified_name_attr=qualified_name_attr,
    )


def resolve_anchors_from_diff_text(
    graph: nx.DiGraph,
    diff_text: str,
    *,
    pr_metadata: Optional[Mapping[str, Any]] = None,
    file_attr: str = "file",
    start_line_attr: str = "start_line",
    end_line_attr: str = "end_line",
    qualified_name_attr: str = "qualified_name",
) -> AnchorSet:
    """
    Resolve anchors directly from raw unified diff text.

    This function imports the parser lazily to avoid hard coupling/import cycles.
    """
    if not isinstance(diff_text, str):
        raise TypeError("diff_text must be a string")

    try:
        from .diff_parser import parse_unified_diff
    except Exception as exc:
        raise RuntimeError(
            "Unable to import unified diff parser for diff-text anchor resolution."
        ) from exc

    parsed = parse_unified_diff(diff_text)
    return resolve_anchors_from_parsed_diff(
        graph,
        parsed,
        pr_metadata=pr_metadata,
        file_attr=file_attr,
        start_line_attr=start_line_attr,
        end_line_attr=end_line_attr,
        qualified_name_attr=qualified_name_attr,
    )


def _span_from_lines(lines: Iterable[int]) -> Optional[Tuple[int, int]]:
    nums = sorted({int(x) for x in lines if int(x) >= 1})
    if not nums:
        return None
    return nums[0], nums[-1]


__all__ = [
    "ChangedHunk",
    "AnchorSet",
    "resolve_anchors",
    "resolve_anchors_from_diff_map",
    "resolve_anchors_from_parsed_diff",
    "resolve_anchors_from_diff_text",
]
