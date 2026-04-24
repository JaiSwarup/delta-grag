"""
Dev/test-only mock LLM backend.

This module intentionally keeps deterministic mock behavior out of the
production-focused transformers client implementation.
"""

from __future__ import annotations

import json

from .transformers_client import GenerationRequest, GenerationResult, TransformersClientConfig


class MockBackend:
    """Deterministic mock backend for tests and local development."""

    def __init__(self, config: TransformersClientConfig) -> None:
        self._config = config

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if self._config.mock_response_text is not None:
            text = self._config.mock_response_text
        else:
            text = self._build_default_response(request)

        return GenerationResult(
            text=text,
            raw={"mock": True},
            backend="mock",
            model_name=self._config.model_name,
            usage={
                "input_chars": len(request.prompt),
                "output_chars": len(text),
            },
            metadata={"deterministic": True},
        )

    def _build_default_response(self, request: GenerationRequest) -> str:
        prompt_preview = request.prompt[:240].strip().replace("\n", "\\n")
        digest = (
            _stable_digest(request.prompt)
            if self._config.mock_deterministic_hash
            else "na"
        )
        preview = (
            f"{self._config.mock_prefix}\n"
            f"- digest: {digest}\n"
            f"- prompt_preview: {prompt_preview}\n"
            f"- note: mock backend does not run an LLM; replace backend with hf_pipeline for real inference."
        )
        payload = {
            "overall_risk": "low",
            "findings": [],
            "mock": {
                "deterministic": bool(self._config.mock_deterministic_hash),
                "digest": digest,
                "prompt_preview": prompt_preview,
            },
        }
        # Keep legacy-readable preview while appending structured JSON for downstream parsing.
        return f"{preview}\n{json.dumps(payload, ensure_ascii=False)}"


def _stable_digest(text: str) -> str:
    # Deterministic lightweight digest without importing heavy crypto libs.
    # This is NOT for security; it's for test traceability only.
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


__all__ = ["MockBackend"]
