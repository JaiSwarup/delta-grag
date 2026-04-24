"""
Transformers client wrapper for real provider-backed text generation.

Design goals
------------
- Keep model invocation behind a small, testable interface.
- Allow easy switching between backends via configuration.
- Avoid hard dependency on `transformers` unless the HF backend is used.

This module provides:
- `GenerationRequest`: structured generation input.
- `GenerationResult`: normalized generation output + metadata.
- `TransformersClientConfig`: backend/model/runtime settings.
- `TransformersClient`: unified client with `generate(...)`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol


class Backend(Protocol):
    """Backend protocol for text generation backends."""

    def generate(self, request: "GenerationRequest") -> "GenerationResult": ...


@dataclass(frozen=True)
class GenerationRequest:
    """Request payload for generation."""

    prompt: str
    max_new_tokens: int = 512
    temperature: float = 0.1
    top_p: float = 0.95
    do_sample: bool = False
    stop: tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """Normalized generation output."""

    text: str
    raw: Any = None
    backend: str = "unknown"
    model_name: str = "unknown"
    usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransformersClientConfig:
    """
    Client configuration.

    backend:
      - "mock": dev/test-only backend, guarded behind allow_dev_mock_controls
      - "hf_pipeline": Hugging Face pipeline backend
            - "openrouter": OpenRouter chat-completions backend

    mock_response_text:
      Optional fixed mock output used by the dev/test mock backend.
    """

    backend: str = "mock"
    model_name: str = "mock-model"
    device: int = -1
    trust_remote_code: bool = False
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: Optional[str] = None
    openrouter_site_name: Optional[str] = None
    openrouter_timeout_seconds: int = 60

    # HF generation defaults
    max_new_tokens: int = 512
    temperature: float = 0.1
    top_p: float = 0.95
    do_sample: bool = False

    # Mock controls
    mock_response_text: Optional[str] = None
    mock_prefix: str = "[MOCK REVIEW]"
    mock_deterministic_hash: bool = True
    allow_dev_mock_controls: bool = False

class HFPipelineBackend:
    """Hugging Face `transformers.pipeline` backend."""

    def __init__(self, config: TransformersClientConfig) -> None:
        self._config = config
        self._pipeline = None

    def generate(self, request: GenerationRequest) -> GenerationResult:
        pipe = self._get_pipeline()

        kwargs = {
            "max_new_tokens": request.max_new_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "do_sample": request.do_sample,
        }

        # Stop sequences are handled best-effort by truncation post-process here.
        out = pipe(request.prompt, **kwargs)
        text = _extract_generated_text(out)

        if request.stop:
            text = _truncate_on_stop(text, request.stop)

        return GenerationResult(
            text=text,
            raw=out,
            backend="hf_pipeline",
            model_name=self._config.model_name,
            usage={
                "input_chars": len(request.prompt),
                "output_chars": len(text),
            },
            metadata={"stops_applied": bool(request.stop)},
        )

    def _get_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline

        try:
            from transformers import pipeline  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Hugging Face backend requires `transformers` to be installed."
            ) from exc

        self._pipeline = pipeline(
            task="text-generation",
            model=self._config.model_name,
            device=self._config.device,
            trust_remote_code=self._config.trust_remote_code,
        )
        return self._pipeline


class OpenRouterBackend:
    """OpenRouter chat-completions backend over HTTP."""

    def __init__(self, config: TransformersClientConfig) -> None:
        self._config = config
        self._api_key = (
            config.openrouter_api_key
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("OPEN_ROUTER_API_KEY")
            or _read_env_value_from_dotenv("OPENROUTER_API_KEY")
            or _read_env_value_from_dotenv("OPEN_ROUTER_API_KEY")
        )
        if not self._api_key:
            raise ValueError(
                "OpenRouter backend requires openrouter_api_key, OPENROUTER_API_KEY, "
                "or OPENROUTER_API_KEY in .env"
            )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            from urllib import request as urllib_request
        except Exception as exc:
            raise RuntimeError("Failed to import urllib.request for OpenRouter backend") from exc

        payload: Dict[str, Any] = {
            "model": self._config.model_name,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_new_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
        }
        if request.stop:
            payload["stop"] = list(request.stop)

        body = json.dumps(payload).encode("utf-8")
        base = self._config.openrouter_base_url.rstrip("/")
        req = urllib_request.Request(
            url=f"{base}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        if self._config.openrouter_site_url:
            req.add_header("HTTP-Referer", self._config.openrouter_site_url)
        if self._config.openrouter_site_name:
            req.add_header("X-Title", self._config.openrouter_site_name)

        try:
            with urllib_request.urlopen(req, timeout=self._config.openrouter_timeout_seconds) as resp:
                raw_text = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise RuntimeError(f"OpenRouter request failed: {exc}") from exc

        try:
            raw = json.loads(raw_text)
        except Exception as exc:
            raise RuntimeError("OpenRouter returned non-JSON response") from exc

        text = _extract_openrouter_text(raw)
        if request.stop:
            text = _truncate_on_stop(text, request.stop)

        usage = {
            "input_chars": len(request.prompt),
            "output_chars": len(text),
        }
        if isinstance(raw, dict) and isinstance(raw.get("usage"), dict):
            usage.update(raw["usage"])

        return GenerationResult(
            text=text,
            raw=raw,
            backend="openrouter",
            model_name=self._config.model_name,
            usage=usage,
            metadata={
                "stops_applied": bool(request.stop),
                "provider": "openrouter",
            },
        )


class TransformersClient:
    """Unified generation client with pluggable backend."""

    def __init__(
        self,
        config: Optional[TransformersClientConfig] = None,
        backend_override: Optional[Backend] = None,
    ) -> None:
        self._config = config or TransformersClientConfig()
        self._backend: Backend = backend_override or self._init_backend(self._config)

    @property
    def config(self) -> TransformersClientConfig:
        return self._config

    def generate(
        self,
        prompt: str,
        *,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        do_sample: Optional[bool] = None,
        stop: Optional[tuple[str, ...]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> GenerationResult:
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        req = GenerationRequest(
            prompt=prompt,
            max_new_tokens=max_new_tokens
            if max_new_tokens is not None
            else self._config.max_new_tokens,
            temperature=temperature
            if temperature is not None
            else self._config.temperature,
            top_p=top_p if top_p is not None else self._config.top_p,
            do_sample=do_sample if do_sample is not None else self._config.do_sample,
            stop=stop or (),
            metadata=dict(metadata or {}),
        )

        _validate_request(req)
        return self._backend.generate(req)

    def _init_backend(self, config: TransformersClientConfig) -> Backend:
        backend = (config.backend or "").strip().lower()
        if backend == "mock":
            if not config.allow_dev_mock_controls:
                raise ValueError(
                    "Mock backend is disabled on the production runtime path; "
                    "set allow_dev_mock_controls=True for tests/dev-only usage"
                )
            from .dev_mock_backend import MockBackend

            return MockBackend(config)
        if backend in {"hf", "hf_pipeline", "transformers"}:
            if config.mock_response_text is not None:
                raise ValueError(
                    "mock_response_text may only be used with the mock backend in tests/dev"
                )
            return HFPipelineBackend(config)
        if backend in {"openrouter", "openrouter_chat"}:
            if config.mock_response_text is not None:
                raise ValueError(
                    "mock_response_text may only be used with the mock backend in tests/dev"
                )
            return OpenRouterBackend(config)
        raise ValueError(f"Unsupported backend: {config.backend!r}")


def _validate_request(req: GenerationRequest) -> None:
    if req.max_new_tokens < 1:
        raise ValueError("max_new_tokens must be >= 1")
    if req.temperature < 0:
        raise ValueError("temperature must be >= 0")
    if not (0 < req.top_p <= 1):
        raise ValueError("top_p must be in (0, 1]")
    if not isinstance(req.do_sample, bool):
        raise TypeError("do_sample must be a bool")


def _extract_generated_text(raw: Any) -> str:
    """
    Extract generated text from common transformers pipeline output shapes.
    """
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            if "generated_text" in first and isinstance(first["generated_text"], str):
                return first["generated_text"]
            if "text" in first and isinstance(first["text"], str):
                return first["text"]
    if isinstance(raw, str):
        return raw
    return str(raw)


def _truncate_on_stop(text: str, stops: tuple[str, ...]) -> str:
    cut = None
    for s in stops:
        idx = text.find(s)
        if idx >= 0:
            cut = idx if cut is None else min(cut, idx)
    return text if cut is None else text[:cut]


def _extract_openrouter_text(raw: Any) -> str:
    if not isinstance(raw, dict):
        return str(raw)
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return str(first)
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "\n".join(parts)
    return ""


def _read_env_value_from_dotenv(name: str) -> Optional[str]:
    """
    Best-effort dotenv lookup for local development.

    Search order:
    1) current working directory and parents
    2) this file's directory parents
    """
    for env_path in _candidate_dotenv_paths():
        value = _read_name_from_env_file(env_path, name)
        if value:
            return value
    return None


def _candidate_dotenv_paths() -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    for base in (Path.cwd(), Path(__file__).resolve().parent):
        for parent in (base, *base.parents):
            p = parent / ".env"
            if p in seen:
                continue
            seen.add(p)
            candidates.append(p)

    return candidates


def _read_name_from_env_file(env_path: Path, name: str) -> Optional[str]:
    try:
        if not env_path.exists() or not env_path.is_file():
            return None
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None

    prefix = f"{name}="
    export_prefix = f"export {name}="

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(export_prefix):
            value = line[len(export_prefix) :].strip()
        elif line.startswith(prefix):
            value = line[len(prefix) :].strip()
        else:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value.strip() or None

    return None

def generate_text(
    prompt: str,
    *,
    config: Optional[TransformersClientConfig] = None,
    max_new_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    do_sample: Optional[bool] = None,
    stop: Optional[tuple[str, ...]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    """
    Convenience helper for one-shot text generation.

    This is equivalent to creating a `TransformersClient` and calling
    `client.generate(...)`, but returns only the generated text.
    """
    client = TransformersClient(config=config)
    result = client.generate(
        prompt,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        do_sample=do_sample,
        stop=stop,
        metadata=metadata,
    )
    return result.text


def generate_result(
    prompt: str,
    *,
    config: Optional[TransformersClientConfig] = None,
    max_new_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    do_sample: Optional[bool] = None,
    stop: Optional[tuple[str, ...]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> GenerationResult:
    """
    Convenience helper returning full normalized generation result.
    """
    client = TransformersClient(config=config)
    return client.generate(
        prompt,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        do_sample=do_sample,
        stop=stop,
        metadata=metadata,
    )


__all__ = [
    "GenerationRequest",
    "GenerationResult",
    "TransformersClientConfig",
    "TransformersClient",
    "HFPipelineBackend",
    "OpenRouterBackend",
    "generate_text",
    "generate_result",
]
