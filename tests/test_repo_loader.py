from __future__ import annotations

from pathlib import Path

from src.ingestion.repo_loader import (
    RepoLoadConfig,
    iter_repo_files,
    load_repo_snapshot,
    read_repo_file,
    read_text_file,
    resolve_repo_root,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_resolve_repo_root_valid(tmp_path: Path) -> None:
    root = resolve_repo_root(tmp_path)
    assert root == tmp_path.resolve()
    assert root.is_dir()


def test_resolve_repo_root_missing_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    try:
        resolve_repo_root(missing)
        assert False, "Expected FileNotFoundError for missing repo root"
    except FileNotFoundError:
        pass


def test_load_repo_snapshot_filters_and_is_deterministic(tmp_path: Path) -> None:
    # Included
    _write(tmp_path / "a.py", "print('a')\n")
    _write(tmp_path / "pkg" / "b.js", "console.log('b');\n")
    _write(tmp_path / "pkg" / "c.ts", "export const c = 1;\n")

    # Excluded by dir
    _write(tmp_path / "__pycache__" / "x.py", "print('x')\n")
    _write(tmp_path / "node_modules" / "lib.js", "console.log('lib');\n")

    # Excluded by extension / suffix
    _write(tmp_path / "notes.txt", "hello\n")
    _write(tmp_path / "artifact.pyc", "compiled\n")

    cfg = RepoLoadConfig()
    snap1 = load_repo_snapshot(tmp_path, cfg)
    snap2 = load_repo_snapshot(tmp_path, cfg)

    rels1 = [f.rel_path for f in snap1.files]
    rels2 = [f.rel_path for f in snap2.files]

    assert rels1 == rels2, "Expected deterministic ordering across snapshots"
    assert rels1 == sorted(rels1), "Expected sorted deterministic rel paths"

    assert "a.py" in rels1
    assert "pkg/b.js" in rels1
    assert "pkg/c.ts" in rels1

    assert "__pycache__/x.py" not in rels1
    assert "node_modules/lib.js" not in rels1
    assert "notes.txt" not in rels1
    assert "artifact.pyc" not in rels1

    assert snap1.file_count == len(rels1)
    assert snap1.total_bytes > 0


def test_iter_repo_files_respects_max_file_bytes(tmp_path: Path) -> None:
    _write(tmp_path / "small.py", "x=1\n")
    _write(tmp_path / "big.py", "a" * 5000)

    cfg = RepoLoadConfig(max_file_bytes=100)
    files = list(iter_repo_files(tmp_path, cfg))
    rels = [f.rel_path for f in files]

    assert "small.py" in rels
    assert "big.py" not in rels


def test_read_text_file_reads_content(tmp_path: Path) -> None:
    fp = tmp_path / "plain.py"
    _write(fp, "def run():\n    return 1\n")

    text = read_text_file(fp)
    assert "def run()" in text
    assert "return 1" in text


def test_read_repo_file_reads_repo_relative_path(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "mod.py", "VALUE = 7\n")

    text = read_repo_file(tmp_path, "src/mod.py")
    assert text.strip() == "VALUE = 7"


def test_read_repo_file_rejects_absolute_path(tmp_path: Path) -> None:
    _write(tmp_path / "ok.py", "x=1\n")

    abs_path = (tmp_path / "ok.py").resolve()
    try:
        read_repo_file(tmp_path, str(abs_path))
        assert False, "Expected ValueError for absolute rel_path"
    except ValueError:
        pass


def test_read_repo_file_blocks_path_escape(tmp_path: Path) -> None:
    _write(tmp_path / "safe.py", "safe=1\n")

    try:
        read_repo_file(tmp_path, "../outside.py")
        assert False, "Expected ValueError for path traversal"
    except ValueError:
        pass


def test_read_repo_file_missing_raises(tmp_path: Path) -> None:
    try:
        read_repo_file(tmp_path, "missing.py")
        assert False, "Expected FileNotFoundError for missing repository file"
    except FileNotFoundError:
        pass


def test_hidden_files_excluded_by_default_and_includable(tmp_path: Path) -> None:
    _write(tmp_path / ".hidden.py", "print('hidden')\n")
    _write(tmp_path / "visible.py", "print('visible')\n")

    default_cfg = RepoLoadConfig()
    files_default = [f.rel_path for f in iter_repo_files(tmp_path, default_cfg)]
    assert ".hidden.py" not in files_default
    assert "visible.py" in files_default

    include_hidden_cfg = RepoLoadConfig(include_hidden_files=True)
    files_with_hidden = [
        f.rel_path for f in iter_repo_files(tmp_path, include_hidden_cfg)
    ]
    assert ".hidden.py" in files_with_hidden
    assert "visible.py" in files_with_hidden
