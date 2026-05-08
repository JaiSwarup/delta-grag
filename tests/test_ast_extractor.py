from __future__ import annotations

from pathlib import Path

from src.ast_extractor import extract_functions


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_extract_functions_top_level_function(tmp_path: Path) -> None:
    file_path = tmp_path / "mod.py"
    _write(file_path, "def run(x, y):\n    return x + y\n")

    functions = extract_functions(file_path)

    assert [fn.fqn for fn in functions] == ["run"]
    assert functions[0].params == ["x", "y"]
    assert functions[0].is_method is False
    assert functions[0].source_code.startswith("def run")


def test_extract_functions_class_method_and_nested_function(tmp_path: Path) -> None:
    file_path = tmp_path / "mod.py"
    _write(
        file_path,
        "class Service:\n"
        "    def handle(self, value):\n"
        "        def inner(flag):\n"
        "            return flag\n"
        "        return inner(value)\n",
    )

    functions = extract_functions(file_path)
    fqns = [fn.fqn for fn in functions]

    assert fqns == ["Service.handle", "Service.handle.inner"]

    method = functions[0]
    nested = functions[1]

    assert method.is_method is True
    assert method.class_name == "Service"
    assert method.params == ["self", "value"]

    assert nested.is_nested is True
    assert nested.is_method is True
    assert nested.class_name == "Service"
    assert nested.params == ["flag"]


def test_extract_functions_lambda_assignment_is_captured_as_named_callable(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "lambda_case.py"
    _write(file_path, "def run():\n    util = lambda x: x + 1\n    return util(1)\n")

    functions = extract_functions(file_path)
    fqns = [fn.fqn for fn in functions]

    assert fqns == ["run", "run.util"]
    assert functions[1].is_lambda is True
    assert functions[1].params == ["x"]


def test_extract_functions_supports_async_functions(tmp_path: Path) -> None:
    file_path = tmp_path / "async_mod.py"
    _write(file_path, "async def fetch(client, url):\n    return await client.get(url)\n")

    functions = extract_functions(file_path)

    assert [fn.fqn for fn in functions] == ["fetch"]
    assert functions[0].params == ["client", "url"]
    assert functions[0].start_line < functions[0].end_line


def test_extract_functions_accepts_single_line_function(tmp_path: Path) -> None:
    file_path = tmp_path / "single_line.py"
    _write(file_path, "def with_yield(): yield 1\n")

    functions = extract_functions(file_path)

    assert [fn.fqn for fn in functions] == ["with_yield"]
    assert functions[0].start_line == functions[0].end_line == 1
