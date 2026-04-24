from __future__ import annotations

import subprocess
from pathlib import Path

from src.repo_manager import RepoError, clone_at_sha


def _git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_source_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source-repo"
    repo.mkdir()

    _git(["init"], repo)
    _git(["config", "user.name", "Test User"], repo)
    _git(["config", "user.email", "test@example.com"], repo)

    _write(repo / "pkg" / "__init__.py", "")
    _write(repo / "pkg" / "mod.py", "def first():\n    return 1\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "first"], repo)
    first_sha = _git(["rev-parse", "HEAD"], repo)

    _write(repo / "pkg" / "mod.py", "def second():\n    return 2\n")
    _write(repo / "README.md", "# sample\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "second"], repo)
    second_sha = _git(["rev-parse", "HEAD"], repo)

    return repo, first_sha, second_sha


def test_clone_at_sha_creates_snapshot_and_checks_out_requested_commit(
    tmp_path: Path,
) -> None:
    source_repo, first_sha, _ = _build_source_repo(tmp_path)
    cache_dir = tmp_path / "cache"

    snapshot = clone_at_sha(str(source_repo), first_sha, cache_dir)

    assert snapshot.local_path.exists()
    assert snapshot.local_path.is_dir()
    assert (snapshot.local_path / ".git").exists()
    assert snapshot.commit_sha == first_sha
    assert snapshot.size_mb >= 0

    head_sha = _git(["rev-parse", "HEAD"], snapshot.local_path)
    assert head_sha == first_sha


def test_clone_at_sha_returns_cached_snapshot_for_same_repo_and_commit(
    tmp_path: Path,
) -> None:
    source_repo, first_sha, _ = _build_source_repo(tmp_path)
    cache_dir = tmp_path / "cache"

    first_snapshot = clone_at_sha(str(source_repo), first_sha, cache_dir)
    second_snapshot = clone_at_sha(str(source_repo), first_sha, cache_dir)

    assert first_snapshot.local_path == second_snapshot.local_path
    assert second_snapshot.commit_sha == first_sha


def test_repo_snapshot_get_file_list_filters_python_files(tmp_path: Path) -> None:
    source_repo, _, second_sha = _build_source_repo(tmp_path)
    cache_dir = tmp_path / "cache"

    snapshot = clone_at_sha(str(source_repo), second_sha, cache_dir)
    files = snapshot.get_file_list()

    assert Path("pkg/mod.py") in files
    assert all(path.suffix == ".py" for path in files)
    assert Path("README.md") not in files


def test_clone_at_sha_invalid_commit_raises_repo_error(tmp_path: Path) -> None:
    source_repo, _, _ = _build_source_repo(tmp_path)
    cache_dir = tmp_path / "cache"

    try:
        clone_at_sha(str(source_repo), "deadbeef", cache_dir)
        assert False, "Expected RepoError for invalid commit SHA"
    except RepoError:
        pass
