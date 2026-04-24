"""Ingestion package for PR diff parsing and anchor resolution."""

from .anchor_resolver import (
    AnchorSet,
    ChangedHunk,
    resolve_anchors,
    resolve_anchors_from_diff_map,
    resolve_anchors_from_diff_text,
    resolve_anchors_from_parsed_diff,
)
from .diff_parser import (
    DiffHunk,
    DiffLine,
    DiffParseResult,
    FileDiff,
    parse_unified_diff,
)

__all__ = [
    "ChangedHunk",
    "AnchorSet",
    "resolve_anchors",
    "resolve_anchors_from_diff_map",
    "resolve_anchors_from_parsed_diff",
    "resolve_anchors_from_diff_text",
    "DiffLine",
    "DiffHunk",
    "FileDiff",
    "DiffParseResult",
    "parse_unified_diff",
]
