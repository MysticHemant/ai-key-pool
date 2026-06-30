"""Provider factory for AI Key Pool.

Instantiates the correct provider adapter based on provider name.
Supports built-in adapters, plugin adapters, and generic OpenAI-compatible
providers discovered automatically from environment variables.

Uses the ManifestRegistry for dynamic provider discovery and
capability-based routing.
"""

from typing import Optional
from .base_provider import BaseProvider
from .github_models import GitHubModelsProvider
from .groq import GroqProvider
from .openrouter import OpenRouterProvider
from .gemini import GeminiProvider
from .manifest import ManifestRegistry, manifest_registry, ProviderManifest
from ..utils.logger import get_logger


logger = get_logger("provider_factory")

# Built-in providers with dedicated adapters
_BUILTIN_PROVIDERS: dict[str, type[BaseProvider]] = {
    "github_models": GitHubModelsProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "gemini": GeminiProvider,
}

# Providers known to be OpenAI-compatible (use generic adapter)
_OPENAI_COMPATIBLE = {
    "groq", "together", "fireworks", "mistral", "cerebras",
    "deepinfra", "openai", "sambanova", "novita", "chutes",
}

# Providers that are NOT OpenAI-compatible
_NOT_OPENAI_COMPATIBLE = {"anthropic"}


def _discover_providers() -> None:
    """Auto-discover providers from environment variables.

    Scans for AIKEYPOOL_PROVIDER_*_KEYS and registers generic
    OpenAI-compatible adapters for unknown providers.
    Also populates the ManifestRegistry with provider manifests.
    """
    import os

    # First, register builtin providers with manifests
    for name, cls in _BUILTIN_PROVIDERS.items():
        if name not in manifest_registry:
            try:
                instance = cls()
                manifest = instance.get_manifest()
                if manifest:
                    manifest_registry.register(manifest)
            except Exception as e:
                logger.warning("Could not create manifest for builtin %s: %s", name, e)

    # Then discover from environment
    for key, value in os.environ.items():
        if key.startswith("AIKEYPOOL_PROVIDER_") and key.endswith("_KEYS"):
            provider_name = key[len("AIKEYPOOL_PROVIDER_"):-len("_KEYS")].lower()
            if provider_name and provider_name not in manifest_registry:
                if provider_name in _NOT_OPENAI_COMPATIBLE:
                    logger.info("PROVIDER DISCOVERY: %s detected (requires dedicated plugin)", provider_name)
                    continue

                # Create generic adapter and register manifest
                try:
                    from .plugins.generic_openai import GenericOpenAIProvider
                    generic = GenericOpenAIProvider(provider_name)
                    manifest = generic.get_manifest()
                    manifest_registry.register(manifest)
                except Exception as e:
                    logger.warning("Could not create manifest for %s: %s", provider_name, e)


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

    # Check builtin providers first
    cls = _BUILTIN_PROVIDERS.get(provider_name.lower())
    if cls:
        return cls(**kwargs)

    # Check if we have a manifest (auto-discovered generic provider)
    manifest = manifest_registry.get(provider_name.lower())
    if manifest and manifest.adapter == "generic":
        from .plugins.generic_openai import GenericOpenAIProvider
        return GenericOpenAIProvider(provider_name, **kwargs)

    # Check if provider has keys but no adapter
    import os
    keys_key = f"AIKEYPOOL_PROVIDER_{provider_name.upper()}_KEYS"
    if os.environ.get(keys_key):
        if provider_name.lower() not in _NOT_OPENAI_COMPATIBLE:
            from .plugins.generic_openai import GenericOpenAIProvider
            return GenericOpenAIProvider(provider_name, **kwargs)

    available = ", ".join(sorted(manifest_registry.list_provider_ids()))
    raise ValueError(
        f"Unknown provider: '{provider_name}'. Available: {available}"
    )


def list_providers() -> list[str]:
    """Return list of available provider names."""
    global _discovery_done
    if not _discovery_done:
        _discover_providers()
        _discovery_done = True
    return manifest_registry.list_provider_ids()


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
    for name, manifest in manifest_registry.get_all().items():
        status[name] = {
            "adapter": manifest.adapter,
            "display_name": manifest.display_name,
            "capabilities": manifest.capabilities,
            "priority": manifest.priority,
            "health": manifest.health,
            "enabled": manifest.enabled,
        }
    return status


def get_manifest_registry() -> ManifestRegistry:
    """Return the global manifest registry.

    Returns:
        ManifestRegistry instance
    """
    global _discovery_done
    if not _discovery_done:
        _discover_providers()
        _discovery_done = True
    return manifest_registry
