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
    AIKEYPOOL_PROVIDER_<NAME>_CAPABILITIES — comma-separated capability tags
    AIKEYPOOL_PROVIDER_<NAME>_PRIORITY  — routing priority (lower = higher)
"""

import os
from typing import Optional

from ..base_provider import BaseProvider
from ..manifest import ProviderManifest


# Default capabilities for known providers
_DEFAULT_CAPABILITIES = {
    "together": ["reasoning", "coding", "long_context"],
    "fireworks": ["reasoning", "coding", "fast_inference"],
    "mistral": ["reasoning", "coding", "vision", "long_context"],
    "cerebras": ["fast_inference", "reasoning"],
    "deepinfra": ["reasoning", "coding", "long_context"],
    "openai": ["reasoning", "coding", "vision", "long_context"],
    "sambanova": ["fast_inference", "reasoning", "coding"],
    "novita": ["reasoning", "coding"],
    "chutes": ["reasoning", "coding"],
    "groq": ["fast_inference", "reasoning", "coding"],
}

_DEFAULT_PRIORITY = {
    "together": 4,
    "fireworks": 5,
    "mistral": 3,
    "cerebras": 2,
    "deepinfra": 6,
    "openai": 2,
    "sambanova": 3,
    "novita": 7,
    "chutes": 8,
    "groq": 1,
}


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
        "sambanova": ("https://api.sambanova.ai/v1/chat/completions", "Meta-Llama-3.1-70B-Instruct"),
        "novita": ("https://api.novita.ai/v3/openai/chat/completions", "meta-llama-3.1-70b-instruct"),
        "chutes": ("https://api.chutes.ai/v1/chat/completions", "deepseek-ai/DeepSeek-V3"),
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

    def get_default_model(self) -> str:
        return self._default_model

    def get_manifest(self) -> ProviderManifest:
        """Return manifest for this generic provider.

        Reads capabilities and priority from environment variables:
            AIKEYPOOL_PROVIDER_<NAME>_CAPABILITIES — comma-separated tags
            AIKEYPOOL_PROVIDER_<NAME>_PRIORITY — integer priority
        """
        name = self._provider_name.lower()

        # Detect capabilities from env or defaults
        caps_key = f"AIKEYPOOL_PROVIDER_{name.upper()}_CAPABILITIES"
        env_caps = os.environ.get(caps_key, "")
        if env_caps:
            capabilities = [c.strip() for c in env_caps.split(",") if c.strip()]
        else:
            capabilities = list(_DEFAULT_CAPABILITIES.get(name, ["reasoning", "coding"]))

        # Detect priority from env or defaults
        pri_key = f"AIKEYPOOL_PROVIDER_{name.upper()}_PRIORITY"
        env_pri = os.environ.get(pri_key, "")
        if env_pri:
            try:
                priority = int(env_pri)
            except ValueError:
                priority = 10
        else:
            priority = _DEFAULT_PRIORITY.get(name, 10)

        # Detect supported models from env or use default
        models_key = f"AIKEYPOOL_PROVIDER_{name.upper()}_MODELS"
        env_models = os.environ.get(models_key, "")
        if env_models:
            supported_models = [m.strip() for m in env_models.split(",") if m.strip()]
        else:
            supported_models = [self._default_model] if self._default_model else []

        display_name = name.replace("_", " ").title()

        return ProviderManifest(
            provider_id=name,
            display_name=display_name,
            adapter="generic",
            supported_models=supported_models,
            capabilities=capabilities,
            priority=priority,
            health="unknown",
            endpoint=self._endpoint,
            default_model=self._default_model,
        )
