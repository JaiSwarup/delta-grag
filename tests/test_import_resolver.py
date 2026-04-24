from __future__ import annotations

from pathlib import Path

from src.file_indexer import build_file_index
from src.import_resolver import build_import_map_with_exports


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_import_map_resolves_absolute_and_relative_imports(tmp_path: Path) -> None:
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "helpers.py", "def util():\n    return 1\n")
    _write(
        tmp_path / "pkg" / "consumer.py",
        "from .helpers import util as helper_util\n"
        "import pkg.helpers as helpers\n",
    )

    file_index = build_file_index(tmp_path, include_extensions=[".py"])
    import_map = build_import_map_with_exports(tmp_path, file_index)

    assert import_map.file_to_module["pkg/helpers.py"] == "pkg.helpers"
    consumer_aliases = import_map.alias_to_fqn["pkg/consumer.py"]
    assert consumer_aliases["helper_util"] == "pkg.helpers.util"
    assert consumer_aliases["helpers"] == "pkg.helpers"


def test_build_import_map_resolves_init_re_exports(tmp_path: Path) -> None:
    _write(
        tmp_path / "pkg" / "__init__.py",
        "from .helpers import util\n",
    )
    _write(tmp_path / "pkg" / "helpers.py", "def util():\n    return 1\n")
    _write(
        tmp_path / "consumer.py",
        "from pkg import util\n",
    )

    file_index = build_file_index(tmp_path, include_extensions=[".py"])
    import_map = build_import_map_with_exports(tmp_path, file_index)

    assert import_map.file_to_module["pkg/__init__.py"] == "pkg"
    assert import_map.alias_to_fqn["consumer.py"]["util"] == "pkg.helpers.util"
