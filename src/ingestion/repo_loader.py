"""
Repository loader utilities for ingestion.

This module provides lightweight, deterministic helpers to:
- Validate and normalize repository paths.
- Enumerate source files (with include/exclude controls).
- Read file contents safely for downstream parsing/indexing.

Design goals:
- Keep behavior predictable and explicit.
- Avoid hidden global state.
- Stay generic so it can support multiple language pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Sequence, Tuple

DEFAULT_EXCLUDED_DIRS: Tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    ".tox",
)

DEFAULT_EXCLUDED_FILE_SUFFIXES: Tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".dylib",
    ".class",
    ".jar",
    ".min.js",
    ".map",
)


@dataclass(frozen=True)
class RepoLoadConfig:
    """
    Configuration for repository file enumeration.
    """

    include_extensions: Tuple[str, ...] = (
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".cpp",
        ".cc",
        ".cxx",
        ".c",
        ".h",
        ".hpp",
    )
    exclude_dirs: Tuple[str, ...] = DEFAULT_EXCLUDED_DIRS
    exclude_file_suffixes: Tuple[str, ...] = DEFAULT_EXCLUDED_FILE_SUFFIXES
    include_hidden_files: bool = False
    follow_symlinks: bool = False
    max_file_bytes: Optional[int] = None  # None means no explicit cap


@dataclass(frozen=True)
class RepoFile:
    """
    Metadata + content handle for a repository file.
    """

    abs_path: Path
    rel_path: str
    size_bytes: int


@dataclass(frozen=True)
class RepoSnapshot:
    """
    Snapshot view of discovered repository files.
    """

    repo_root: Path
    files: Tuple[RepoFile, ...] = field(default_factory=tuple)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)


def load_repo_snapshot(
    repo_root: str | Path, config: Optional[RepoLoadConfig] = None
) -> RepoSnapshot:
    """
    Validate repository root and return deterministic file snapshot.
    """
    cfg = config or RepoLoadConfig()
    root = resolve_repo_root(repo_root)

    files = tuple(iter_repo_files(root, cfg))
    return RepoSnapshot(repo_root=root, files=files)


def resolve_repo_root(repo_root: str | Path) -> Path:
    """
    Resolve and validate repository root path.

    Raises:
        ValueError: if path is empty/invalid.
        FileNotFoundError: if path does not exist.
        NotADirectoryError: if path is not a directory.
    """
    if repo_root is None:
        raise ValueError("repo_root cannot be None")

    root = Path(repo_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Repository root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Repository root is not a directory: {root}")

    return root


def iter_repo_files(
    repo_root: Path, config: Optional[RepoLoadConfig] = None
) -> Iterator[RepoFile]:
    """
    Yield repository files in deterministic order based on normalized relative path.
    """
    cfg = config or RepoLoadConfig()
    root = resolve_repo_root(repo_root)

    include_exts = _normalize_extensions(cfg.include_extensions)
    excluded_dirs = set(cfg.exclude_dirs)
    excluded_suffixes = tuple(cfg.exclude_file_suffixes)

    candidates: list[Path] = []

    for p in root.rglob("*"):
        if not p.is_file():
            continue

        if p.is_symlink() and not cfg.follow_symlinks:
            continue

        rel = p.relative_to(root)
        if _should_exclude_path(
            rel=rel,
            include_hidden_files=cfg.include_hidden_files,
            excluded_dirs=excluded_dirs,
            include_exts=include_exts,
            excluded_suffixes=excluded_suffixes,
        ):
            continue

        try:
            size = p.stat().st_size
        except OSError:
            # Skip unreadable file metadata.
            continue

        if cfg.max_file_bytes is not None and size > cfg.max_file_bytes:
            continue

        candidates.append(p)

    # Deterministic ordering.
    for abs_path in sorted(candidates, key=lambda x: _as_posix_rel(root, x)):
        rel_path = _as_posix_rel(root, abs_path)
        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        yield RepoFile(abs_path=abs_path, rel_path=rel_path, size_bytes=size)


def read_text_file(
    file_path: str | Path,
    *,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> str:
    """
    Read file as text using explicit decoding policy.
    """
    path = Path(file_path)
    return path.read_text(encoding=encoding, errors=errors)


def read_repo_file(
    repo_root: str | Path,
    rel_path: str,
    *,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> str:
    """
    Read a repository-relative file safely.

    Guards against path traversal outside repo_root.
    """
    root = resolve_repo_root(repo_root)
    rel = Path(rel_path)

    if rel.is_absolute():
        raise ValueError("rel_path must be repository-relative, not absolute")

    candidate = (root / rel).resolve()
    if not _is_within_root(root, candidate):
        raise ValueError(f"Path escapes repository root: {rel_path}")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"Repository file not found: {rel_path}")

    return candidate.read_text(encoding=encoding, errors=errors)


def _normalize_extensions(exts: Sequence[str]) -> Tuple[str, ...]:
    out: list[str] = []
    for ext in exts:
        e = ext.strip()
        if not e:
            continue
        if not e.startswith("."):
            e = f".{e}"
        out.append(e.lower())
    return tuple(sorted(set(out)))


def _should_exclude_path(
    *,
    rel: Path,
    include_hidden_files: bool,
    excluded_dirs: set[str],
    include_exts: Tuple[str, ...],
    excluded_suffixes: Tuple[str, ...],
) -> bool:
    parts = rel.parts
    name = rel.name

    # Excluded directories anywhere in the relative path.
    if any(part in excluded_dirs for part in parts[:-1]):
        return True

    # Hidden files/dirs (except when allowed).
    if not include_hidden_files:
        if any(part.startswith(".") for part in parts):
            return True

    lower_name = name.lower()

    # Explicitly excluded suffixes.
    if any(lower_name.endswith(sfx.lower()) for sfx in excluded_suffixes):
        return True

    # Extension filter.
    if include_exts:
        ext = Path(lower_name).suffix.lower()
        if ext not in include_exts:
            return True

    return False


def _as_posix_rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _is_within_root(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = [
    "RepoLoadConfig",
    "RepoFile",
    "RepoSnapshot",
    "resolve_repo_root",
    "iter_repo_files",
    "load_repo_snapshot",
    "read_text_file",
    "read_repo_file",
]
