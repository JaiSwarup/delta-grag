from __future__ import annotations

from pathlib import Path

from src.ast_extractor import extract_functions
from src.call_extractor import build_import_map, extract_call_edges


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_extract_call_edges_resolves_direct_local_calls(tmp_path: Path) -> None:
    file_path = tmp_path / "mod.py"
    _write(
        file_path,
        "def util():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    return util()\n",
    )

    functions = extract_functions(file_path)
    edges = extract_call_edges(file_path, all_functions=functions)

    assert len(edges) == 1
    assert edges[0].caller_fqn == "run"
    assert edges[0].callee_fqn == "util"
    assert edges[0].is_resolved is True
    assert edges[0].resolution_method == "direct"


def test_extract_call_edges_resolves_import_alias_calls(tmp_path: Path) -> None:
    helper_path = tmp_path / "helpers.py"
    caller_path = tmp_path / "caller.py"
    _write(helper_path, "def util():\n    return 1\n")
    _write(
        caller_path,
        "from helpers import util as alias\n"
        "\n"
        "def run():\n"
        "    return alias()\n",
    )

    functions = extract_functions(helper_path) + extract_functions(caller_path)
    edges = extract_call_edges(
        caller_path,
        all_functions=functions,
        import_map=build_import_map(caller_path),
    )

    assert len(edges) == 1
    assert edges[0].caller_fqn == "run"
    assert edges[0].callee_fqn == "util"
    assert edges[0].resolution_method == "import"


def test_extract_call_edges_resolves_self_method_calls(tmp_path: Path) -> None:
    file_path = tmp_path / "service.py"
    _write(
        file_path,
        "class Service:\n"
        "    def handle(self):\n"
        "        return self.helper()\n"
        "\n"
        "    def helper(self):\n"
        "        return 1\n",
    )

    functions = extract_functions(file_path)
    edges = extract_call_edges(file_path, all_functions=functions)

    assert len(edges) == 1
    assert edges[0].caller_fqn == "Service.handle"
    assert edges[0].callee_fqn == "Service.helper"
    assert edges[0].resolution_method == "self"


def test_extract_call_edges_marks_dynamic_calls_unresolved(tmp_path: Path) -> None:
    file_path = tmp_path / "dynamic.py"
    _write(
        file_path,
        "def run(obj, name):\n"
        "    target = getattr(obj, name)\n"
        "    return target()\n",
    )

    functions = extract_functions(file_path)
    edges = extract_call_edges(file_path, all_functions=functions)

    assert len(edges) == 2
    unresolved = [edge for edge in edges if edge.is_resolved is False]
    assert len(unresolved) == 2
    assert {edge.raw_callee for edge in unresolved} == {"getattr", "target"}
