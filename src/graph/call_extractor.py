"""
Static Python symbol and call extraction for intra-repo call graph construction.

This module:
- Parses Python files with Tree-sitter.
- Extracts function symbols (top-level + nested) and lambda-assigned function symbols.
- Extracts call sites in function/lambda scopes.
- Resolves static intra-repo edges with conservative rules.

Ignored by design:
- Dynamic dispatch (obj.method() where obj is runtime instance)
- Runtime-generated callables
- External/unresolved targets
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from tree_sitter import Language, Node, Parser

# =========================
# Data models
# =========================


@dataclass(frozen=True)
class FunctionSymbol:
    symbol_id: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    is_nested: bool = False
    is_lambda: bool = False


@dataclass(frozen=True)
class ImportAlias:
    file_path: str
    local_name: str
    source_module: str
    source_name: Optional[str] = (
        None  # None for `import x as y`, set for `from m import f as g`
    )


@dataclass(frozen=True)
class CallSite:
    file_path: str
    caller_symbol_id: str
    callee_expr_text: str
    line: int


@dataclass
class FileExtraction:
    file_path: str
    symbols: List[FunctionSymbol] = field(default_factory=list)
    imports: List[ImportAlias] = field(default_factory=list)
    calls: List[CallSite] = field(default_factory=list)
    local_defs_by_name: Dict[str, List[str]] = field(
        default_factory=dict
    )  # simple name -> symbol_ids in file


@dataclass
class RepoExtraction:
    files: List[FileExtraction] = field(default_factory=list)

    def all_symbols(self) -> List[FunctionSymbol]:
        out: List[FunctionSymbol] = []
        for f in self.files:
            out.extend(f.symbols)
        return out

    def all_imports(self) -> List[ImportAlias]:
        out: List[ImportAlias] = []
        for f in self.files:
            out.extend(f.imports)
        return out

    def all_calls(self) -> List[CallSite]:
        out: List[CallSite] = []
        for f in self.files:
            out.extend(f.calls)
        return out


# =========================
# Parser setup
# =========================


def build_parser() -> Parser:
    """
    Build Tree-sitter parser for Python.
    Requires `tree_sitter_python` package.
    """
    try:
        import tree_sitter_python as tspython  # type: ignore
    except ImportError:
        return None

    parser = Parser()
    py_capsule = tspython.language()

    # Compatibility across bindings
    try:
        parser.language = Language(py_capsule)
    except Exception:
        return None

    return parser


# =========================
# Public extraction API
# =========================


def extract_repo(repo_root: Path, parser: Optional[Parser] = None) -> RepoExtraction:
    parser = parser or build_parser()
    files: List[FileExtraction] = []

    for py_file in iter_python_files(repo_root):
        if parser is not None:
            files.append(extract_file(py_file, repo_root, parser))
        else:
            files.append(extract_file_ast(py_file, repo_root))

    return RepoExtraction(files=files)


def extract_file(file_path: Path, repo_root: Path, parser: Parser) -> FileExtraction:
    source = file_path.read_bytes()
    tree = parser.parse(source)
    root = tree.root_node

    rel_path = str(file_path.relative_to(repo_root)).replace("\\", "/")
    extraction = FileExtraction(file_path=rel_path)
    current_module = module_name_from_path(rel_path)

    # Stack of active caller scopes: (qualified_name, symbol_id)
    scope_stack: List[Tuple[str, str]] = []
    class_stack: List[str] = []
    lambda_counter = 0

    # Per-scope local function bindings for lexical name resolution
    local_bindings_stack: List[Dict[str, str]] = [{}]

    def push_bindings() -> None:
        local_bindings_stack.append({})

    def pop_bindings() -> None:
        local_bindings_stack.pop()

    def bind_local_name(name: str, symbol_id: str) -> None:
        local_bindings_stack[-1][name] = symbol_id
        extraction.local_defs_by_name.setdefault(name, []).append(symbol_id)

    def current_caller_symbol_id() -> Optional[str]:
        if not scope_stack:
            return None
        return scope_stack[-1][1]

    def walk(node: Node) -> None:
        nonlocal lambda_counter

        ntype = node.type

        if ntype in ("import_statement", "import_from_statement"):
            extraction.imports.extend(
                _extract_import_aliases(node, rel_path, current_module)
            )
            # still traverse children
            for ch in node.children:
                walk(ch)
            return

        if ntype == "class_definition":
            class_name_node = node.child_by_field_name("name")
            if class_name_node is not None:
                class_name = _node_text(class_name_node, source)
                class_stack.append(class_name)
                body = node.child_by_field_name("body")
                if body is not None:
                    walk(body)
                class_stack.pop()
                return

        if ntype == "function_definition":
            fn_name_node = node.child_by_field_name("name")
            if fn_name_node is None:
                return

            fn_name = _node_text(fn_name_node, source)
            if scope_stack:
                parent_qn = scope_stack[-1][0]
            else:
                parent_qn = ".".join(class_stack)
            qn = f"{parent_qn}.{fn_name}" if parent_qn else fn_name

            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            sid = _symbol_id(rel_path, qn, start)

            sym = FunctionSymbol(
                symbol_id=sid,
                name=fn_name,
                qualified_name=qn,
                file_path=rel_path,
                start_line=start,
                end_line=end,
                is_nested=bool(scope_stack),
                is_lambda=False,
            )
            extraction.symbols.append(sym)
            bind_local_name(fn_name, sid)

            scope_stack.append((qn, sid))
            push_bindings()

            body = node.child_by_field_name("body")
            if body is not None:
                walk(body)

            pop_bindings()
            scope_stack.pop()
            return

        # Lambda assignment support: foo = lambda x: ...
        if ntype == "assignment":
            lhs_name = _extract_assignment_name(node, source)
            rhs = node.child_by_field_name("right")
            if lhs_name and rhs is not None and rhs.type == "lambda":
                if scope_stack:
                    parent_qn = scope_stack[-1][0]
                else:
                    parent_qn = ".".join(class_stack)
                qn = f"{parent_qn}.{lhs_name}" if parent_qn else lhs_name

                start = node.start_point[0] + 1
                end = node.end_point[0] + 1
                sid = _symbol_id(rel_path, qn, start)

                sym = FunctionSymbol(
                    symbol_id=sid,
                    name=lhs_name,
                    qualified_name=qn,
                    file_path=rel_path,
                    start_line=start,
                    end_line=end,
                    is_nested=bool(scope_stack),
                    is_lambda=True,
                )
                extraction.symbols.append(sym)
                bind_local_name(lhs_name, sid)

                # Treat lambda body as new callable scope
                scope_stack.append((qn, sid))
                push_bindings()
                walk(rhs)
                pop_bindings()
                scope_stack.pop()
                return

        # Explicit lambda expression in place (nested unnamed lambda)
        if ntype == "lambda":
            lambda_counter += 1
            synthetic_name = f"<lambda_{lambda_counter}>"
            if scope_stack:
                parent_qn = scope_stack[-1][0]
            else:
                parent_qn = ".".join(class_stack)
            qn = f"{parent_qn}.{synthetic_name}" if parent_qn else synthetic_name

            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            sid = _symbol_id(rel_path, qn, start)

            sym = FunctionSymbol(
                symbol_id=sid,
                name=synthetic_name,
                qualified_name=qn,
                file_path=rel_path,
                start_line=start,
                end_line=end,
                is_nested=bool(scope_stack),
                is_lambda=True,
            )
            extraction.symbols.append(sym)

            scope_stack.append((qn, sid))
            push_bindings()
            for ch in node.children:
                walk(ch)
            pop_bindings()
            scope_stack.pop()
            return

        if ntype == "call":
            caller = current_caller_symbol_id()
            if caller:
                func_node = node.child_by_field_name("function")
                callee_expr = (
                    _node_text(func_node, source) if func_node is not None else ""
                )
                extraction.calls.append(
                    CallSite(
                        file_path=rel_path,
                        caller_symbol_id=caller,
                        callee_expr_text=callee_expr,
                        line=node.start_point[0] + 1,
                    )
                )

        for ch in node.children:
            walk(ch)

    walk(root)
    return extraction


def extract_file_ast(file_path: Path, repo_root: Path) -> FileExtraction:
    source_text = file_path.read_text(encoding="utf-8", errors="replace")
    rel_path = str(file_path.relative_to(repo_root)).replace("\\", "/")
    extraction = FileExtraction(file_path=rel_path)
    current_module = module_name_from_path(rel_path)

    module = ast.parse(source_text, filename=str(file_path))
    scope_stack: List[Tuple[str, str]] = []
    class_stack: List[str] = []
    local_bindings_stack: List[Dict[str, str]] = [{}]
    lambda_counter = 0

    def bind_local_name(name: str, symbol_id: str) -> None:
        local_bindings_stack[-1][name] = symbol_id
        extraction.local_defs_by_name.setdefault(name, []).append(symbol_id)

    def current_caller_symbol_id() -> Optional[str]:
        if not scope_stack:
            return None
        return scope_stack[-1][1]

    def push_scope(qn: str, sid: str) -> None:
        scope_stack.append((qn, sid))
        local_bindings_stack.append({})

    def pop_scope() -> None:
        scope_stack.pop()
        local_bindings_stack.pop()

    def qn_for(name: str) -> str:
        if scope_stack:
            parent_qn = scope_stack[-1][0]
        else:
            parent_qn = ".".join(class_stack)
        return f"{parent_qn}.{name}" if parent_qn else name

    def add_symbol(name: str, lineno: int, end_lineno: int, is_lambda: bool) -> str:
        qn = qn_for(name)
        sid = _symbol_id(rel_path, qn, lineno)
        extraction.symbols.append(
            FunctionSymbol(
                symbol_id=sid,
                name=name,
                qualified_name=qn,
                file_path=rel_path,
                start_line=lineno,
                end_line=end_lineno,
                is_nested=bool(scope_stack),
                is_lambda=is_lambda,
            )
        )
        bind_local_name(name, sid)
        return sid

    def resolve_direct_name(name: str) -> str:
        for frame in reversed(local_bindings_stack):
            if name in frame:
                return name
        return name

    def visit_statements(stmts: List[ast.stmt]) -> None:
        for stmt in stmts:
            visit_stmt(stmt)

    def visit_stmt(stmt: ast.stmt) -> None:
        nonlocal lambda_counter

        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                source_module = str(alias.name or "").strip()
                if not source_module:
                    continue
                local_name = str(alias.asname or source_module.split(".")[-1]).strip()
                if not local_name:
                    continue
                extraction.imports.append(
                    ImportAlias(
                        file_path=rel_path,
                        local_name=local_name,
                        source_module=source_module,
                        source_name=None,
                    )
                )
            return

        if isinstance(stmt, ast.ImportFrom):
            raw_module = "." * int(getattr(stmt, "level", 0) or 0)
            if getattr(stmt, "module", None):
                raw_module += str(stmt.module)
            source_module = _normalize_relative_module(current_module, raw_module)
            if source_module:
                for alias in stmt.names:
                    source_name = str(alias.name or "").strip()
                    if not source_name or source_name == "*":
                        continue
                    local_name = str(alias.asname or source_name).strip()
                    if not local_name:
                        continue
                    extraction.imports.append(
                        ImportAlias(
                            file_path=rel_path,
                            local_name=local_name,
                            source_module=source_module,
                            source_name=source_name,
                        )
                    )
            return

        if isinstance(stmt, ast.FunctionDef):
            start = getattr(stmt, "lineno", 1)
            end = getattr(stmt, "end_lineno", start)
            sid = add_symbol(stmt.name, start, end, is_lambda=False)
            push_scope(qn_for(stmt.name), sid)
            visit_statements(stmt.body)
            pop_scope()
            return

        if isinstance(stmt, ast.AsyncFunctionDef):
            start = getattr(stmt, "lineno", 1)
            end = getattr(stmt, "end_lineno", start)
            sid = add_symbol(stmt.name, start, end, is_lambda=False)
            push_scope(qn_for(stmt.name), sid)
            visit_statements(stmt.body)
            pop_scope()
            return

        if isinstance(stmt, ast.ClassDef):
            class_stack.append(stmt.name)
            visit_statements(stmt.body)
            class_stack.pop()
            return

        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                lhs = stmt.targets[0].id
                if isinstance(stmt.value, ast.Lambda):
                    start = getattr(stmt, "lineno", 1)
                    end = getattr(stmt, "end_lineno", start)
                    sid = add_symbol(lhs, start, end, is_lambda=True)
                    push_scope(qn_for(lhs), sid)
                    visit_expr(stmt.value.body)
                    pop_scope()
                    return

        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.expr):
                visit_expr(child)
            elif isinstance(child, ast.stmt):
                visit_stmt(child)

    def visit_expr(expr: ast.expr) -> None:
        nonlocal lambda_counter

        if isinstance(expr, ast.Call):
            caller = current_caller_symbol_id()
            if caller is not None:
                callee_expr = _expr_to_callee_text(expr.func)
                line = getattr(expr, "lineno", 1)
                extraction.calls.append(
                    CallSite(
                        file_path=rel_path,
                        caller_symbol_id=caller,
                        callee_expr_text=callee_expr,
                        line=line,
                    )
                )
            for child in ast.iter_child_nodes(expr):
                if isinstance(child, ast.expr):
                    visit_expr(child)
            return

        if isinstance(expr, ast.Lambda):
            lambda_counter += 1
            synthetic_name = f"<lambda_{lambda_counter}>"
            start = getattr(expr, "lineno", 1)
            end = getattr(expr, "end_lineno", start)
            sid = add_symbol(synthetic_name, start, end, is_lambda=True)
            push_scope(qn_for(synthetic_name), sid)
            visit_expr(expr.body)
            pop_scope()
            return

        for child in ast.iter_child_nodes(expr):
            if isinstance(child, ast.expr):
                visit_expr(child)

    visit_statements(module.body)
    return extraction


def _expr_to_callee_text(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _expr_to_callee_text(expr.value)
        return f"{base}.{expr.attr}" if base else expr.attr
    return ""


# =========================
# Resolution API
# =========================


def build_symbol_lookup(
    symbols: Iterable[FunctionSymbol],
) -> Tuple[Dict[str, str], Dict[str, List[str]], Dict[str, str]]:
    """
    Returns:
      by_qualified_name: qname -> symbol_id
      by_simple_name: name -> [symbol_id...]
      module_member_to_symbol: "module.name" -> symbol_id
    """
    by_qualified_name: Dict[str, str] = {}
    by_simple_name: Dict[str, List[str]] = {}
    module_member_to_symbol: Dict[str, str] = {}

    for s in symbols:
        by_qualified_name[s.qualified_name] = s.symbol_id
        by_simple_name.setdefault(s.name, []).append(s.symbol_id)

        mod = module_name_from_path(s.file_path)
        module_member_to_symbol[f"{mod}.{s.name}"] = s.symbol_id
        module_member_to_symbol[f"{mod}.{s.qualified_name}"] = s.symbol_id

    return by_qualified_name, by_simple_name, module_member_to_symbol


def resolve_callee_symbol_ids(
    call: CallSite,
    file_local_defs: Dict[str, Dict[str, List[str]]],
    global_simple: Dict[str, List[str]],
    imports_by_file: Dict[str, List[ImportAlias]],
    module_member_to_symbol: Dict[str, str],
    caller_qualified_name_by_id: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    Conservative static resolution for intra-repo calls.

    Supported:
      - name(...) using local file defs first; then unique global match
      - alias(...) for `from pkg.mod import fn as alias`
      - alias.fn(...) for `import pkg.mod as alias`
      - pkg.fn(...) if `pkg` imported and mapped
    Ignored:
      - dynamic dispatch/object methods
      - ambiguous global simple-name matches
      - unresolved external symbols
    """
    expr = call.callee_expr_text.strip()
    if not expr:
        return []

    expr = _strip_wrapping_parens(expr)

    imports = imports_by_file.get(call.file_path, [])

    # 1) direct name call: util(...)
    if "." not in expr and _is_identifier(expr):
        local_map = file_local_defs.get(call.file_path, {})
        local_candidates = local_map.get(expr, [])
        if len(local_candidates) == 1:
            return local_candidates
        if len(local_candidates) > 1:
            # ambiguous nested shadowing in file-level aggregate; ignore
            return []

        # from mod import obj as x ; x()
        for imp in imports:
            if imp.local_name == expr and imp.source_name:
                key = f"{imp.source_module}.{imp.source_name}"
                sid = module_member_to_symbol.get(key)
                if sid:
                    return [sid]

        global_candidates = global_simple.get(expr, [])
        if len(global_candidates) == 1:
            return global_candidates
        return []

    # 2) dotted call: alias.func(...)
    if "." in expr:
        head, _, tail = expr.partition(".")
        if not (_is_identifier(head) and _is_identifier(tail)):
            return []

        # self.method() / cls.method() inside class scope
        if head in {"self", "cls"} and caller_qualified_name_by_id is not None:
            caller_qn = caller_qualified_name_by_id.get(call.caller_symbol_id, "")
            if "." in caller_qn:
                class_qn = caller_qn.rsplit(".", 1)[0]
                mod = module_name_from_path(call.file_path)
                key = f"{mod}.{class_qn}.{tail}"
                sid = module_member_to_symbol.get(key)
                if sid:
                    return [sid]

        # import mod as m ; m.func()
        for imp in imports:
            if imp.local_name != head:
                continue

            if imp.source_name is None:
                # import pkg.mod as m ; m.func()
                key = f"{imp.source_module}.{tail}"
            else:
                # from pkg import mod as m ; m.func()
                key = f"{imp.source_module}.{imp.source_name}.{tail}"

            sid = module_member_to_symbol.get(key)
            if sid:
                return [sid]

        return []

    return []


