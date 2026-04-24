"""LLM package interfaces for prompt construction and review generation."""

from .prompt_builder import (
    PromptBuildConfig,
    PromptBuildResult,
    build_prompt_from_pipeline_result,
    build_review_prompt,
)
from .review_generator import (
    Citation,
    Finding,
    LLMClient,
    ReviewGenerator,
    ReviewGeneratorConfig,
    ReviewResult,
    parse_findings_from_model_output,
)
from .dev_mock_backend import MockBackend
from .transformers_client import (
    GenerationRequest,
    GenerationResult,
    HFPipelineBackend,
    OpenRouterBackend,
    TransformersClient,
    TransformersClientConfig,
    generate_result,
    generate_text,
)

__all__ = [
    "PromptBuildConfig",
    "PromptBuildResult",
    "build_review_prompt",
    "build_prompt_from_pipeline_result",
    "Citation",
    "Finding",
    "ReviewResult",
    "ReviewGeneratorConfig",
    "LLMClient",
    "ReviewGenerator",
    "parse_findings_from_model_output",
    "GenerationRequest",
    "GenerationResult",
    "TransformersClientConfig",
    "TransformersClient",
    "MockBackend",
    "HFPipelineBackend",
    "OpenRouterBackend",
    "generate_text",
    "generate_result",
]
