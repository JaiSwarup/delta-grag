"""
Stable graph node identity helpers.
"""

from __future__ import annotations

from pathlib import Path


def normalize_repo_relative_path(file_path: str | Path, repo_root: str | Path) -> str:
    path = Path(file_path).expanduser().resolve()
    root = Path(repo_root).expanduser().resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def build_node_id(
    file_path: str | Path,
    fqn: str,
    repo_root: str | Path,
) -> str:
    return f"{normalize_repo_relative_path(file_path, repo_root)}::{fqn}"


__all__ = ["build_node_id", "normalize_repo_relative_path"]
