"""
Repository clone and snapshot cache management.

This module is intentionally separate from `src.ingestion.repo_loader`: it owns
network/disk snapshot acquisition, while the ingestion loader works with an
already-materialized local repository tree.
"""

from __future__ import annotations

import subprocess
import shutil
from time import sleep
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse


class RepoError(RuntimeError):
    """Raised when repository clone or checkout operations fail."""


@dataclass(frozen=True)
class RepoSnapshot:
    """Materialized repository snapshot at a specific commit."""

    repo_url: str
    commit_sha: str
    local_path: Path
    cloned_at: datetime
    size_mb: float

    def get_file_list(self, extensions: Sequence[str] = (".py",)) -> list[Path]:
        """Return deterministic repository-relative file paths filtered by suffix."""
        normalized_exts = _normalize_extensions(extensions)
        files: list[Path] = []
        for path in sorted(self.local_path.rglob("*")):
            if not path.is_file():
                continue
            rel_path = path.relative_to(self.local_path)
            if any(part == ".git" for part in rel_path.parts):
                continue
            if normalized_exts and path.suffix.lower() not in normalized_exts:
                continue
            files.append(rel_path)
        return files


def clone_at_sha(repo_url: str, commit_sha: str, cache_dir: str | Path) -> RepoSnapshot:
    """
    Clone a repository and checkout a specific commit, reusing cached snapshots.

    Snapshot cache layout:
        <cache_dir>/<normalized_repo_id>/<full_commit_sha>/
    """
    if not repo_url or not repo_url.strip():
        raise ValueError("repo_url must be a non-empty string")
    if not commit_sha or not commit_sha.strip():
        raise ValueError("commit_sha must be a non-empty string")

    normalized_sha = commit_sha.strip()
    cache_root = Path(cache_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)

    snapshot_path = cache_root / _repo_cache_key(repo_url) / normalized_sha
    if snapshot_path.exists():
        return _load_cached_snapshot(
            repo_url=repo_url,
            commit_sha=normalized_sha,
            snapshot_path=snapshot_path,
        )

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git_with_retry(["clone", repo_url, str(snapshot_path)])
        _git_with_retry(["checkout", normalized_sha], cwd=snapshot_path)
    except RepoError:
        if snapshot_path.exists():
            _cleanup_incomplete_snapshot(snapshot_path)
        raise

    return _build_snapshot(
        repo_url=repo_url,
        commit_sha=normalized_sha,
        snapshot_path=snapshot_path,
    )


def _load_cached_snapshot(
    *,
    repo_url: str,
    commit_sha: str,
    snapshot_path: Path,
) -> RepoSnapshot:
    _validate_snapshot_path(snapshot_path)
    head_sha = _git(["rev-parse", "HEAD"], cwd=snapshot_path).strip()
    if head_sha != commit_sha:
        raise RepoError(
            f"Cached snapshot HEAD mismatch for {snapshot_path}: "
            f"expected {commit_sha}, got {head_sha}"
        )
    return _build_snapshot(
        repo_url=repo_url,
        commit_sha=commit_sha,
        snapshot_path=snapshot_path,
    )


def _build_snapshot(
    *,
    repo_url: str,
    commit_sha: str,
    snapshot_path: Path,
) -> RepoSnapshot:
    _validate_snapshot_path(snapshot_path)
    head_sha = _git(["rev-parse", "HEAD"], cwd=snapshot_path).strip()
    if head_sha != commit_sha:
        raise RepoError(
            f"Repository HEAD does not match requested commit: "
            f"expected {commit_sha}, got {head_sha}"
        )
    return RepoSnapshot(
        repo_url=repo_url,
        commit_sha=commit_sha,
        local_path=snapshot_path,
        cloned_at=datetime.now(UTC),
        size_mb=_directory_size_mb(snapshot_path),
    )


def _validate_snapshot_path(snapshot_path: Path) -> None:
    if not snapshot_path.exists() or not snapshot_path.is_dir():
        raise RepoError(f"Snapshot path does not exist: {snapshot_path}")
    if not (snapshot_path / ".git").exists():
        raise RepoError(f"Snapshot path is not a git repository: {snapshot_path}")


def _git(args: Sequence[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "git failed"
        raise RepoError(stderr)
    return completed.stdout


def _git_with_retry(args: Sequence[str], cwd: Path | None = None) -> str:
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            return _git(args, cwd=cwd)
        except RepoError as exc:
            if attempt >= attempts or not _looks_transient_git_error(str(exc)):
                raise
            sleep(0.2 * (2 ** (attempt - 1)))
    raise RepoError("git operation failed after retries")


def _looks_transient_git_error(message: str) -> bool:
    msg = message.lower()
    transient_markers = (
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "temporary failure",
        "unable to access",
        "tls",
        "proxy",
        "network",
        "http 5",
    )
    return any(marker in msg for marker in transient_markers)


def _repo_cache_key(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    raw = parsed.path if parsed.scheme else repo_url
    cleaned = raw.strip().rstrip("/").replace("\\", "/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    parts = [part for part in cleaned.split("/") if part and part not in {".", ".."}]
    if not parts:
        raise ValueError(f"Could not derive cache key from repo_url: {repo_url}")
    if len(parts) >= 2:
        return f"{_sanitize_cache_part(parts[-2])}__{_sanitize_cache_part(parts[-1])}"
    return _sanitize_cache_part(parts[-1])


def _sanitize_cache_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def _normalize_extensions(extensions: Sequence[str]) -> set[str]:
    normalized: set[str] = set()
    for ext in extensions:
        value = str(ext).strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = f".{value}"
        normalized.add(value)
    return normalized


def _directory_size_mb(root: Path) -> float:
    total_bytes = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            total_bytes += path.stat().st_size
        except OSError:
            continue
    return round(total_bytes / (1024 * 1024), 4)


def _cleanup_incomplete_snapshot(snapshot_path: Path) -> None:
    shutil.rmtree(snapshot_path, ignore_errors=True)


__all__ = ["RepoError", "RepoSnapshot", "clone_at_sha"]
