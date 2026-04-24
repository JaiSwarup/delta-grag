"""Linearization package for Delta-GRAG context serialization."""

from .bfs_linearizer import DEFAULT_MAX_CHARS, linearize_subgraph

__all__ = ["linearize_subgraph", "DEFAULT_MAX_CHARS"]
