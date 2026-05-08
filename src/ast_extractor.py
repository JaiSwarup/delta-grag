"""
Standalone function extraction boundary for Python source files.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class FunctionNode:
    fqn: str
    file_path: Path
    start_line: int
    end_line: int
    source_code: str
    params: list[str]
    is_method: bool
    class_name: Optional[str] = None
    is_nested: bool = False
    is_lambda: bool = False


def extract_functions(file_path: str | Path) -> list[FunctionNode]:
    path = Path(file_path).expanduser().resolve()
    source_text = path.read_text(encoding="utf-8", errors="replace")
    module = ast.parse(source_text, filename=str(path))
    return extract_functions_from_module(source_text, module, path)


def extract_functions_from_module(
    source_text: str,
    module: ast.Module,
    file_path: str | Path,
) -> list[FunctionNode]:
    """Extract functions from a pre-parsed AST module.

    This variant is used by the single-parse path in ``build_call_graph`` so
    that the source file is read and parsed only once per build.
    """
    path = Path(file_path).expanduser().resolve()
    source_lines = source_text.splitlines()

    functions: list[FunctionNode] = []
    function_stack: list[str] = []
    class_stack: list[str] = []

    def current_parent_fqn() -> str:
        if function_stack:
            return function_stack[-1]
        if class_stack:
            return ".".join(class_stack)
        return ""

    def build_fqn(name: str) -> str:
        parent = current_parent_fqn()
        return f"{parent}.{name}" if parent else name

    def build_params(node: ast.arguments) -> list[str]:
        names = [arg.arg for arg in node.posonlyargs]
        names.extend(arg.arg for arg in node.args)
        if node.vararg is not None:
            names.append(f"*{node.vararg.arg}")
        names.extend(arg.arg for arg in node.kwonlyargs)
        if node.kwarg is not None:
            names.append(f"**{node.kwarg.arg}")
        return names

    def build_source_segment(start_line: int, end_line: int) -> str:
        return "\n".join(source_lines[start_line - 1 : end_line]).strip()

    def add_function(
        *,
        name: str,
        lineno: int,
        end_lineno: int,
        params: list[str],
        is_lambda: bool,
    ) -> str:
        fqn = build_fqn(name)
        functions.append(
            FunctionNode(
                fqn=fqn,
                file_path=path,
                start_line=lineno,
                end_line=end_lineno,
                source_code=build_source_segment(lineno, end_lineno),
                params=params,
                is_method=bool(class_stack),
                class_name=class_stack[-1] if class_stack else None,
                is_nested=bool(function_stack),
                is_lambda=is_lambda,
            )
        )
        return fqn

    def visit_statements(statements: list[ast.stmt]) -> None:
        for statement in statements:
            visit_statement(statement)

    def visit_statement(statement: ast.stmt) -> None:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start_line = getattr(statement, "lineno", 1)
            end_line = getattr(statement, "end_lineno", start_line)
            fqn = add_function(
                name=statement.name,
                lineno=start_line,
                end_lineno=end_line,
                params=build_params(statement.args),
                is_lambda=False,
            )
            function_stack.append(fqn)
            visit_statements(statement.body)
            function_stack.pop()
            return

        if isinstance(statement, ast.ClassDef):
            class_stack.append(statement.name)
            visit_statements(statement.body)
            class_stack.pop()
            return

        if isinstance(statement, ast.Assign):
            if len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                target_name = statement.targets[0].id
                if isinstance(statement.value, ast.Lambda):
                    start_line = getattr(statement, "lineno", 1)
                    end_line = getattr(statement, "end_lineno", start_line)
                    lambda_fqn = add_function(
                        name=target_name,
                        lineno=start_line,
                        end_lineno=end_line,
                        params=build_params(statement.value.args),
                        is_lambda=True,
                    )
                    function_stack.append(lambda_fqn)
                    visit_expression(statement.value.body)
                    function_stack.pop()
                    return

        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                visit_statement(child)
            elif isinstance(child, ast.expr):
                visit_expression(child)

    def visit_expression(expression: ast.expr) -> None:
        for child in ast.iter_child_nodes(expression):
            if isinstance(child, ast.stmt):
                visit_statement(child)
            elif isinstance(child, ast.expr):
                visit_expression(child)

    visit_statements(module.body)
    _validate_unique_fqns(functions, path)
    return functions


__all__ = ["FunctionNode", "extract_functions", "extract_functions_from_module"]


def _validate_unique_fqns(functions: list[FunctionNode], file_path: Path) -> None:
    seen: set[str] = set()
    for function in functions:
        if function.start_line > function.end_line and not function.is_lambda:
            raise ValueError(
                f"Invalid function span for {function.fqn} in {file_path}: "
                f"{function.start_line}..{function.end_line}"
            )
        if function.fqn in seen:
            raise ValueError(f"Duplicate function FQN in {file_path}: {function.fqn}")
        seen.add(function.fqn)

