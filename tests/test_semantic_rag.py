from __future__ import annotations

from pathlib import Path

from src.ast_extractor import extract_functions
from src.baselines.semantic_rag import (
    build_semantic_index,
    load_semantic_index,
    semantic_retrieve,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_semantic_retrieve_returns_relevant_function_for_query(tmp_path: Path) -> None:
    file_path = tmp_path / "service.py"
    _write(
        file_path,
        "def parse_user_token(raw_token):\n"
        "    return raw_token.strip()\n"
        "\n"
        "def render_invoice_pdf(invoice):\n"
        "    return invoice.total\n",
    )

    functions = extract_functions(file_path)
    index = build_semantic_index(functions)
    result = semantic_retrieve("token parsing strips raw token", index, top_k=1)

    assert result.retrieved
    assert result.retrieved[0][0] == "parse_user_token"
    assert result.query_tokens > 0


def test_semantic_index_json_round_trip(tmp_path: Path) -> None:
    file_path = tmp_path / "service.py"
    _write(file_path, "def compute_total(invoice):\n    return invoice.total\n")

    functions = extract_functions(file_path)
    index_path = tmp_path / "semantic_index.json"

    index = build_semantic_index(functions, save_path=index_path)
    loaded = load_semantic_index(index_path)

    assert loaded.vectors == index.vectors
    assert loaded.metadata == index.metadata


def test_semantic_retrieve_handles_empty_index() -> None:
    index = build_semantic_index([])

    result = semantic_retrieve("anything", index, top_k=5)

    assert result.retrieved == []
    assert result.top_k == 5


def test_semantic_retrieve_validates_top_k(tmp_path: Path) -> None:
    index = build_semantic_index([])

    try:
        semantic_retrieve("query", index, top_k=0)
        assert False, "Expected ValueError for top_k=0"
    except ValueError:
        pass
