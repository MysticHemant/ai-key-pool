"""Plugin loader for AI Key Pool.

Discovers and loads provider plugins automatically.
Supports both built-in providers and generic OpenAI-compatible adapters.
"""

import os
from typing import Optional

from ..base_provider import BaseProvider
from ..plugins.generic_openai import GenericOpenAIProvider
from ...utils.logger import get_logger


logger = get_logger("plugin_loader")

# Built-in providers that have dedicated adapters
BUILTIN_PROVIDERS = {
    "github_models",
    "groq",
    "openrouter",
}

# Known OpenAI-compatible providers (use generic adapter)
OPENAI_COMPATIBLE = {
    "groq",
    "together",
    "fireworks",
    "mistral",
    "cerebras",
    "deepinfra",
    "openai",
}

# Providers that are NOT OpenAI-compatible (require dedicated plugins)
NOT_OPENAI_COMPATIBLE = {
    "anthropic",
}


def load_plugins(provider_names: list[str]) -> dict[str, type[BaseProvider]]:
    """Load provider plugins for the given provider names.

    For each provider name:
    - If a built-in adapter exists, use it
    - If the provider is known OpenAI-compatible, use GenericOpenAIProvider
    - Otherwise, check for AIKEYPOOL_PROVIDER_<NAME>_ENDPOINT env var
      and use GenericOpenAIProvider if found
    - If nothing works, log a warning and skip

    Args:
        provider_names: List of provider names to load

    Returns:
        Dict mapping provider name -> provider class
    """
    from ..groq import GroqProvider
    from ..github_models import GitHubModelsProvider
    from ..openrouter import OpenRouterProvider

    builtin_map = {
        "groq": GroqProvider,
        "github_models": GitHubModelsProvider,
        "openrouter": OpenRouterProvider,
    }

    loaded = {}

    for name in provider_names:
        name_lower = name.lower()

        # 1. Check built-in adapters
        if name_lower in builtin_map:
            loaded[name_lower] = builtin_map[name_lower]
            logger.info("PLUGIN: Loaded built-in adapter for %s", name_lower)
            continue

        # 2. Check if OpenAI-compatible via known list
        if name_lower in OPENAI_COMPATIBLE:
            loaded[name_lower] = _make_generic_factory(name_lower)
            logger.info("PLUGIN: Loaded generic OpenAI adapter for %s", name_lower)
            continue

        # 3. Check for custom endpoint in environment
        endpoint_key = f"AIKEYPOOL_PROVIDER_{name.upper()}_ENDPOINT"
        if os.environ.get(endpoint_key):
            loaded[name_lower] = _make_generic_factory(name_lower)
            logger.info("PLUGIN: Loaded generic adapter for %s (custom endpoint)", name_lower)
            continue

        # 4. Check if provider has keys but no adapter
        keys_key = f"AIKEYPOOL_PROVIDER_{name.upper()}_KEYS"
        if os.environ.get(keys_key):
            if name_lower not in NOT_OPENAI_COMPATIBLE:
                loaded[name_lower] = _make_generic_factory(name_lower)
                logger.warning(
                    "PLUGIN: No dedicated adapter for %s — using generic OpenAI adapter",
                    name_lower,
                )
                continue

        logger.warning(
            "PLUGIN: Cannot load adapter for %s — no compatible plugin found",
            name_lower,
        )

    return loaded


def _make_generic_factory(provider_name: str):
    """Create a factory function for GenericOpenAIProvider.

    Args:
        provider_name: Provider name

    Returns:
        Factory class that creates GenericOpenAIProvider instances
    """
    class _Factory:
        def __init__(self, **kwargs):
            self._name = provider_name

        def get_provider_name(self):
            return provider_name

        def get_endpoint(self):
            return GenericOpenAIProvider(provider_name).get_endpoint()

        def get_auth_headers(self, api_key):
            return GenericOpenAIProvider(provider_name).get_auth_headers(api_key)

        def chat(self, api_key, model, messages):
            return GenericOpenAIProvider(provider_name).chat(api_key, model, messages)

        def health_check(self, api_key, model=None):
            return GenericOpenAIProvider(provider_name).health_check(api_key, model)

    # Make it look like the provider class for isinstance checks
    _Factory.__name__ = f"GenericOpenAI_{provider_name}"
    _Factory.__qualname__ = f"GenericOpenAI_{provider_name}"

    return GenericOpenAIProvider


def get_plugin_providers() -> dict[str, str]:
    """Get status of all detectable providers.

    Returns:
        Dict mapping provider name -> status string
        ("builtin", "generic", "detected", "missing")
    """
    from ..provider_factory import PROVIDER_MAP

    statuses = {}

    # Check built-in
    for name in PROVIDER_MAP:
        statuses[name] = "builtin"

    # Check environment for additional providers
    for key, value in os.environ.items():
        if key.startswith("AIKEYPOOL_PROVIDER_") and key.endswith("_KEYS"):
            provider_name = key[len("AIKEYPOOL_PROVIDER_"):-len("_KEYS")].lower()
            if provider_name not in statuses:
                if value.strip():
                    # Has keys — check if we have an adapter
                    if provider_name in OPENAI_COMPATIBLE or provider_name in BUILTIN_PROVIDERS:
                        statuses[provider_name] = "generic"
                    else:
                        endpoint_key = f"AIKEYPOOL_PROVIDER_{provider_name.upper()}_ENDPOINT"
                        if os.environ.get(endpoint_key):
                            statuses[provider_name] = "generic"
                        else:
                            statuses[provider_name] = "detected"
                else:
                    statuses[provider_name] = "missing"

    return statuses
