"""Corpus loading for real-project LLM-free evaluation.

The default corpus intentionally points at the sibling ``code-review-graph``
evaluation configs so BTP is measured on the same open-source projects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only in under-provisioned envs
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class EvalCommitCase:
    repo: str
    repo_url: str
    language: str
    sha: str
    description: str = ""
    expected_changed_files: int | None = None

    @property
    def pr_id(self) -> str:
        return f"{self.repo}@{self.sha[:12]}"


@dataclass(frozen=True)
class EvalRepoConfig:
    name: str
    url: str
    language: str
    commit: str = "HEAD"
    size_category: str = "unknown"
    test_commits: tuple[EvalCommitCase, ...] = field(default_factory=tuple)


def default_configs_dir() -> Path:
    """Return the sibling code-review-graph config directory when available."""
    btp_root = Path(__file__).resolve().parents[2]
    sibling = (
        btp_root.parent
        / "code-review-graph"
        / "code_review_graph"
        / "eval"
        / "configs"
    )
    if sibling.exists():
        return sibling
    return Path(__file__).resolve().parent / "configs"


def load_repo_config(path: str | Path) -> EvalRepoConfig:
    config_path = Path(path).expanduser().resolve()
    text = config_path.read_text(encoding="utf-8")
    payload = yaml.safe_load(text) if yaml is not None else _load_simple_yaml(text)
    if not isinstance(payload, Mapping):
        raise ValueError(f"Invalid evaluation config: {config_path}")

    name = _required_text(payload, "name")
    url = _required_text(payload, "url")
    language = str(payload.get("language", "unknown")).strip() or "unknown"
    test_commits = tuple(
        _commit_case(name=name, url=url, language=language, raw=item)
        for item in _sequence(payload.get("test_commits", ()))
    )
    return EvalRepoConfig(
        name=name,
        url=url,
        language=language,
        commit=str(payload.get("commit", "HEAD")),
        size_category=str(payload.get("size_category", "unknown")),
        test_commits=test_commits,
    )


def load_configs(
    *,
    configs_dir: str | Path | None = None,
    repos: Sequence[str] | None = None,
    limit: int | None = None,
) -> tuple[EvalRepoConfig, ...]:
    root = Path(configs_dir).expanduser().resolve() if configs_dir else default_configs_dir()
    if not root.exists():
        raise FileNotFoundError(f"Evaluation configs directory not found: {root}")

    selected = {name.strip() for name in repos or () if name.strip()}
    paths = sorted(root.glob("*.yaml"))
    if selected:
        paths = [path for path in paths if path.stem in selected]
        missing = selected - {path.stem for path in paths}
        if missing:
            raise FileNotFoundError(f"Missing evaluation config(s): {sorted(missing)}")

    configs = [load_repo_config(path) for path in paths]
    if limit is None:
        return tuple(configs)

    remaining = int(limit)
    limited: list[EvalRepoConfig] = []
    for config in configs:
        if remaining <= 0:
            break
        commits = config.test_commits[:remaining]
        remaining -= len(commits)
        limited.append(
            EvalRepoConfig(
                name=config.name,
                url=config.url,
                language=config.language,
                commit=config.commit,
                size_category=config.size_category,
                test_commits=commits,
            )
        )
    return tuple(limited)


def iter_cases(configs: Iterable[EvalRepoConfig]) -> tuple[EvalCommitCase, ...]:
    return tuple(case for config in configs for case in config.test_commits)


def _commit_case(
    *,
    name: str,
    url: str,
    language: str,
    raw: Any,
) -> EvalCommitCase:
    if not isinstance(raw, Mapping):
        raise ValueError(f"Invalid test_commit in {name}: {raw!r}")
    changed_files = raw.get("changed_files")
    return EvalCommitCase(
        repo=name,
        repo_url=url,
        language=language,
        sha=_required_text(raw, "sha"),
        description=str(raw.get("description", "")),
        expected_changed_files=int(changed_files) if changed_files is not None else None,
    )


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"Missing required config key: {key}")
    return value


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def _load_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small CRG eval-config YAML subset without PyYAML.

    Supported shape:
    - top-level scalar keys
    - top-level lists of scalars
    - top-level lists of mappings with scalar fields
    """
    root: dict[str, Any] = {}
    current_key: str | None = None
    current_item: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()

        if indent == 0:
            current_item = None
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key
            if value:
                root[key] = _parse_scalar(value)
            else:
                root[key] = []
            continue

        if current_key is None:
            continue

        if line.startswith("- "):
            payload = line[2:].strip()
            if ":" in payload:
                key, value = payload.split(":", 1)
                current_item = {key.strip(): _parse_scalar(value.strip())}
                root.setdefault(current_key, []).append(current_item)
            else:
                current_item = None
                root.setdefault(current_key, []).append(_parse_scalar(payload))
            continue

        if current_item is not None and ":" in line:
            key, value = line.split(":", 1)
            current_item[key.strip()] = _parse_scalar(value.strip())

    return root


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value


__all__ = [
    "EvalCommitCase",
    "EvalRepoConfig",
    "default_configs_dir",
    "iter_cases",
    "load_configs",
    "load_repo_config",
]
