from __future__ import annotations

from pathlib import Path

from src.file_indexer import build_file_index


def _write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_build_file_index_filters_extensions_and_counts_loc(tmp_path: Path) -> None:
    _write_text(tmp_path / "a.py", "def a():\n    return 1\n")
    _write_text(tmp_path / "pkg" / "b.py", "x = 1\n\n\ny = 2\n")
    _write_text(tmp_path / "pkg" / "skip.js", "console.log('x')\n")

    index = build_file_index(tmp_path, include_extensions=[".py"])

    assert sorted(index.files) == ["a.py", "pkg/b.py"]
    assert index.files["a.py"].loc == 2
    assert index.files["pkg/b.py"].loc == 2
    assert all(path.endswith(".py") for path in index.files)


def test_build_file_index_accepts_extension_without_dot(tmp_path: Path) -> None:
    _write_text(tmp_path / "app.py", "print('ok')\n")
    _write_text(tmp_path / "notes.txt", "hello\n")

    index = build_file_index(tmp_path, include_extensions=["py"])

    assert sorted(index.files) == ["app.py"]


def test_build_file_index_skips_binary_and_oversized_files(tmp_path: Path) -> None:
    _write_bytes(tmp_path / "bin.py", b"\x00\x01\x02")
    _write_text(tmp_path / "big.py", "a" * 200)
    _write_text(tmp_path / "ok.py", "value = 1\n")

    index = build_file_index(
        tmp_path,
        include_extensions=[".py"],
        max_file_bytes=100,
    )

    assert sorted(index.files) == ["ok.py"]


def test_build_file_index_keeps_latin_1_text_files_parseable(tmp_path: Path) -> None:
    _write_text(tmp_path / "latin.py", "name = 'caf\xe9'\n", encoding="latin-1")

    index = build_file_index(tmp_path, include_extensions=[".py"])
    metadata = index.files["latin.py"]

    assert metadata.encoding == "latin-1"
    assert metadata.loc == 1
    assert metadata.is_parseable is True


def test_file_index_get_python_files_returns_metadata_list(tmp_path: Path) -> None:
    _write_text(tmp_path / "pkg" / "mod.py", "def run():\n    return 1\n")

    index = build_file_index(tmp_path, include_extensions=[".py"])
    python_files = index.get_python_files()

    assert len(python_files) == 1
    assert python_files[0].path.as_posix() == "pkg/mod.py"
