"""
Repository file indexing with lightweight metadata extraction.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from src.repo_manager import RepoSnapshot

DEFAULT_INCLUDE_EXTENSIONS: tuple[str, ...] = (".py",)
DEFAULT_EXCLUDED_DIRS: tuple[str, ...] = (
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
)
DEFAULT_MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class FileMetadata:
    path: Path
    size_bytes: int
    loc: int
    encoding: str
    is_parseable: bool


@dataclass(frozen=True)
class FileIndex:
    root_path: Path
    files: dict[str, FileMetadata] = field(default_factory=dict)

    def get_python_files(self) -> list[FileMetadata]:
        return [
            metadata
            for rel_path, metadata in sorted(self.files.items())
            if Path(rel_path).suffix.lower() == ".py"
        ]


def build_file_index(
    snapshot: RepoSnapshot | str | Path,
    *,
    include_extensions: Sequence[str] = DEFAULT_INCLUDE_EXTENSIONS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_workers: int = 8,
) -> FileIndex:
    root_path = _resolve_snapshot_root(snapshot)
    normalized_exts = _normalize_extensions(include_extensions)
    candidate_paths = [
        path
        for path in sorted(root_path.rglob("*"))
        if _is_candidate_file(
            root_path=root_path,
            path=path,
            include_extensions=normalized_exts,
            max_file_bytes=max_file_bytes,
        )
    ]

    files: dict[str, FileMetadata] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        for metadata in executor.map(
            lambda p: _index_file(root_path, p), candidate_paths
        ):
            if metadata is None or not metadata.is_parseable:
                continue
            files[metadata.path.as_posix()] = metadata

    return FileIndex(root_path=root_path, files=files)


def _resolve_snapshot_root(snapshot: RepoSnapshot | str | Path) -> Path:
    if isinstance(snapshot, RepoSnapshot):
        root = snapshot.local_path
    else:
        root = Path(snapshot)
    root = root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Snapshot path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Snapshot path is not a directory: {root}")
    return root


def _normalize_extensions(include_extensions: Sequence[str]) -> set[str]:
    normalized: set[str] = set()
    for ext in include_extensions:
        value = str(ext).strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = f".{value}"
        normalized.add(value)
    return normalized


def _is_candidate_file(
    *,
    root_path: Path,
    path: Path,
    include_extensions: set[str],
    max_file_bytes: int,
) -> bool:
    if not path.is_file():
        return False
    rel_path = path.relative_to(root_path)
    if any(part in DEFAULT_EXCLUDED_DIRS for part in rel_path.parts[:-1]):
        return False
    if include_extensions and path.suffix.lower() not in include_extensions:
        return False
    try:
        size_bytes = path.stat().st_size
    except OSError:
        return False
    if size_bytes > max_file_bytes:
        return False
    return True


def _index_file(root_path: Path, path: Path) -> FileMetadata | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    parseable, encoding, text = _decode_text(raw)
    rel_path = path.relative_to(root_path)
    loc = _count_loc(text) if parseable else 0
    return FileMetadata(
        path=rel_path,
        size_bytes=len(raw),
        loc=loc,
        encoding=encoding,
        is_parseable=parseable,
    )


def _decode_text(raw: bytes) -> tuple[bool, str, str]:
    if b"\x00" in raw:
        return False, "binary", ""
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return True, "utf-8-sig", raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return False, "utf-8-sig", ""
    try:
        return True, "utf-8", raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return True, "latin-1", raw.decode("latin-1")
    except UnicodeDecodeError:
        return False, "unknown", ""


def _count_loc(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


__all__ = [
    "DEFAULT_INCLUDE_EXTENSIONS",
    "FileIndex",
    "FileMetadata",
    "build_file_index",
]
