"""
Standalone import resolution and module boundary mapping.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.file_indexer import FileIndex


@dataclass(frozen=True)
class ImportMap:
    file_to_module: dict[str, str] = field(default_factory=dict)
    alias_to_fqn: dict[str, dict[str, str]] = field(default_factory=dict)


def build_import_map(
    snapshot_path: str | Path,
    file_index: FileIndex,
) -> ImportMap:
    root = Path(snapshot_path).expanduser().resolve()
    file_to_module = {
        metadata.path.as_posix(): _module_name_from_rel_path(metadata.path)
        for metadata in file_index.get_python_files()
    }
    module_to_file = {module: rel_path for rel_path, module in file_to_module.items()}

    alias_to_fqn: dict[str, dict[str, str]] = {}
    for rel_path, module_name in file_to_module.items():
        file_path = root / rel_path
        aliases = _extract_aliases_for_file(
            file_path=file_path,
            current_module=module_name,
            module_to_file=module_to_file,
        )
        alias_to_fqn[rel_path] = aliases

    return ImportMap(
        file_to_module=file_to_module,
        alias_to_fqn=alias_to_fqn,
    )


def _extract_aliases_for_file(
    *,
    file_path: Path,
    current_module: str,
    module_to_file: dict[str, str],
) -> dict[str, str]:
    source_text = file_path.read_text(encoding="utf-8", errors="replace")
    module = ast.parse(source_text, filename=str(file_path))
    aliases: dict[str, str] = {}

    for statement in module.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name in sys.stdlib_module_names:
                    continue
                local_name = alias.asname or alias.name.split(".")[-1]
                aliases[local_name] = alias.name
            continue

        if isinstance(statement, ast.ImportFrom):
            base_module = _normalize_relative_module(
                current_module=current_module,
                level=statement.level,
                module_name=statement.module,
            )
            if not base_module or base_module in sys.stdlib_module_names:
                continue

            for alias in statement.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                aliases[local_name] = _resolve_import_target(
                    base_module=base_module,
                    imported_name=alias.name,
                    module_to_file=module_to_file,
                )

    return aliases


def _resolve_import_target(
    *,
    base_module: str,
    imported_name: str,
    module_to_file: dict[str, str],
) -> str:
    direct_member = f"{base_module}.{imported_name}"
    if direct_member in module_to_file:
        return direct_member

    if base_module in module_to_file:
        module_file_rel = module_to_file[base_module]
        if module_file_rel.endswith("__init__.py"):
            re_exports = _read_init_re_exports(module_file_rel, module_to_file)
            if imported_name in re_exports:
                return re_exports[imported_name]

    return direct_member


def _read_init_re_exports(
    module_file_rel: str,
    module_to_file: dict[str, str],
) -> dict[str, str]:
    # Caller supplies actual file path elsewhere; here we only need stable parsing
    # of already-indexed module identities, so we infer the export target names from
    # the module map and the import statements in __init__.py if available.
    # This helper is deliberately best-effort and only supports explicit imports.
    return _INIT_REEXPORT_CACHE.setdefault(module_file_rel, {})


_INIT_REEXPORT_CACHE: dict[str, dict[str, str]] = {}


def populate_init_re_exports(snapshot_path: Path, import_map: ImportMap) -> None:
    root = snapshot_path.resolve()
    for rel_path, module_name in import_map.file_to_module.items():
        if not rel_path.endswith("__init__.py"):
            continue
        file_path = root / rel_path
        source_text = file_path.read_text(encoding="utf-8", errors="replace")
        module = ast.parse(source_text, filename=str(file_path))
        exports: dict[str, str] = {}
        for statement in module.body:
            if not isinstance(statement, ast.ImportFrom):
                continue
            base_module = _normalize_relative_module(
                current_module=f"{module_name}.__init__",
                level=statement.level,
                module_name=statement.module,
            )
            if not base_module:
                continue
            for alias in statement.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                exports[local_name] = f"{base_module}.{alias.name}"
        _INIT_REEXPORT_CACHE[rel_path] = exports


def _module_name_from_rel_path(rel_path: Path) -> str:
    stem = rel_path.with_suffix("")
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


def build_import_map_with_exports(
    snapshot_path: str | Path,
    file_index: FileIndex,
) -> ImportMap:
    root = Path(snapshot_path).expanduser().resolve()
    import_map = build_import_map(root, file_index)
    populate_init_re_exports(root, import_map)

    module_to_file = {module: rel for rel, module in import_map.file_to_module.items()}
    alias_to_fqn: dict[str, dict[str, str]] = {}
    for rel_path, module_name in import_map.file_to_module.items():
        alias_to_fqn[rel_path] = _extract_aliases_for_file(
            file_path=root / rel_path,
            current_module=module_name,
            module_to_file=module_to_file,
        )

    return ImportMap(
        file_to_module=import_map.file_to_module,
        alias_to_fqn=alias_to_fqn,
    )


__all__ = ["ImportMap", "build_import_map", "build_import_map_with_exports"]
