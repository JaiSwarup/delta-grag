"""Git materialization for configured real-project evaluation commits."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.eval.corpus import EvalCommitCase


@dataclass(frozen=True)
class MaterializedCommit:
    case: EvalCommitCase
    repo_path: Path
    base_sha: str
    head_sha: str
    diff_text: str
    changed_files: tuple[str, ...]


def materialize_commit(
    case: EvalCommitCase,
    *,
    repos_dir: str | Path,
    refresh: bool = False,
) -> MaterializedCommit:
    repos_root = Path(repos_dir).expanduser().resolve()
    repos_root.mkdir(parents=True, exist_ok=True)
    repo_path = repos_root / case.repo

    if repo_path.exists():
        if refresh:
            _git(["fetch", "--all", "--tags"], cwd=repo_path)
    else:
        _git(["clone", case.repo_url, str(repo_path)], cwd=repos_root)

    _git(["fetch", "--all", "--tags"], cwd=repo_path)
    head_sha = _git(["rev-parse", case.sha], cwd=repo_path)
    base_sha = _git(["rev-parse", f"{head_sha}^"], cwd=repo_path)
    diff_text = _git(["diff", base_sha, head_sha], cwd=repo_path)
    changed_files = tuple(
        line.strip()
        for line in _git(["diff", "--name-only", base_sha, head_sha], cwd=repo_path).splitlines()
        if line.strip()
    )
    _git(["checkout", "--force", head_sha], cwd=repo_path)

    return MaterializedCommit(
        case=case,
        repo_path=repo_path,
        base_sha=base_sha,
        head_sha=head_sha,
        diff_text=diff_text,
        changed_files=changed_files,
    )


def _git(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {message}")
    return completed.stdout.strip()


__all__ = ["MaterializedCommit", "materialize_commit"]
