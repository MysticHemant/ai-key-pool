"""Plugin loader for AI Key Pool.

Discovers and loads provider plugins automatically.
Uses the ManifestRegistry for dynamic provider discovery.
"""

import os
from typing import Optional

from ..base_provider import BaseProvider
from ..plugins.generic_openai import GenericOpenAIProvider
from ..manifest import manifest_registry, ProviderManifest
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
    "sambanova",
    "novita",
    "chutes",
}

# Providers that are NOT OpenAI-compatible (require dedicated plugins)
NOT_OPENAI_COMPATIBLE = {
    "anthropic",
}


def load_plugins(provider_names: list[str]) -> dict[str, type[BaseProvider]]:
    """Load provider plugins for the given provider names.

    For each provider name:
    - If a built-in adapter exists, use it
    - If the provider is in the manifest registry, use its adapter
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

        # 2. Check manifest registry
        manifest = manifest_registry.get(name_lower)
        if manifest:
            if manifest.adapter == "builtin":
                # Should be in builtin_map, but handle gracefully
                logger.info("PLUGIN: Loaded builtin adapter for %s (from manifest)", name_lower)
            elif manifest.adapter == "generic":
                loaded[name_lower] = _make_generic_factory(name_lower)
                logger.info("PLUGIN: Loaded generic OpenAI adapter for %s (from manifest)", name_lower)
                continue
            else:
                logger.info("PLUGIN: Loaded adapter %s for %s (from manifest)", manifest.adapter, name_lower)
                continue

        # 3. Check if OpenAI-compatible via known list
        if name_lower in OPENAI_COMPATIBLE:
            loaded[name_lower] = _make_generic_factory(name_lower)
            logger.info("PLUGIN: Loaded generic OpenAI adapter for %s", name_lower)
            continue

        # 4. Check for custom endpoint in environment
        endpoint_key = f"AIKEYPOOL_PROVIDER_{name.upper()}_ENDPOINT"
        if os.environ.get(endpoint_key):
            loaded[name_lower] = _make_generic_factory(name_lower)
            logger.info("PLUGIN: Loaded generic adapter for %s (custom endpoint)", name_lower)
            continue

        # 5. Check if provider has keys but no adapter
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
        GenericOpenAIProvider class
    """
    return GenericOpenAIProvider


def get_plugin_providers() -> dict[str, str]:
    """Get status of all detectable providers.

    Returns:
        Dict mapping provider name -> status string
        ("builtin", "generic", "detected", "missing")
    """
    from ..provider_factory import manifest_registry

    statuses = {}

    # Check manifest registry first
    for name, manifest in manifest_registry.get_all().items():
        if manifest.adapter == "builtin":
            statuses[name] = "builtin"
        else:
            statuses[name] = "generic"

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
