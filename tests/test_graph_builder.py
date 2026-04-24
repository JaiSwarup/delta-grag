from __future__ import annotations

from pathlib import Path

from src.graph.graph_builder import build_call_graph


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _find_node_ids_by_name(graph, name: str) -> list[str]:
    return [n for n, d in graph.nodes(data=True) if d.get("name") == name]


def test_sample_call_edge(tmp_path: Path) -> None:
    code = "def util():\n    pass\n\ndef main():\n    util()\n"
    _write(tmp_path / "sample.py", code)

    g = build_call_graph(tmp_path)

    main_ids = _find_node_ids_by_name(g, "main")
    util_ids = _find_node_ids_by_name(g, "util")

    assert main_ids, "Expected at least one 'main' node"
    assert util_ids, "Expected at least one 'util' node"
    assert any(g.has_edge(m, u) for m in main_ids for u in util_ids), (
        "Expected edge main -> util"
    )


def test_nested_function_call_edge(tmp_path: Path) -> None:
    code = "def outer():\n    def inner():\n        pass\n    inner()\n"
    _write(tmp_path / "nested.py", code)

    g = build_call_graph(tmp_path)

    outer_ids = _find_node_ids_by_name(g, "outer")
    inner_ids = _find_node_ids_by_name(g, "inner")

    assert outer_ids, "Expected at least one 'outer' node"
    assert inner_ids, "Expected at least one 'inner' node"

    assert any(g.has_edge(o, i) for o in outer_ids for i in inner_ids), (
        "Expected edge outer -> inner for nested call"
    )


def test_lambda_assignment_and_call_edge(tmp_path: Path) -> None:
    code = "def run():\n    util = lambda x: x + 1\n    return util(1)\n"
    _write(tmp_path / "lambda_case.py", code)

    g = build_call_graph(tmp_path)

    run_ids = _find_node_ids_by_name(g, "run")
    util_ids = _find_node_ids_by_name(g, "util")

    assert run_ids, "Expected at least one 'run' node"
    assert util_ids, "Expected lambda-assigned symbol node named 'util'"

    # Lambda assignment call should resolve as run -> util in current static model.
    assert any(g.has_edge(r, u) for r in run_ids for u in util_ids), (
        "Expected edge run -> util for lambda assignment call"
    )


def test_from_import_alias_call_resolves_to_local_symbol(tmp_path: Path) -> None:
    util_code = "def util():\n    return 1\n"
    caller_code = "from util_mod import util as u\n\ndef run():\n    return u()\n"
    _write(tmp_path / "util_mod.py", util_code)
    _write(tmp_path / "caller.py", caller_code)

    g = build_call_graph(tmp_path)

    run_ids = _find_node_ids_by_name(g, "run")
    util_ids = [n for n, d in g.nodes(data=True) if d.get("qualified_name") == "util"]

    assert run_ids, "Expected at least one 'run' node"
    assert util_ids, "Expected imported target symbol 'util' in util_mod.py"
    assert any(g.has_edge(r, u) for r in run_ids for u in util_ids), (
        "Expected edge run -> util for from-import alias call"
    )


def test_self_method_call_resolves_with_class_qualified_symbols(tmp_path: Path) -> None:
    code = (
        "class C:\n"
        "    def a(self):\n"
        "        return self.b()\n"
        "\n"
        "    def b(self):\n"
        "        return 1\n"
    )
    _write(tmp_path / "cmod.py", code)

    g = build_call_graph(tmp_path)
    a_ids = [n for n, d in g.nodes(data=True) if d.get("qualified_name") == "C.a"]
    b_ids = [n for n, d in g.nodes(data=True) if d.get("qualified_name") == "C.b"]

    assert a_ids, "Expected class-qualified symbol C.a"
    assert b_ids, "Expected class-qualified symbol C.b"
    assert any(g.has_edge(a, b) for a in a_ids for b in b_ids), (
        "Expected edge C.a -> C.b for self method call"
    )


def test_from_imported_module_alias_dotted_call_resolves(tmp_path: Path) -> None:
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "helpers.py", "def util():\n    return 1\n")
    _write(
        tmp_path / "caller.py",
        "from pkg import helpers as h\n\ndef run():\n    return h.util()\n",
    )

    g = build_call_graph(tmp_path)
    run_ids = [n for n, d in g.nodes(data=True) if d.get("qualified_name") == "run"]
    util_ids = [n for n, d in g.nodes(data=True) if d.get("qualified_name") == "util"]

    assert run_ids, "Expected run symbol"
    assert util_ids, "Expected util symbol"
    assert any(g.has_edge(r, u) for r in run_ids for u in util_ids), (
        "Expected edge run -> util for from-imported module alias dotted call"
    )
