"""Key rotator for AI Key Pool.

Implements automatic key rotation when requests fail due to
rate limiting, quota exhaustion, or authentication errors.
"""

from typing import Optional, Callable, Any
from dataclasses import dataclass

from .key_manager import KeyManager
from .key_registry import KeyEntry
from ..utils.config import Config
from ..utils.logger import (
    get_logger,
    log_rotation,
    log_retry,
    log_no_healthy_keys,
)


logger = get_logger("key_rotator")


class RotationError(Exception):
    """Raised when rotation fails because no healthy keys are available.

    Attributes:
        provider: The provider name that was exhausted.
    """

    def __init__(self, provider: str, message: Optional[str] = None):
        self.provider = provider
        self.message = message or f"No healthy keys available for provider: {provider}"
        super().__init__(self.message)


@dataclass
class RotationResult:
    """Result of a key rotation attempt."""
    success: bool
    key_used: Optional[str] = None
    retries: int = 0
    error: Optional[str] = None
    rotations: int = 0
    response: Any = None


# Error types that trigger rotation
ROTATION_ERRORS = {"rate_limit", "quota_exhausted", "auth_error"}


def should_rotate(error_type: str) -> bool:
    """Check if an error type should trigger key rotation.

    Args:
        error_type: Type of error

    Returns:
        True if rotation should occur
    """
    return error_type.lower() in ROTATION_ERRORS


class KeyRotator:
    """Automatic key rotation with retry logic.

    Handles rotating between keys when requests fail,
    with configurable retry counts.
    """

    def __init__(self, config: Config, key_manager: KeyManager):
        """Initialize the key rotator.

        Args:
            config: System configuration
            key_manager: Key manager instance
        """
        self.config = config
        self.key_manager = key_manager

    def execute_with_rotation(
        self,
        provider: str,
        request_fn: Callable[[str], Any],
        max_retries: Optional[int] = None,
    ) -> RotationResult:
        """Execute a request with automatic key rotation on failure.

        Args:
            provider: Provider name
            request_fn: Function that takes a key value and executes the request.
                       Should raise an exception or return an error tuple on failure.
            max_retries: Maximum number of retries (uses config default if None)

        Returns:
            RotationResult with details of what happened
        """
        if max_retries is None:
            max_retries = self.config.retry_count

        result = RotationResult(success=False)
        failed_key_ids: list[str] = []
        attempts = 0

        while attempts <= max_retries:
            # Get next available key, excluding all previously failed keys
            key = self.key_manager.get_next_key(provider, exclude_key_ids=failed_key_ids)

            if key is None:
                log_no_healthy_keys(logger, provider)
                result.error = f"No healthy keys available for provider: {provider}"
                return result

            # Track rotation
            if failed_key_ids:
                result.rotations += 1
                log_rotation(logger, failed_key_ids[-1], key.key_id, "Request failed")

            result.key_used = key.key_id
            attempts += 1
            result.retries = attempts

            try:
                # Execute the request
                response = request_fn(key.key_value)

                # Success
                self.key_manager.mark_success(key.key_id)
                result.success = True
                result.response = response
                return result

            except Exception as e:
                # Determine error type from exception
                error_type = self._classify_error(e)
                self.key_manager.mark_failure(key.key_id, error_type)
                failed_key_ids.append(key.key_id)

                if should_rotate(error_type):
                    log_retry(logger, key.key_id, attempts, max_retries)
                    continue
                else:
                    # Non-rotation error, fail immediately
                    result.error = str(e)
                    return result

        # Exhausted all retries
        result.error = f"Max retries ({max_retries}) exceeded for provider: {provider}"
        return result

    def _classify_error(self, error: Exception) -> str:
        """Classify an exception into an error type.

        Maps exception messages to normalized error categories using
        substring matching. Override this method to customize error
        classification for specific provider integrations.

        Args:
            error: Exception that occurred

        Returns:
            One of: "rate_limit", "quota_exhausted", "auth_error", "unknown"
        """
        error_str = str(error).lower()

        if "rate limit" in error_str or "429" in error_str:
            return "rate_limit"
        if "quota" in error_str or "exceeded" in error_str:
            return "quota_exhausted"
        if "auth" in error_str or "401" in error_str or "invalid" in error_str:
            return "auth_error"
        return "unknown"

    def force_rotate(self, provider: str, current_key_id: str) -> Optional[KeyEntry]:
        """Force rotation to next key regardless of health.

        Useful for manual intervention.

        Args:
            provider: Provider name
            current_key_id: Key to rotate away from

        Returns:
            Next key or None
        """
        return self.key_manager.get_next_key(provider, exclude_key_ids=[current_key_id])
