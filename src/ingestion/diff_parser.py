"""
Unified diff parser for PR-aware ingestion.

This module parses unified diff text and extracts:
- Changed files
- Hunks per file
- Added/deleted/context line spans
- New-file changed line numbers (for anchor resolution)

The parser is deterministic and intentionally conservative:
- It supports common `git diff`/unified diff shapes.
- It ignores unsupported metadata lines unless they are needed
  to infer file boundaries and hunk locations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# -----------------------------
# Data models
# -----------------------------


@dataclass(frozen=True)
class DiffLine:
    """A single line inside a unified diff hunk."""

    kind: str  # one of: "add", "del", "ctx"
    text: str
    old_line: Optional[int]
    new_line: Optional[int]


@dataclass(frozen=True)
class DiffHunk:
    """A parsed hunk with header metadata and classified lines."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: Tuple[DiffLine, ...]

    @property
    def added_new_lines(self) -> Tuple[int, ...]:
        return tuple(
            dl.new_line
            for dl in self.lines
            if dl.kind == "add" and dl.new_line is not None
        )

    @property
    def deleted_old_lines(self) -> Tuple[int, ...]:
        return tuple(
            dl.old_line
            for dl in self.lines
            if dl.kind == "del" and dl.old_line is not None
        )

    @property
    def touched_new_lines(self) -> Tuple[int, ...]:
        """
        New-file line numbers considered touched for anchoring.

        Includes:
        - Added lines directly.
        - Context lines adjacent to add/del blocks (via all context lines in hunk),
          which helps map modifications where line replacement occurred.
        """
        return tuple(
            dl.new_line
            for dl in self.lines
            if dl.new_line is not None and dl.kind in ("add", "ctx")
        )


@dataclass(frozen=True)
class FileDiff:
    """All diff information for one file path in the new revision."""

    old_path: Optional[str]
    new_path: Optional[str]
    hunks: Tuple[DiffHunk, ...] = field(default_factory=tuple)
    is_new_file: bool = False
    is_deleted_file: bool = False
    is_rename: bool = False
    rename_from: Optional[str] = None
    rename_to: Optional[str] = None

    @property
    def path(self) -> Optional[str]:
        # Prefer new path for PR anchoring.
        return self.new_path or self.old_path

    @property
    def changed_new_lines(self) -> Tuple[int, ...]:
        nums: List[int] = []
        for h in self.hunks:
            nums.extend(h.touched_new_lines)
        return tuple(sorted(set(nums)))

    @property
    def changed_added_lines(self) -> Tuple[int, ...]:
        nums: List[int] = []
        for h in self.hunks:
            nums.extend(h.added_new_lines)
        return tuple(sorted(set(nums)))


@dataclass(frozen=True)
class DiffParseResult:
    """Top-level parsed diff output."""

    files: Tuple[FileDiff, ...]

    @property
    def changed_files(self) -> Tuple[str, ...]:
        out = []
        for f in self.files:
            p = f.path
            if p is not None and p != "/dev/null":
                out.append(p)
        return tuple(out)

    @property
    def changed_lines_by_file(self) -> Dict[str, Tuple[int, ...]]:
        out: Dict[str, Tuple[int, ...]] = {}
        for f in self.files:
            p = f.path
            if p is None or p == "/dev/null":
                continue
            out[p] = f.changed_new_lines
        return out


# -----------------------------
# Parser
# -----------------------------


_DIFF_START_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
_HUNK_RE = re.compile(r"^@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@(?:\s*(.*))?$")
_PATH_OLD_RE = re.compile(r"^---\s+(.*)$")
_PATH_NEW_RE = re.compile(r"^\+\+\+\s+(.*)$")
_RENAME_FROM_RE = re.compile(r"^rename from (.+)$")
_RENAME_TO_RE = re.compile(r"^rename to (.+)$")
_NEW_FILE_RE = re.compile(r"^new file mode\s+\d+$")
_DELETED_FILE_RE = re.compile(r"^deleted file mode\s+\d+$")


