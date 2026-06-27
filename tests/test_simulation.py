"""Test simulation for AI Key Pool.

Demonstrates key management, rotation, and health tracking
without making real API calls. Uses assertions for verification.
"""

import sys
from pathlib import Path

# Add project root to path (src is a sub-package)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.key_pool import KeyManager, KeyRotator, RotationResult
from src.key_pool.key_registry import KeyStatus
from src.utils.config import Config


# Helper functions and simulated error classes
def simulate_successful_request(key_value: str) -> str:
    """Simulate a successful API request."""
    return f"Response from {key_value[:8]}..."


class SimulatedRateLimitError(Exception):
    """Simulated rate limit error."""
    pass


class SimulatedQuotaError(Exception):
    """Simulated quota exhaustion error."""
    pass


def simulate_rate_limit_request(key_value: str) -> str:
    """Simulate a request that hits rate limit."""
    if "key-1" in key_value:
        raise SimulatedRateLimitError("429 Rate limit exceeded")
    return f"Response from {key_value[:8]}..."


def simulate_quota_exhausted_request(key_value: str) -> str:
    """Simulate a request where quota is exhausted."""
    if "key-1" in key_value or "key-2" in key_value:
        raise SimulatedQuotaError("Quota exceeded for this API key")
    return f"Response from {key_value[:8]}..."


def clean_data():
    """Remove stale test data before running."""
    data_dir = Path(__file__).parent.parent / "data"
    for f in ["key_registry.json", "key_health.json"]:
        p = data_dir / f
        if p.exists():
            p.unlink()


def test_basic_operations():
    """Test basic key management operations."""
    print("\n=== Test 1: Basic Key Management ===")
    clean_data()

    data_dir = Path(__file__).parent.parent / "data"
    manager = KeyManager(data_dir)

    # Register keys for multiple providers
    manager.register_key("openai-key-1", "openai", "sk-openai-key-1-abc123")
    manager.register_key("openai-key-2", "openai", "sk-openai-key-2-def456")
    manager.register_key("openai-key-3", "openai", "sk-openai-key-3-ghi789")
    manager.register_key("anthropic-key-1", "anthropic", "sk-ant-key-1-xyz789")
    manager.register_key("anthropic-key-2", "anthropic", "sk-ant-key-2-uvw456")

    # Verify registration
    stats = manager.get_all_stats()
    assert stats["registry"]["total_keys"] == 5, f"Expected 5 keys, got {stats['registry']['total_keys']}"
    assert stats["registry"]["by_provider"]["openai"] == 3
    assert stats["registry"]["by_provider"]["anthropic"] == 2

    # Get active key
    key = manager.get_active_key("openai")
    assert key is not None, "Should return an active key"
    assert key.key_id == "openai-key-1", f"Expected openai-key-1, got {key.key_id}"

    # Record success
    manager.mark_success("openai-key-1")
    status = manager.get_key_status("openai-key-1")
    assert status["success_count"] == 1
    assert status["failure_count"] == 0
    assert status["health_status"] == "healthy"

    print("  PASSED")


def test_rotation_on_rate_limit():
    """Test automatic rotation on rate limit errors."""
    print("\n=== Test 2: Rotation on Rate Limit ===")

    data_dir = Path(__file__).parent.parent / "data"
    manager = KeyManager(data_dir)
    config = Config(retry_count=3)
    rotator = KeyRotator(config, manager)

    result = rotator.execute_with_rotation("openai", simulate_rate_limit_request)

    assert result.success is True, f"Expected success, got error: {result.error}"
    assert result.key_used == "openai-key-2", f"Expected openai-key-2, got {result.key_used}"
    assert result.rotations >= 1, "Expected at least 1 rotation"
    print("  PASSED")


