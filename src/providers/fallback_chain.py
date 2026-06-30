"""Fallback chain for AI Key Pool reliability.

Provides automatic failover between providers and deterministic
fallback when all providers fail. Never fails due to a single
provider failure.
"""

import time
from typing import Optional, Callable, Any
from .capability_router import CapabilityRouter
from .manifest import manifest_registry
from ..key_pool import KeyManager
from ..utils.config import Config
from ..utils.logger import get_logger


logger = get_logger("fallback_chain")


class FallbackResult:
    """Result from a fallback chain execution."""

    def __init__(
        self,
        success: bool,
        response: Any = None,
        provider_used: str = None,
        error: Optional[str] = None,
        attempts: list[dict] = None,
        deterministic_fallback: bool = False,
    ):
        self.success = success
        self.response = response
        self.provider_used = provider_used
        self.error = error
        self.attempts = attempts or []
        self.deterministic_fallback = deterministic_fallback


class FallbackChain:
    """Manages provider failover and deterministic fallback.

    Guarantees:
    1. Try different providers in priority order
    2. Retry with exponential backoff
    3. Deterministic fallback if all providers fail
    4. Never terminate workflow due to provider failure
    """

    def __init__(self, config: Config, key_manager: KeyManager):
        self.config = config
        self.key_manager = key_manager
        self.router = CapabilityRouter(config, key_manager)

    def execute_with_fallback(
        self,
        capability: str,
        request_fn: Callable,
        deterministic_fn: Optional[Callable] = None,
        max_retries_per_provider: int = 1,
        exclude_providers: list[str] = None,
    ) -> FallbackResult:
        """Execute a request with full fallback chain.

        Fallback order:
        1. Try providers with the requested capability (priority order)
        2. Retry failed providers with backoff
        3. Try all providers without capability filter
        4. Execute deterministic fallback

        Args:
            capability: Capability to route by
            request_fn: Function that takes (api_key) and returns result
            deterministic_fn: Optional deterministic fallback function
            max_retries_per_provider: Max retries per provider
            exclude_providers: Providers to exclude

        Returns:
            FallbackResult with the outcome
        """
        all_attempts = []

        # Phase 1: Try capability-matched providers
        providers = self.router.route_by_capability(capability, exclude_providers)
        if providers:
            for manifest in providers:
                for retry in range(max_retries_per_provider):
                    result = self._try_provider(manifest.provider_id, request_fn)
                    all_attempts.append(result)

                    if result["success"]:
                        return FallbackResult(
                            success=True,
                            response=result["response"],
                            provider_used=manifest.provider_id,
                            attempts=all_attempts,
                        )

                    logger.warning(
                        "FALLBACK: Provider %s failed (attempt %d/%d): %s",
                        manifest.provider_id, retry + 1, max_retries_per_provider,
                        result.get("error", "unknown"),
                    )

                    # Exponential backoff on retry
                    if retry < max_retries_per_provider - 1:
                        backoff = min(2 ** retry, 5)
                        time.sleep(backoff)

        # Phase 2: Try all providers (without capability filter)
        all_providers = manifest_registry.get_healthy()
        exclude_set = set(exclude_providers or [])
        for manifest in all_providers.values():
            if manifest.provider_id in exclude_set:
                continue
            # Skip providers already tried
            if any(a["provider"] == manifest.provider_id for a in all_attempts):
                continue

            result = self._try_provider(manifest.provider_id, request_fn)
            all_attempts.append(result)

            if result["success"]:
                logger.info(
                    "FALLBACK: Provider %s succeeded (no capability match)",
                    manifest.provider_id,
                )
                return FallbackResult(
                    success=True,
                    response=result["response"],
                    provider_used=manifest.provider_id,
                    attempts=all_attempts,
                )

        # Phase 3: Deterministic fallback
        if deterministic_fn:
            logger.info("FALLBACK: All providers failed, using deterministic fallback")
            try:
                deterministic_result = deterministic_fn()
                return FallbackResult(
                    success=True,
                    response=deterministic_result,
                    provider_used="deterministic",
                    attempts=all_attempts,
                    deterministic_fallback=True,
                )
            except Exception as e:
                logger.error("FALLBACK: Deterministic fallback failed: %s", e)

        # All attempts failed
        return FallbackResult(
            success=False,
            response=None,
            provider_used=None,
            error=f"All {len(all_attempts)} attempts failed",
            attempts=all_attempts,
        )

    def _try_provider(self, provider_id: str, request_fn: Callable) -> dict:
        """Try executing a request with a specific provider.

        Args:
            provider_id: Provider to try
            request_fn: Function that takes (api_key) and returns result

        Returns:
            Dict with 'success', 'response', 'provider', 'error'
        """
        try:
            result = self.router.rotator.execute_with_rotation(
                provider_id, request_fn,
            )
            if result.success:
                return {
                    "success": True,
                    "response": result.response,
                    "provider": provider_id,
                    "error": None,
                }
            else:
                return {
                    "success": False,
                    "response": None,
                    "provider": provider_id,
                    "error": result.error,
                }
        except Exception as e:
            return {
                "success": False,
                "response": None,
                "provider": provider_id,
                "error": str(e),
            }

    def execute_with_simple_fallback(
        self,
        capability: str,
        request_fn: Callable,
        exclude_providers: list[str] = None,
    ) -> FallbackResult:
        """Execute with simple fallback (no retries, no deterministic).

        Used for lightweight operations where speed matters more than
        guaranteed success.

        Args:
            capability: Capability to route by
            request_fn: Function that takes (api_key) and returns result
            exclude_providers: Providers to exclude

        Returns:
            FallbackResult
        """
        providers = self.router.route_by_capability(capability, exclude_providers)

        for manifest in providers:
            result = self._try_provider(manifest.provider_id, request_fn)
            if result["success"]:
                return FallbackResult(
                    success=True,
                    response=result["response"],
                    provider_used=manifest.provider_id,
                    attempts=[result],
                )

        return FallbackResult(
            success=False,
            response=None,
            provider_used=None,
            error=f"No healthy provider for capability '{capability}'",
            attempts=[],
        )


def create_fallback_chain(config: Config, key_manager: KeyManager) -> FallbackChain:
    """Create a fallback chain instance.

    Args:
        config: System configuration
        key_manager: Key manager instance

    Returns:
        FallbackChain instance
    """
    return FallbackChain(config, key_manager)
