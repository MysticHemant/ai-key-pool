"""Provider manifest system for AI Key Pool.

Defines the ProviderManifest dataclass and ManifestRegistry for
dynamic provider discovery and capability-based routing.
"""

from dataclasses import dataclass, field
from typing import Optional
from ..utils.logger import get_logger


logger = get_logger("manifest")


@dataclass
class ProviderManifest:
    """Metadata describing an AI provider's capabilities and configuration.

    Attributes:
        provider_id: Unique identifier (e.g., 'groq', 'github_models')
        display_name: Human-readable name (e.g., 'Groq', 'GitHub Models')
        adapter: Adapter type: 'builtin', 'generic', or module path
        supported_models: List of model identifiers this provider supports
        capabilities: List of capability tags this provider supports
        priority: Lower number = higher priority (default 10)
        health: Current health status: 'healthy', 'degraded', 'unhealthy', 'unknown'
        enabled: Whether this provider is available for routing
        endpoint: API endpoint URL (for generic providers)
        default_model: Default model to use if none specified
    """
    provider_id: str
    display_name: str
    adapter: str  # "builtin", "generic", or module path
    supported_models: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    priority: int = 10
    health: str = "unknown"
    enabled: bool = True
    endpoint: str = ""
    default_model: str = ""


# Standard capability constants
CAPABILITY_REASONING = "reasoning"
CAPABILITY_CODING = "coding"
CAPABILITY_LONG_CONTEXT = "long_context"
CAPABILITY_VISION = "vision"
CAPABILITY_SEARCH = "search"
CAPABILITY_FAST_INFERENCE = "fast_inference"
CAPABILITY_LOW_COST = "low_cost"


class ManifestRegistry:
    """Registry of provider manifests for dynamic discovery.

    Provides capability-based queries and health-aware provider selection.
    """

    def __init__(self):
        self._manifests: dict[str, ProviderManifest] = {}

    def __contains__(self, provider_id: str) -> bool:
        """Check if a provider is registered.

        Args:
            provider_id: Provider identifier to check

        Returns:
            True if provider is registered
        """
        return provider_id in self._manifests

    def register(self, manifest: ProviderManifest) -> None:
        """Register a provider manifest.

        Args:
            manifest: ProviderManifest to register
        """
        self._manifests[manifest.provider_id] = manifest
        logger.info(
            "MANIFEST REGISTERED: %s (capabilities=%s, priority=%d, health=%s)",
            manifest.provider_id,
            manifest.capabilities,
            manifest.priority,
            manifest.health,
        )

    def unregister(self, provider_id: str) -> None:
        """Remove a provider manifest.

        Args:
            provider_id: Provider to remove
        """
        if provider_id in self._manifests:
            del self._manifests[provider_id]
            logger.info("MANIFEST UNREGISTERED: %s", provider_id)

    def get(self, provider_id: str) -> Optional[ProviderManifest]:
        """Get manifest for a specific provider.

        Args:
            provider_id: Provider identifier

        Returns:
            ProviderManifest or None if not found
        """
        return self._manifests.get(provider_id)

    def get_all(self) -> dict[str, ProviderManifest]:
        """Get all registered manifests.

        Returns:
            Dict mapping provider_id -> ProviderManifest
        """
        return dict(self._manifests)

    def get_enabled(self) -> dict[str, ProviderManifest]:
        """Get all enabled provider manifests.

        Returns:
            Dict of enabled providers
        """
        return {pid: m for pid, m in self._manifests.items() if m.enabled}

    def get_healthy(self) -> dict[str, ProviderManifest]:
        """Get all healthy provider manifests.

        Returns:
            Dict of healthy providers
        """
        return {
            pid: m for pid, m in self._manifests.items()
            if m.enabled and m.health in ("healthy", "unknown")
        }

    def get_by_capability(self, capability: str) -> list[ProviderManifest]:
        """Get providers supporting a specific capability, sorted by priority.

        Args:
            capability: Capability tag to search for

        Returns:
            List of ProviderManifest sorted by priority (ascending)
        """
        matches = [
            m for m in self._manifests.values()
            if m.enabled and capability in m.capabilities
        ]
        return sorted(matches, key=lambda m: m.priority)

    def get_healthy_by_capability(self, capability: str) -> list[ProviderManifest]:
        """Get healthy providers supporting a capability, sorted by priority.

        Args:
            capability: Capability tag to search for

        Returns:
            List of healthy ProviderManifest sorted by priority
        """
        matches = [
            m for m in self._manifests.values()
            if m.enabled and capability in m.capabilities and m.health in ("healthy", "unknown")
        ]
        return sorted(matches, key=lambda m: m.priority)

    def update_health(self, provider_id: str, health: str) -> None:
        """Update health status for a provider.

        Args:
            provider_id: Provider to update
            health: New health status ('healthy', 'degraded', 'unhealthy', 'unknown')
        """
        if provider_id in self._manifests:
            old_health = self._manifests[provider_id].health
            self._manifests[provider_id].health = health
            if old_health != health:
                logger.info(
                    "HEALTH UPDATE: %s %s -> %s",
                    provider_id, old_health, health,
                )

    def set_enabled(self, provider_id: str, enabled: bool) -> None:
        """Enable or disable a provider.

        Args:
            provider_id: Provider to update
            enabled: Whether provider should be enabled
        """
        if provider_id in self._manifests:
            self._manifests[provider_id].enabled = enabled
            logger.info("ENABLE UPDATE: %s enabled=%s", provider_id, enabled)

    def list_provider_ids(self) -> list[str]:
        """Get sorted list of all provider IDs.

        Returns:
            Sorted list of provider identifiers
        """
        return sorted(self._manifests.keys())

    def list_capabilities(self) -> list[str]:
        """Get all unique capabilities across all providers.

        Returns:
            Sorted list of unique capability tags
        """
        caps = set()
        for m in self._manifests.values():
            caps.update(m.capabilities)
        return sorted(caps)

    def to_dict(self) -> dict[str, dict]:
        """Export all manifests as dicts for serialization.

        Returns:
            Dict mapping provider_id -> manifest dict
        """
        result = {}
        for pid, m in self._manifests.items():
            result[pid] = {
                "provider_id": m.provider_id,
                "display_name": m.display_name,
                "adapter": m.adapter,
                "supported_models": m.supported_models,
                "capabilities": m.capabilities,
                "priority": m.priority,
                "health": m.health,
                "enabled": m.enabled,
                "endpoint": m.endpoint,
                "default_model": m.default_model,
            }
        return result


# Global manifest registry instance
manifest_registry = ManifestRegistry()