def test_rotation_on_quota_exhaustion():
    """Test rotation when quota is exhausted."""
    print("\n=== Test 3: Rotation on Quota Exhaustion ===")

    data_dir = Path(__file__).parent.parent / "data"
    manager = KeyManager(data_dir)
    config = Config(retry_count=3)
    rotator = KeyRotator(config, manager)

    result = rotator.execute_with_rotation("openai", simulate_quota_exhausted_request)

    # key-1 and key-2 fail, key-3 should succeed
    assert result.success is True, f"Expected success, got error: {result.error}"
    assert result.key_used == "openai-key-3", f"Expected openai-key-3, got {result.key_used}"
    assert result.rotations >= 1, "Expected at least 1 rotation"

    # Verify key-1 and key-2 have failures recorded
    s1 = manager.get_key_status("openai-key-1")
    s2 = manager.get_key_status("openai-key-2")
    assert s1["failure_count"] >= 1, "key-1 should have failures"
    assert s2["failure_count"] >= 1, "key-2 should have failures"
    print("  PASSED")


def test_no_healthy_keys():
    """Test behavior when no healthy keys are available."""
    print("\n=== Test 4: No Healthy Keys Available ===")

    data_dir = Path(__file__).parent.parent / "data"
    manager = KeyManager(data_dir)

    # Disable all Anthropic keys
    manager.disable_key("anthropic-key-1", "Manual disable for test")
    manager.disable_key("anthropic-key-2", "Manual disable for test")

    config = Config(retry_count=2)
    rotator = KeyRotator(config, manager)

    result = rotator.execute_with_rotation("anthropic", simulate_successful_request)

    assert result.success is False, "Expected failure when no healthy keys"
    assert "No healthy keys" in result.error, f"Expected 'No healthy keys' error, got: {result.error}"
    print("  PASSED")


def test_health_tracking():
    """Test health tracking over multiple requests."""
    print("\n=== Test 5: Health Tracking ===")

    data_dir = Path(__file__).parent.parent / "data"
    manager = KeyManager(data_dir)

    manager.register_key("health-test-key", "test-provider", "sk-test-health-123")

    # Simulate: success, success, fail, fail, fail, success, success
    requests = [True, True, False, False, False, True, True]
    for success in requests:
        if success:
            manager.mark_success("health-test-key")
        else:
            manager.mark_failure("health-test-key", "rate_limit")

    status = manager.get_key_status("health-test-key")
    assert status["success_count"] == 4, f"Expected 4 successes, got {status['success_count']}"
    assert status["failure_count"] == 3, f"Expected 3 failures, got {status['failure_count']}"
    assert status["consecutive_failures"] == 0, "Consecutive failures should be 0 after final successes"
    assert status["health_status"] == "healthy", f"Expected healthy, got {status['health_status']}"
    print("  PASSED")


def test_input_validation():
    """Test that register_key validates inputs."""
    print("\n=== Test 6: Input Validation ===")

    data_dir = Path(__file__).parent.parent / "data"
    manager = KeyManager(data_dir)

    # Empty key_id
    try:
        manager.register_key("", "openai", "sk-key")
        assert False, "Should have raised ValueError for empty key_id"
    except ValueError:
        pass

    # Empty provider
    try:
        manager.register_key("key-1", "", "sk-key")
        assert False, "Should have raised ValueError for empty provider"
    except ValueError:
        pass

    # Empty key_value
    try:
        manager.register_key("key-1", "openai", "")
        assert False, "Should have raised ValueError for empty key_value"
    except ValueError:
        pass

    # Whitespace-only values
    try:
        manager.register_key("  ", "openai", "sk-key")
        assert False, "Should have raised ValueError for whitespace key_id"
    except ValueError:
        pass

    print("  PASSED")


def test_force_rotate():
    """Test force rotation to next key."""
    print("\n=== Test 7: Force Rotate ===")

    data_dir = Path(__file__).parent.parent / "data"
    manager = KeyManager(data_dir)
    config = Config()
    rotator = KeyRotator(config, manager)

    new_key = rotator.force_rotate("openai", "openai-key-1")
    assert new_key is not None, "Should return a key after force rotate"
    assert new_key.key_id != "openai-key-1", "Should rotate away from specified key"
    print("  PASSED")


def main():
    """Run all simulation tests."""
    print("AI Key Pool - Simulation Tests")
    print("This demonstrates key management without real API calls.\n")

    test_basic_operations()
    test_rotation_on_rate_limit()
    test_rotation_on_quota_exhaustion()
    test_no_healthy_keys()
    test_health_tracking()
    test_input_validation()
    test_force_rotate()

    print("\n=== All Tests Complete ===")
    print("All tests passed!")


if __name__ == "__main__":
    main()
