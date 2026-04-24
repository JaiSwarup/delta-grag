from __future__ import annotations

import asyncio
from pathlib import Path

from src.ast_extractor import extract_functions
from src.baselines.file_context_reviewer import build_file_context, file_context_review
from src.file_indexer import build_file_index
from src.ingestion.diff_parser import parse_unified_diff
from src.llm_caller import LLMCallerConfig


class _AsyncStubProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def generate(
        self,
        *,
        prompt: str,
        model_name: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        self.prompts.append(prompt)
        return self.response


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_file_context_includes_changed_files(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "def run():\n    return 1\n")
    diff = parse_unified_diff(
        """\
diff --git a/a.py b/a.py
index 1..2 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 def run():
-    return 0
+    return 1
"""
    )
    file_index = build_file_index(tmp_path)
    functions = extract_functions(tmp_path / "a.py")

    context, included, truncated = build_file_context(
        diff=diff,
        file_index=file_index,
        function_nodes=functions,
        token_budget=100,
    )

    assert "### FILE: a.py" in context
    assert Path("a.py") in included
    assert truncated == []


def test_build_file_context_falls_back_to_modified_functions_when_full_file_too_large(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "a.py",
        "def changed():\n"
        "    return 1\n"
        "\n"
        "def unrelated():\n"
        + "\n".join(f"    value_{idx} = {idx}" for idx in range(40))
        + "\n    return value_39\n",
    )
    diff = parse_unified_diff(
        """\
diff --git a/a.py b/a.py
index 1..2 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 def changed():
-    return 0
+    return 1
"""
    )
    file_index = build_file_index(tmp_path)
    functions = extract_functions(tmp_path / "a.py")

    context, included, truncated = build_file_context(
        diff=diff,
        file_index=file_index,
        function_nodes=functions,
        token_budget=80,
    )

    assert "modified functions only" in context
    assert "def changed()" in context
    assert "def unrelated()" not in context
    assert Path("a.py") in included
    assert Path("a.py") in truncated


def test_file_context_review_invokes_provider_and_parses_output(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "def run():\n    return 1\n")
    diff_text = """\
diff --git a/a.py b/a.py
index 1..2 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 def run():
-    return 0
+    return 1
"""
    provider = _AsyncStubProvider('{"findings":[],"overall_risk":"low"}')
    config = LLMCallerConfig(model_name="stub-model", max_attempts=1)

    result = asyncio.run(
        file_context_review(
            diff=parse_unified_diff(diff_text),
            diff_text=diff_text,
            file_index=build_file_index(tmp_path),
            function_nodes=extract_functions(tmp_path / "a.py"),
            pr_metadata={"title": "Update run"},
            provider=provider,
            config=config,
            token_budget=100,
        )
    )

    assert provider.prompts
    assert "[FILE CONTEXT]" in result.prompt
    assert result.review.review.findings == ()
    assert result.included_files == [Path("a.py")]
