"""
Standalone call-edge extraction and conservative intra-repo resolution.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.ast_extractor import FunctionNode, extract_functions

ResolutionMethod = Literal["direct", "import", "self", "unresolved"]


@dataclass(frozen=True)
class CallEdge:
    caller_fqn: str
    callee_fqn: str
    call_site_line: int
    is_resolved: bool
    resolution_method: ResolutionMethod
    raw_callee: str


def extract_call_edges(
    file_path: str | Path,
    *,
    all_functions: list[FunctionNode],
    import_map: dict[str, str] | None = None,
) -> list[CallEdge]:
    path = Path(file_path).expanduser().resolve()
    source_text = path.read_text(encoding="utf-8", errors="replace")
    module = ast.parse(source_text, filename=str(path))

    functions = [fn for fn in all_functions if fn.file_path.resolve() == path]
    if not functions:
        return []

    by_fqn = {fn.fqn: fn for fn in all_functions}
    simple_name_to_fqns: dict[str, list[str]] = {}
    for function in all_functions:
        simple_name = function.fqn.rsplit(".", 1)[-1]
        simple_name_to_fqns.setdefault(simple_name, []).append(function.fqn)

    local_function_names = {
        function.fqn: function.fqn.rsplit(".", 1)[-1] for function in functions
    }
    import_aliases = import_map if import_map is not None else build_import_map(path)
    function_nodes_by_line = sorted(
        functions, key=lambda fn: (fn.start_line, fn.end_line)
    )

    edges: list[CallEdge] = []

    def enclosing_function(line: int) -> FunctionNode | None:
        matches = [
            function
            for function in function_nodes_by_line
            if function.start_line <= line <= function.end_line
        ]
        if not matches:
            return None
        return min(
            matches, key=lambda fn: (fn.end_line - fn.start_line, -fn.start_line)
        )

    def visit_expression(expression: ast.expr) -> None:
        if isinstance(expression, ast.Call):
            caller = enclosing_function(getattr(expression, "lineno", 1))
            if caller is not None:
                raw_callee = _expr_to_text(expression.func)
                callee_fqn, method = _resolve_callee(
                    raw_callee=raw_callee,
                    caller=caller,
                    all_functions_by_fqn=by_fqn,
                    simple_name_to_fqns=simple_name_to_fqns,
                    local_function_names=local_function_names,
                    import_aliases=import_aliases,
                )
                edges.append(
                    CallEdge(
                        caller_fqn=caller.fqn,
                        callee_fqn=callee_fqn,
                        call_site_line=getattr(expression, "lineno", 1),
                        is_resolved=method != "unresolved",
                        resolution_method=method,
                        raw_callee=raw_callee,
                    )
                )
            for child in ast.iter_child_nodes(expression):
                if isinstance(child, ast.expr):
                    visit_expression(child)
            return

        for child in ast.iter_child_nodes(expression):
            if isinstance(child, ast.expr):
                visit_expression(child)

    def visit_statement(statement: ast.stmt) -> None:
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                visit_statement(child)
            elif isinstance(child, ast.expr):
                visit_expression(child)

    for statement in module.body:
        visit_statement(statement)

    return edges


def build_import_map(file_path: str | Path) -> dict[str, str]:
    path = Path(file_path).expanduser().resolve()
    source_text = path.read_text(encoding="utf-8", errors="replace")
    module = ast.parse(source_text, filename=str(path))
    current_module = _module_name_from_path(path)

    aliases: dict[str, str] = {}
    for statement in module.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                local_name = alias.asname or alias.name.split(".")[-1]
                aliases[local_name] = alias.name
        elif isinstance(statement, ast.ImportFrom):
            module_name = _normalize_relative_module(
                current_module=current_module,
                level=statement.level,
                module_name=statement.module,
            )
            if not module_name:
                continue
            for alias in statement.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                aliases[local_name] = f"{module_name}.{alias.name}"
    return aliases


def extract_call_edges_for_repo(repo_root: str | Path) -> list[CallEdge]:
    root = Path(repo_root).expanduser().resolve()
    functions: list[FunctionNode] = []
    python_files = sorted(root.rglob("*.py"))
    for file_path in python_files:
        functions.extend(extract_functions(file_path))

    edges: list[CallEdge] = []
    for file_path in python_files:
        edges.extend(
            extract_call_edges(
                file_path,
                all_functions=functions,
                import_map=build_import_map(file_path),
            )
        )
    return edges


def _resolve_callee(
    *,
    raw_callee: str,
    caller: FunctionNode,
    all_functions_by_fqn: dict[str, FunctionNode],
    simple_name_to_fqns: dict[str, list[str]],
    local_function_names: dict[str, str],
    import_aliases: dict[str, str],
) -> tuple[str, ResolutionMethod]:
    if not raw_callee:
        return raw_callee, "unresolved"

    if "." not in raw_callee:
        caller_scope_prefix = caller.fqn.rsplit(".", 1)[0] if "." in caller.fqn else ""

        same_scope_fqn = (
            f"{caller_scope_prefix}.{raw_callee}" if caller_scope_prefix else raw_callee
        )
        if same_scope_fqn in all_functions_by_fqn and same_scope_fqn != caller.fqn:
            return same_scope_fqn, "direct"

        if raw_callee in import_aliases:
            target = import_aliases[raw_callee]
            if target in all_functions_by_fqn:
                return target, "import"
            imported_name = target.rsplit(".", 1)[-1]
            imported_matches = simple_name_to_fqns.get(imported_name, [])
            if len(imported_matches) == 1:
                return imported_matches[0], "import"

        local_matches = [
            fqn
            for fqn in simple_name_to_fqns.get(raw_callee, [])
            if all_functions_by_fqn[fqn].file_path.resolve()
            == caller.file_path.resolve()
        ]
        if len(local_matches) == 1:
            return local_matches[0], "direct"
        if len(local_matches) > 1:
            return raw_callee, "unresolved"

        global_matches = simple_name_to_fqns.get(raw_callee, [])
        if len(global_matches) == 1:
            return global_matches[0], "direct"

        return raw_callee, "unresolved"

    head, tail = raw_callee.split(".", 1)

    if head in {"self", "cls"} and caller.class_name:
        candidate = f"{caller.class_name}.{tail}"
        if candidate in all_functions_by_fqn:
            return candidate, "self"
        return raw_callee, "unresolved"

    if head in import_aliases:
        candidate = f"{import_aliases[head]}.{tail}"
        if candidate in all_functions_by_fqn:
            return candidate, "import"
        imported_name = candidate.rsplit(".", 1)[-1]
        imported_matches = simple_name_to_fqns.get(imported_name, [])
        if len(imported_matches) == 1:
            return imported_matches[0], "import"
        return raw_callee, "unresolved"

    return raw_callee, "unresolved"


def _expr_to_text(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _expr_to_text(expr.value)
        return f"{base}.{expr.attr}" if base else expr.attr
    return ""


def _module_name_from_path(file_path: Path) -> str:
    stem = file_path.with_suffix("")
    parts = list(stem.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _normalize_relative_module(
    *,
    current_module: str,
    level: int,
    module_name: str | None,
) -> str:
    if level <= 0:
        return module_name or ""
    current_parts = current_module.split(".") if current_module else []
    package_parts = current_parts[:-1] if current_parts else []
    base_parts = package_parts[: max(0, len(package_parts) - (level - 1))]
    if module_name:
        base_parts.extend(module_name.split("."))
    return ".".join(part for part in base_parts if part)


__all__ = [
    "CallEdge",
    "build_import_map",
    "extract_call_edges",
    "extract_call_edges_for_repo",
]
