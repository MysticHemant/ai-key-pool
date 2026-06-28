"""Provider factory for AI Key Pool.

Instantiates the correct provider adapter based provider name.
Supports built-in adapters, plugin adapters, and generic OpenAI-compatible
providers discovered automatically from environment variables.
"""

from typing import Optional
from .base_provider import BaseProvider
from .github_models import GitHubModelsProvider
from .groq import GroqProvider
from .openrouter import OpenRouterProvider
from ..utils.logger import get_logger


logger = get_logger("provider_factory")

# Built-in providers with dedicated adapters
PROVIDER_MAP: dict[str, type[BaseProvider]] = {
    "github_models": GitHubModelsProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
}

# Providers known to be OpenAI-compatible (use generic adapter)
_OPENAI_COMPATIBLE = {
    "groq", "together", "fireworks", "mistral", "cerebras",
    "deepinfra", "openai", "cerebras", "deepinfra",
}

# Providers that are NOT OpenAI-compatible
_NOT_OPENAI_COMPATIBLE = {"anthropic"}


def _discover_providers() -> None:
    """Auto-discover providers from environment variables.

    Scans for AIKEYPOOL_PROVIDER_*_KEYS and registers generic
    OpenAI-compatible adapters for unknown providers.
    """
    import os

    for key, value in os.environ.items():
        if key.startswith("AIKEYPOOL_PROVIDER_") and key.endswith("_KEYS"):
            provider_name = key[len("AIKEYPOOL_PROVIDER_"):-len("_KEYS")].lower()
            if provider_name and provider_name not in PROVIDER_MAP:
                if provider_name in _NOT_OPENAI_COMPATIBLE:
                    logger.info("PROVIDER DISCOVERY: %s detected (requires dedicated plugin)", provider_name)
                    continue

                # Register as generic OpenAI-compatible
                PROVIDER_MAP[provider_name] = _make_generic_provider_class(provider_name)
                logger.info("PROVIDER DISCOVERY: %s registered (generic OpenAI adapter)", provider_name)


def _make_generic_provider_class(provider_name: str) -> type[BaseProvider]:
    """Create a provider class for an OpenAI-compatible API.

    Reads endpoint and model from environment:
        AIKEYPOOL_PROVIDER_<NAME>_ENDPOINT
        AIKEYPOOL_PROVIDER_<NAME>_MODEL

    Args:
        provider_name: Provider identifier

    Returns:
        Provider class
    """
    import os

    # Known defaults: (endpoint, default_model)
    _DEFAULTS = {
        "together": ("https://api.together.xyz/v1/chat/completions", "meta-llama/Llama-3-70b-chat-hf"),
        "fireworks": ("https://api.fireworks.ai/inference/v1/chat/completions", "accounts/fireworks/models/llama-v3p3-70b-instruct"),
        "mistral": ("https://api.mistral.ai/v1/chat/completions", "mistral-large-latest"),
        "cerebras": ("https://api.cerebras.ai/v1/chat/completions", "llama-3.3-70b"),
        "deepinfra": ("https://api.deepinfra.com/v1/openai/chat/completions", "meta-llama/Meta-Llama-3.1-70B-Instruct"),
        "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    }

    endpoint_env = os.environ.get(f"AIKEYPOOL_PROVIDER_{provider_name.upper()}_ENDPOINT", "")
    model_env = os.environ.get(f"AIKEYPOOL_PROVIDER_{provider_name.upper()}_MODEL", "")

    defaults = _DEFAULTS.get(provider_name, ("", "gpt-4o-mini"))
    endpoint = endpoint_env or defaults[0]
    default_model = model_env or defaults[1]

    class _GenericProvider(BaseProvider):
        def get_provider_name(self) -> str:
            return provider_name

        def get_endpoint(self) -> str:
            return endpoint

        def get_auth_headers(self, api_key: str) -> dict:
            return {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

        def get_default_model(self) -> str:
            return default_model

    _GenericProvider.__name__ = f"Generic_{provider_name}"
    _GenericProvider.__qualname__ = f"Generic_{provider_name}"

    return _GenericProvider


_discovery_done = False


def create_provider(provider_name: str, **kwargs) -> BaseProvider:
    """Instantiate a provider by name.

    Falls back to generic OpenAI-compatible adapter if no dedicated
    adapter is found and the provider has keys configured.

    Args:
        provider_name: Provider identifier (e.g., 'groq', 'openrouter')
        **kwargs: Additional arguments passed to the provider constructor

    Returns:
        Configured provider instance

    Raises:
        ValueError: If provider_name is not registered and cannot be auto-discovered
    """
    global _discovery_done
    if not _discovery_done:
        _discover_providers()
        _discovery_done = True

    cls = PROVIDER_MAP.get(provider_name.lower())
    if not cls:
        available = ", ".join(sorted(PROVIDER_MAP.keys()))
        raise ValueError(
            f"Unknown provider: '{provider_name}'. Available: {available}"
        )
    return cls(**kwargs)


def list_providers() -> list[str]:
    """Return list of available provider names."""
    global _discovery_done
    if not _discovery_done:
        _discover_providers()
        _discovery_done = True
    return sorted(PROVIDER_MAP.keys())


def get_provider_status() -> dict[str, dict]:
    """Get detailed status of all providers.

    Returns:
        Dict mapping provider name -> status info
    """
    global _discovery_done
    if not _discovery_done:
        _discover_providers()
        _discovery_done = True

    status = {}
    for name, cls in PROVIDER_MAP.items():
        status[name] = {
            "adapter": "builtin" if name in ("github_models", "groq", "openrouter") else "generic",
            "class": cls.__name__,
        }
    return status
