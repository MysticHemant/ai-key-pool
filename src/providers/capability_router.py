"""Capability-based router for AI Key Pool.

Routes requests to providers based on their capabilities rather than
provider names. The router selects the healthiest provider supporting
the requested capability.
"""

from typing import Optional, Callable
from .manifest import manifest_registry, ProviderManifest, CAPABILITY_REASONING
from ..key_pool import KeyManager, KeyRotator
from ..utils.config import Config
from ..utils.logger import get_logger


logger = get_logger("capability_router")


class CapabilityRouter:
    """Routes requests to providers based on capabilities.

    Uses the ManifestRegistry to find providers supporting a capability,
    then selects based on health and priority.
    """

    def __init__(self, config: Config, key_manager: KeyManager):
        """Initialize the capability router.

        Args:
            config: System configuration
            key_manager: Key manager instance
        """
        self.config = config
        self.key_manager = key_manager
        self.rotator = KeyRotator(config, key_manager)

    def route_by_capability(
        self,
        capability: str,
        exclude_providers: list[str] = None,
    ) -> list[ProviderManifest]:
        """Get providers supporting a capability, sorted by health and priority.

        Args:
            capability: Capability tag to search for
            exclude_providers: Provider IDs to exclude from results

        Returns:
            List of ProviderManifest sorted by priority (ascending),
            filtered to healthy providers only
        """
        exclude = set(exclude_providers or [])

        # Get all providers with the capability
        all_providers = manifest_registry.get_by_capability(capability)

        # Filter: enabled, healthy, not excluded
        result = []
        for manifest in all_providers:
            if not manifest.enabled:
                continue
            if manifest.provider_id in exclude:
                continue
            if manifest.health not in ("healthy", "unknown"):
                continue
            # Check if provider has healthy keys
            healthy_keys = self.key_manager.registry.get_healthy_keys(manifest.provider_id)
            if not healthy_keys:
                continue
            result.append(manifest)

        return result

    def get_healthy_provider_for_capability(
        self,
        capability: str,
        exclude_providers: list[str] = None,
    ) -> Optional[ProviderManifest]:
        """Get the best healthy provider for a capability.

        Args:
            capability: Capability tag to search for
            exclude_providers: Provider IDs to exclude

        Returns:
            Best ProviderManifest or None if no healthy provider found
        """
        providers = self.route_by_capability(capability, exclude_providers)
        return providers[0] if providers else None

    def execute_with_capability_routing(
        self,
        capability: str,
        request_fn: Callable,
        exclude_providers: list[str] = None,
    ) -> dict:
        """Execute a request using capability-based routing with fallback.

        Tries providers in priority order. If one fails, tries the next.
        Falls back to the configured active provider if all fail.

        Args:
            capability: Capability tag to route by
            request_fn: Function that takes (provider_name, api_key) and returns result
            exclude_providers: Provider IDs to exclude

        Returns:
            Dict with 'success', 'response', 'provider_used', 'error'
        """
        providers = self.route_by_capability(capability, exclude_providers)

        if not providers:
            # Fallback to active provider
            logger.warning("CAPABILITY ROUTER: No healthy providers for '%s', falling back to active", capability)
            return self._execute_with_provider(
                self.config.active_provider, request_fn
            )

        last_error = None
        for manifest in providers:
            result = self._execute_with_provider(manifest.provider_id, request_fn)
            if result["success"]:
                return result
            last_error = result.get("error", "unknown")
            logger.warning(
                "CAPABILITY ROUTER: Provider %s failed for '%s': %s",
                manifest.provider_id, capability, last_error,
            )

        # All providers failed
        return {
            "success": False,
            "response": None,
            "provider_used": None,
            "error": f"All providers failed for capability '{capability}': {last_error}",
        }

    def _execute_with_provider(
        self,
        provider_name: str,
        request_fn: Callable,
    ) -> dict:
        """Execute a request with a specific provider using key rotation.

        Args:
            provider_name: Provider to use
            request_fn: Function that takes (api_key) and returns result

        Returns:
            Dict with 'success', 'response', 'provider_used', 'error'
        """
        try:
            result = self.rotator.execute_with_rotation(provider_name, request_fn)
            if result.success:
                return {
                    "success": True,
                    "response": result.response,
                    "provider_used": provider_name,
                    "error": None,
                }
            else:
                return {
                    "success": False,
                    "response": None,
                    "provider_used": provider_name,
                    "error": result.error,
                }
        except Exception as e:
            return {
                "success": False,
                "response": None,
                "provider_used": provider_name,
                "error": str(e),
            }

    def update_provider_health(self, provider_id: str, health: str) -> None:
        """Update health status for a provider.

        Args:
            provider_id: Provider to update
            health: New health status
        """
        manifest_registry.update_health(provider_id, health)

    def get_provider_status_summary(self) -> dict:
        """Get a summary of all providers and their status.

        Returns:
            Dict with provider status information
        """
        summary = {
            "total_providers": 0,
            "healthy_providers": 0,
            "by_capability": {},
            "providers": {},
        }

        for manifest in manifest_registry.get_all().values():
            summary["total_providers"] += 1
            if manifest.health in ("healthy", "unknown"):
                summary["healthy_providers"] += 1

            summary["providers"][manifest.provider_id] = {
                "display_name": manifest.display_name,
                "health": manifest.health,
                "priority": manifest.priority,
                "capabilities": manifest.capabilities,
                "enabled": manifest.enabled,
            }

            for cap in manifest.capabilities:
                if cap not in summary["by_capability"]:
                    summary["by_capability"][cap] = []
                summary["by_capability"][cap].append(manifest.provider_id)

        return summary
