"""
Top-level module entry point for the D-GRAG CLI.

This enables invocation like:
    python -m dgrag --help
    python -m dgrag review --pr-url https://github.com/org/repo/pull/123
"""

from __future__ import annotations

from src.cli import main

if __name__ == "__main__":
    main()