def parse_unified_diff(diff_text: str) -> DiffParseResult:
    """
    Parse unified diff text into structured file/hunk/line objects.

    Parameters
    ----------
    diff_text:
        Raw unified diff text (e.g., from `git diff` or PR patch payload).

    Returns
    -------
    DiffParseResult
    """
    if not isinstance(diff_text, str):
        raise TypeError("diff_text must be a string")

    lines = diff_text.splitlines()
    files: List[FileDiff] = []

    current_old: Optional[str] = None
    current_new: Optional[str] = None
    current_hunks: List[DiffHunk] = []
    current_is_new = False
    current_is_deleted = False
    current_is_rename = False
    current_rename_from: Optional[str] = None
    current_rename_to: Optional[str] = None

    i = 0
    while i < len(lines):
        line = lines[i]

        # Start of new file section
        m_diff = _DIFF_START_RE.match(line)
        if m_diff:
            # flush previous
            if current_old is not None or current_new is not None or current_hunks:
                files.append(
                    FileDiff(
                        old_path=current_old,
                        new_path=current_new,
                        hunks=tuple(current_hunks),
                        is_new_file=current_is_new,
                        is_deleted_file=current_is_deleted,
                        is_rename=current_is_rename,
                        rename_from=current_rename_from,
                        rename_to=current_rename_to,
                    )
                )

            # reset state
            current_old = _normalize_git_path(m_diff.group(1))
            current_new = _normalize_git_path(m_diff.group(2))
            current_hunks = []
            current_is_new = False
            current_is_deleted = False
            current_is_rename = False
            current_rename_from = None
            current_rename_to = None

            i += 1
            continue

        # File metadata
        if _NEW_FILE_RE.match(line):
            current_is_new = True
            i += 1
            continue

        if _DELETED_FILE_RE.match(line):
            current_is_deleted = True
            i += 1
            continue

        m_rf = _RENAME_FROM_RE.match(line)
        if m_rf:
            current_is_rename = True
            current_rename_from = _normalize_git_path(m_rf.group(1))
            i += 1
            continue

        m_rt = _RENAME_TO_RE.match(line)
        if m_rt:
            current_is_rename = True
            current_rename_to = _normalize_git_path(m_rt.group(1))
            i += 1
            continue

        # Path headers
        m_old = _PATH_OLD_RE.match(line)
        if m_old:
            current_old = _normalize_patch_path(m_old.group(1))
            i += 1
            continue

        m_new = _PATH_NEW_RE.match(line)
        if m_new:
            current_new = _normalize_patch_path(m_new.group(1))
            i += 1
            continue

        # Hunk parsing
        m_hunk = _HUNK_RE.match(line)
        if m_hunk:
            old_start = int(m_hunk.group(1))
            old_count = int(m_hunk.group(2) or 1)
            new_start = int(m_hunk.group(3))
            new_count = int(m_hunk.group(4) or 1)
            trailing = m_hunk.group(5) or ""
            header = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@ {trailing}".rstrip()

            parsed_hunk, next_i = _parse_hunk_lines(
                lines=lines,
                start_index=i + 1,
                old_start=old_start,
                new_start=new_start,
            )
            current_hunks.append(
                DiffHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    header=header,
                    lines=tuple(parsed_hunk),
                )
            )
            i = next_i
            continue

        i += 1

    # Flush last file block if any
    if current_old is not None or current_new is not None or current_hunks:
        files.append(
            FileDiff(
                old_path=current_old,
                new_path=current_new,
                hunks=tuple(current_hunks),
                is_new_file=current_is_new,
                is_deleted_file=current_is_deleted,
                is_rename=current_is_rename,
                rename_from=current_rename_from,
                rename_to=current_rename_to,
            )
        )

    # Keep deterministic order and drop file entries with no path signal.
    normalized = []
    for f in files:
        if f.old_path is None and f.new_path is None:
            continue
        normalized.append(f)

    return DiffParseResult(files=tuple(normalized))


def _parse_hunk_lines(
    lines: Sequence[str],
    start_index: int,
    old_start: int,
    new_start: int,
) -> Tuple[List[DiffLine], int]:
    """
    Parse hunk body lines until next hunk or next file section.
    Returns (parsed_lines, next_index_after_hunk).
    """
    out: List[DiffLine] = []
    old_ln = old_start
    new_ln = new_start

    i = start_index
    while i < len(lines):
        raw = lines[i]

        # stop at next hunk or file header
        if raw.startswith("@@ "):
            break
        if raw.startswith("diff --git "):
            break
        if (
            raw.startswith("--- ")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("+++ ")
        ):
            break

        # "\ No newline at end of file" metadata in hunk
        if raw.startswith("\\ "):
            i += 1
            continue

        if raw.startswith("+"):
            out.append(
                DiffLine(
                    kind="add",
                    text=raw[1:],
                    old_line=None,
                    new_line=new_ln,
                )
            )
            new_ln += 1
        elif raw.startswith("-"):
            out.append(
                DiffLine(
                    kind="del",
                    text=raw[1:],
                    old_line=old_ln,
                    new_line=None,
                )
            )
            old_ln += 1
        elif raw.startswith(" "):
            out.append(
                DiffLine(
                    kind="ctx",
                    text=raw[1:],
                    old_line=old_ln,
                    new_line=new_ln,
                )
            )
            old_ln += 1
            new_ln += 1
        else:
            # Fallback: treat unknown as context-like line content.
            out.append(
                DiffLine(
                    kind="ctx",
                    text=raw,
                    old_line=old_ln,
                    new_line=new_ln,
                )
            )
            old_ln += 1
            new_ln += 1

        i += 1

    return out, i


def _normalize_patch_path(path_token: str) -> Optional[str]:
    """
    Normalize paths from --- / +++ headers.
    Examples:
      "a/foo.py" -> "foo.py"
      "b/foo.py" -> "foo.py"
      "/dev/null" -> "/dev/null"
    """
    p = path_token.strip()
    if p == "/dev/null":
        return p
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    return _normalize_git_path(p)


def _normalize_git_path(path_token: str) -> Optional[str]:
    p = path_token.strip()
    if not p:
        return None
    return p.replace("\\", "/")


def collect_changed_file_paths(diff_text: str) -> Tuple[str, ...]:
    """Convenience helper returning changed file paths only."""
    parsed = parse_unified_diff(diff_text)
    return parsed.changed_files


def collect_changed_lines_by_file(diff_text: str) -> Dict[str, Tuple[int, ...]]:
    """Convenience helper returning new-file touched line numbers per file."""
    parsed = parse_unified_diff(diff_text)
    return parsed.changed_lines_by_file


__all__ = [
    "DiffLine",
    "DiffHunk",
    "FileDiff",
    "DiffParseResult",
    "parse_unified_diff",
    "collect_changed_file_paths",
    "collect_changed_lines_by_file",
]
