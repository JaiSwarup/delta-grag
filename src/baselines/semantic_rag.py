"""
Dependency-light semantic RAG baseline.

The roadmap target is FAISS + code embeddings. This module provides the same
retrieval boundary without introducing heavyweight runtime dependencies yet:
functions are embedded as deterministic token-frequency vectors and ranked by
cosine similarity. A FAISS-backed implementation can replace `SemanticIndex`
behind this API later.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from src.ast_extractor import FunctionNode


@dataclass(frozen=True)
class SemanticRetrievalResult:
    query: str
    retrieved: list[tuple[str, float]]
    top_k: int
    query_tokens: int


@dataclass(frozen=True)
class SemanticIndex:
    vectors: dict[str, dict[str, float]] = field(default_factory=dict)
    metadata: dict[str, dict[str, str | int]] = field(default_factory=dict)

    def save_json(self, path: str | Path) -> None:
        output_path = Path(path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "vectors": self.vectors,
            "metadata": self.metadata,
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_semantic_index(
    functions: Iterable[FunctionNode],
    *,
    save_path: str | Path | None = None,
) -> SemanticIndex:
    vectors: dict[str, dict[str, float]] = {}
    metadata: dict[str, dict[str, str | int]] = {}

    for function in functions:
        text = f"{function.fqn}\n{' '.join(function.params)}\n{function.source_code}"
        vectors[function.fqn] = _normalize_vector(_token_counts(text))
        metadata[function.fqn] = {
            "file_path": str(function.file_path),
            "start_line": function.start_line,
            "end_line": function.end_line,
        }

    index = SemanticIndex(vectors=vectors, metadata=metadata)
    if save_path is not None:
        index.save_json(save_path)
    return index


def load_semantic_index(path: str | Path) -> SemanticIndex:
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    return SemanticIndex(
        vectors={
            str(fqn): {str(token): float(weight) for token, weight in vector.items()}
            for fqn, vector in dict(payload.get("vectors", {})).items()
        },
        metadata={
            str(fqn): dict(meta)
            for fqn, meta in dict(payload.get("metadata", {})).items()
        },
    )


def semantic_retrieve(
    query: str,
    index: SemanticIndex,
    *,
    top_k: int = 10,
) -> SemanticRetrievalResult:
    if top_k < 1:
        raise ValueError("top_k must be >= 1")

    query_counts = _token_counts(query)
    query_vector = _normalize_vector(query_counts)
    if not index.vectors or not query_vector:
        return SemanticRetrievalResult(
            query=query,
            retrieved=[],
            top_k=top_k,
            query_tokens=sum(query_counts.values()),
        )

    scored = [
        (fqn, _cosine(query_vector, vector))
        for fqn, vector in index.vectors.items()
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return SemanticRetrievalResult(
        query=query,
        retrieved=scored[:top_k],
        top_k=top_k,
        query_tokens=sum(query_counts.values()),
    )


def _token_counts(text: str) -> Counter[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text.lower())
    return Counter(tokens)


def _normalize_vector(counts: Mapping[str, int]) -> dict[str, float]:
    norm = math.sqrt(sum(value * value for value in counts.values()))
    if norm == 0:
        return {}
    return {token: value / norm for token, value in counts.items()}


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(weight * right.get(token, 0.0) for token, weight in left.items())


__all__ = [
    "SemanticIndex",
    "SemanticRetrievalResult",
    "build_semantic_index",
    "load_semantic_index",
    "semantic_retrieve",
]
