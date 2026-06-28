"""Generic OpenAI-compatible provider adapter.

Supports any provider that implements the OpenAI chat completions API:
- Groq
- Together AI
- Fireworks AI
- Mistral
- Cerebras
- DeepInfra
- Custom OpenAI-compatible endpoints

Configuration via environment variables:
    AIKEYPOOL_PROVIDER_<NAME>_ENDPOINT  — chat completions URL
    AIKEYPOOL_PROVIDER_<NAME>_MODEL     — default model name
"""

import os
from typing import Optional

from ..base_provider import BaseProvider


class GenericOpenAIProvider(BaseProvider):
    """Generic provider for OpenAI-compatible APIs.

    Detects endpoint and model from environment variables:
        AIKEYPOOL_PROVIDER_<NAME>_ENDPOINT
        AIKEYPOOL_PROVIDER_<NAME>_MODEL

    Falls back to sensible defaults for known providers.
    """

    # Known provider defaults (endpoint, default model)
    KNOWN_DEFAULTS: dict[str, tuple[str, str]] = {
        "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
        "together": ("https://api.together.xyz/v1/chat/completions", "meta-llama/Llama-3-70b-chat-hf"),
        "fireworks": ("https://api.fireworks.ai/inference/v1/chat/completions", "accounts/fireworks/models/llama-v3p3-70b-instruct"),
        "mistral": ("https://api.mistral.ai/v1/chat/completions", "mistral-large-latest"),
        "cerebras": ("https://api.cerebras.ai/v1/chat/completions", "llama-3.3-70b"),
        "deepinfra": ("https://api.deepinfra.com/v1/openai/chat/completions", "meta-llama/Meta-Llama-3.1-70B-Instruct"),
        "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
        "anthropic": None,  # Not OpenAI-compatible
        "github_models": None,  # Has its own adapter
        "openrouter": None,  # Has its own adapter
    }

    def __init__(self, provider_name: str = ""):
        self._provider_name = provider_name
        self._endpoint = self._detect_endpoint()
        self._default_model = self._detect_model()

    def _detect_endpoint(self) -> str:
        """Detect endpoint from environment or known defaults."""
        env_key = f"AIKEYPOOL_PROVIDER_{self._provider_name.upper()}_ENDPOINT"
        env_endpoint = os.environ.get(env_key, "")
        if env_endpoint:
            return env_endpoint

        defaults = self.KNOWN_DEFAULTS.get(self._provider_name.lower())
        if defaults:
            return defaults[0]

        return ""

    def _detect_model(self) -> str:
        """Detect default model from environment or known defaults."""
        env_key = f"AIKEYPOOL_PROVIDER_{self._provider_name.upper()}_MODEL"
        env_model = os.environ.get(env_key, "")
        if env_model:
            return env_model

        defaults = self.KNOWN_DEFAULTS.get(self._provider_name.lower())
        if defaults:
            return defaults[1]

        return "gpt-4o-mini"

    def get_provider_name(self) -> str:
        return self._provider_name

    def get_endpoint(self) -> str:
        return self._endpoint

    def get_auth_headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
