"""Key manager for AI Key Pool.

Provides high-level interface for key selection, usage tracking,
and status management. Coordinates between registry and health checker.
"""

from typing import Optional
from pathlib import Path

from .key_registry import KeyRegistry, KeyEntry, KeyStatus
from ..health.health_checker import HealthChecker
from ..utils.logger import (
    get_logger,
    log_key_selected,
    log_key_disabled,
    log_request_success,
    log_request_failure,
)


logger = get_logger("key_manager")


class KeyManager:
    """High-level key management interface.

    Provides methods for selecting keys, recording results,
    and managing key lifecycle.
    """

    def __init__(self, data_dir: Path, max_consecutive_failures: int = 5):
        """Initialize the key manager.

        Args:
            data_dir: Directory for data storage
            max_consecutive_failures: Auto-disable key after this many consecutive failures
        """
        self.data_dir = data_dir
        self.max_consecutive_failures = max_consecutive_failures
        self.registry = KeyRegistry(data_dir)
        self.health_checker = HealthChecker(data_dir)

    def register_key(
        self,
        key_id: str,
        provider: str,
        key_value: str,
    ) -> KeyEntry:
        """Register a new API key.

        Args:
            key_id: Unique identifier
            provider: Provider name
            key_value: The API key value

        Returns:
            Created KeyEntry
        """
        return self.registry.register_key(
            key_id=key_id,
            provider=provider,
            key_value=key_value,
        )

    def get_active_key(self, provider: str) -> Optional[KeyEntry]:
        """Get the current active key for a provider.

        Returns the first healthy key that is active.

        Args:
            provider: Provider name

        Returns:
            Active KeyEntry or None if no healthy keys
        """
        healthy_keys = self.registry.get_healthy_keys(provider)

        # Prefer keys marked as ACTIVE
        for key in healthy_keys:
            if key.status == KeyStatus.ACTIVE:
                log_key_selected(logger, key.key_id, provider)
                return key

        # Fall back to any healthy key
        if healthy_keys:
            key = healthy_keys[0]
            log_key_selected(logger, key.key_id, provider)
            return key

        return None

    def get_next_key(
        self,
        provider: str,
        exclude_key_ids: Optional[list[str]] = None,
    ) -> Optional[KeyEntry]:
        """Get the next available healthy key.

        Useful for rotation when current key fails.

        Args:
            provider: Provider name
            exclude_key_ids: Key IDs to exclude (e.g., previously failed keys)

        Returns:
            Next healthy KeyEntry or None
        """
        exclude = set(exclude_key_ids or [])
        healthy_keys = self.registry.get_healthy_keys(provider)

        for key in healthy_keys:
            if key.key_id not in exclude:
                log_key_selected(logger, key.key_id, provider)
                return key

        return None

    def mark_success(self, key_id: str) -> None:
        """Mark a key usage as successful.

        Args:
            key_id: Unique key identifier
        """
        self.registry.record_usage(key_id, success=True)
        self.health_checker.record_success(key_id)
        log_request_success(logger, key_id)

    def mark_failure(self, key_id: str, error_type: str = "unknown") -> None:
        """Mark a key usage as failed.

        Records the failure in both the registry and health checker.
        If consecutive failures reach the configured threshold, the key
        is automatically disabled.

        Args:
            key_id: Unique key identifier
            error_type: Classification of the failure. Common values:
                "rate_limit", "quota_exhausted", "auth_error", "unknown"
        """
        self.registry.record_usage(key_id, success=False)
        self.health_checker.record_failure(key_id)
        log_request_failure(logger, key_id, error_type)

        # Auto-disable after too many consecutive failures
        health = self.health_checker.get_health(key_id)
        if health.consecutive_failures >= self.max_consecutive_failures:
            self.disable_key(key_id, f"Too many consecutive failures ({health.consecutive_failures})")

    def disable_key(self, key_id: str, reason: str = "Manual disable") -> None:
        """Disable a key.

        Args:
            key_id: Unique key identifier
            reason: Reason for disabling
        """
        self.registry.disable_key(key_id)
        log_key_disabled(logger, key_id, reason)

    def enable_key(self, key_id: str) -> None:
        """Enable a previously disabled key.

        Args:
            key_id: Unique key identifier
        """
        self.registry.enable_key(key_id)
        self.health_checker.reset_health(key_id)

    def get_key_status(self, key_id: str) -> Optional[dict]:
        """Get detailed status of a key.

        Args:
            key_id: Unique key identifier

        Returns:
            Dictionary with key status info or None
        """
        entry = self.registry.get_key(key_id)
        if not entry:
            return None

        health = self.health_checker.get_health(key_id)

        return {
            "key_id": entry.key_id,
            "provider": entry.provider,
            "status": entry.status.value,
            "last_used": entry.last_used,
            "failure_count": entry.failure_count,
            "success_count": entry.success_count,
            "health_status": health.status.value,
            "consecutive_failures": health.consecutive_failures,
            "last_success": health.last_success,
            "last_failure": health.last_failure,
        }

    def get_provider_summary(self, provider: str) -> dict:
        """Get summary of all keys for a provider.

        Args:
            provider: Provider name

        Returns:
            Dictionary with provider summary
        """
        keys = self.registry.get_keys_for_provider(provider)
        healthy = self.registry.get_healthy_keys(provider)

        return {
            "provider": provider,
            "total_keys": len(keys),
            "healthy_keys": len(healthy),
            "keys": [
                {
                    "key_id": k.key_id,
                    "status": k.status.value,
                    "failure_count": k.failure_count,
                    "success_count": k.success_count,
                }
                for k in keys
            ],
        }

    def get_all_stats(self) -> dict:
        """Get overall statistics.

        Returns:
            Dictionary with all stats
        """
        return {
            "registry": self.registry.get_stats(),
            "health": self.health_checker.get_stats(),
        }