# =========================
# Helper utilities
# =========================


def iter_python_files(repo_root: Path) -> Iterable[Path]:
    for p in repo_root.rglob("*.py"):
        parts = p.parts
        if "__pycache__" in parts:
            continue
        if any(part.startswith(".") for part in parts):
            continue
        yield p


def module_name_from_path(rel_path: str) -> str:
    """
    Convert `pkg/sub/mod.py` -> `pkg.sub.mod`
    and `pkg/sub/__init__.py` -> `pkg.sub`
    """
    p = Path(rel_path).with_suffix("")
    parts = list(p.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_by_file(extraction: RepoExtraction) -> Dict[str, List[ImportAlias]]:
    out: Dict[str, List[ImportAlias]] = {}
    for f in extraction.files:
        out[f.file_path] = list(f.imports)
    return out


def file_local_defs(extraction: RepoExtraction) -> Dict[str, Dict[str, List[str]]]:
    out: Dict[str, Dict[str, List[str]]] = {}
    for f in extraction.files:
        out[f.file_path] = dict(f.local_defs_by_name)
    return out


def _symbol_id(file_path: str, qualified_name: str, start_line: int) -> str:
    return f"{file_path}:{qualified_name}:{start_line}"


def _node_text(node: Optional[Node], source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _is_identifier(text: str) -> bool:
    return text.isidentifier()


def _strip_wrapping_parens(expr: str) -> str:
    e = expr.strip()
    while e.startswith("(") and e.endswith(")") and len(e) >= 2:
        e = e[1:-1].strip()
    return e


def _extract_assignment_name(node: Node, source: bytes) -> Optional[str]:
    """
    Supports:
      x = ...
    Ignores tuple unpacking / attrs / subscripts.
    """
    left = node.child_by_field_name("left")
    if left is None:
        return None
    if left.type == "identifier":
        return _node_text(left, source)
    return None


def _extract_import_aliases(
    node: Node,
    file_path: str,
    current_module: str,
) -> List[ImportAlias]:
    """
    Best-effort parsing of import statements:
      - import a.b as c
      - import a.b
      - from a.b import f as g
      - from a.b import f
      - relative from imports are normalized with current module package
    """
    out: List[ImportAlias] = []

    # helper to decode arbitrary node text
    def txt(n: Node) -> str:
        raw = n.text
        if raw is None:
            return ""
        return raw.decode("utf-8", errors="replace")

    if node.type == "import_statement":
        for ch in node.children:
            if ch.type == "aliased_import":
                # Usually: <dotted_name|identifier> as <identifier>
                names = [
                    c for c in ch.children if c.type in ("dotted_name", "identifier")
                ]
                if not names:
                    continue
                src = txt(names[0])
                alias = txt(names[-1]) if len(names) > 1 else src.split(".")[-1]
                out.append(
                    ImportAlias(
                        file_path=file_path,
                        local_name=alias,
                        source_module=src,
                        source_name=None,
                    )
                )
            elif ch.type in ("dotted_name", "identifier"):
                src = txt(ch)
                out.append(
                    ImportAlias(
                        file_path=file_path,
                        local_name=src.split(".")[-1],
                        source_module=src,
                        source_name=None,
                    )
                )

    elif node.type == "import_from_statement":
        module_name: Optional[str] = None
        imported: List[Tuple[str, str]] = []

        for ch in node.children:
            if ch.type == "dotted_name":
                module_name = txt(ch)
            elif ch.type == "aliased_import":
                ids = [c for c in ch.children if c.type == "identifier"]
                if not ids:
                    continue
                src_name = txt(ids[0])
                local_name = txt(ids[-1])
                imported.append((src_name, local_name))
            elif ch.type == "identifier":
                # either module (rare shape) or imported symbol
                token = txt(ch)
                if module_name is None:
                    module_name = token
                else:
                    imported.append((token, token))

        if module_name:
            module_name = _normalize_relative_module(current_module, module_name)
            for src_name, local_name in imported:
                out.append(
                    ImportAlias(
                        file_path=file_path,
                        local_name=local_name,
                        source_module=module_name,
                        source_name=src_name,
                    )
                )

    return out


def _normalize_relative_module(current_module: str, module_name: str) -> str:
    if not module_name.startswith("."):
        return module_name

    dots = len(module_name) - len(module_name.lstrip("."))
    suffix = module_name[dots:]
    current_parts = current_module.split(".") if current_module else []

    # module context should resolve from package, not leaf module name
    pkg_parts = current_parts[:-1] if current_parts else []

    if dots > len(pkg_parts) + 1:
        base_parts: List[str] = []
    else:
        base_parts = pkg_parts[: max(0, len(pkg_parts) - (dots - 1))]

    if suffix:
        base_parts.extend(suffix.split("."))

    return ".".join([p for p in base_parts if p])
