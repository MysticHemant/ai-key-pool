"""Provider factory for AI Key Pool.

Instantiates the correct provider adapter based on provider name.
The rest of the system never instantiates providers directly.
"""

from typing import Optional
from .base_provider import BaseProvider
from .github_models import GitHubModelsProvider
from .groq import GroqProvider
from .openrouter import OpenRouterProvider


PROVIDER_MAP: dict[str, type[BaseProvider]] = {
    "github_models": GitHubModelsProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
}


def create_provider(provider_name: str, **kwargs) -> BaseProvider:
    """Instantiate a provider by name.

    Args:
        provider_name: Provider identifier (e.g., 'groq', 'openrouter')
        **kwargs: Additional arguments passed to the provider constructor

    Returns:
        Configured provider instance

    Raises:
        ValueError: If provider_name is not registered
    """
    cls = PROVIDER_MAP.get(provider_name.lower())
    if not cls:
        available = ", ".join(sorted(PROVIDER_MAP.keys()))
        raise ValueError(
            f"Unknown provider: '{provider_name}'. Available: {available}"
        )
    return cls(**kwargs)


def list_providers() -> list[str]:
    """Return list of available provider names."""
    return sorted(PROVIDER_MAP.keys())
