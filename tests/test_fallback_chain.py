"""Tests for the Fallback Chain module."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.providers.manifest import ManifestRegistry, ProviderManifest, CAPABILITY_REASONING
from src.providers.fallback_chain import FallbackChain, FallbackResult


def _make_registry_with_providers():
    """Create a registry with test providers."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="p1", display_name="P1", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], priority=1, health="healthy")
    m2 = ProviderManifest(provider_id="p2", display_name="P2", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], priority=2, health="healthy")
    m3 = ProviderManifest(provider_id="p3", display_name="P3", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], priority=3, health="unhealthy")
    for m in [m1, m2, m3]:
        registry.register(m)
    return registry


def test_fallback_result_defaults():
    """Test FallbackResult default values."""
    r = FallbackResult(success=False)
    assert r.success is False
    assert r.response is None
    assert r.provider_used is None
    assert r.error is None
    assert r.attempts == []
    assert r.deterministic_fallback is False


def test_fallback_result_with_values():
    """Test FallbackResult with custom values."""
    r = FallbackResult(
        success=True,
        response="hello",
        provider_used="groq",
        attempts=[{"provider": "groq", "success": True}],
        deterministic_fallback=False,
    )
    assert r.success is True
    assert r.response == "hello"
    assert r.provider_used == "groq"
    assert len(r.attempts) == 1


def test_fallback_chain_init():
    """Test FallbackChain initializes correctly."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "p1"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.fallback_chain.manifest_registry", registry):
        chain = FallbackChain(config, km)
        assert chain.router is not None


def test_execute_with_fallback_success():
    """Test execute_with_fallback succeeds on first provider."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "p1"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.fallback_chain.manifest_registry", registry):
        chain = FallbackChain(config, km)
        request_fn = MagicMock(return_value="success")
        result = chain.execute_with_fallback(
            CAPABILITY_REASONING,
            request_fn,
            max_retries_per_provider=1,
        )

    assert result.success is True
    assert result.response == "success"
    assert result.provider_used == "p1"


def test_execute_with_fallback_fallback_to_next_provider():
    """Test execute_with_fallback falls back to next provider."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "p1"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.fallback_chain.manifest_registry", registry):
        chain = FallbackChain(config, km)
        # First call fails, second succeeds
        call_count = 0
        def request_fn(api_key):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Provider failed")
            return "success"

        result = chain.execute_with_fallback(
            CAPABILITY_REASONING,
            request_fn,
            max_retries_per_provider=1,
        )

    assert result.success is True
    assert result.response == "success"
    assert result.provider_used == "p2"


def test_execute_with_fallback_deterministic():
    """Test execute_with_fallback uses deterministic fallback when all providers fail."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "p1"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.fallback_chain.manifest_registry", registry):
        chain = FallbackChain(config, km)
        request_fn = MagicMock(side_effect=Exception("All fail"))
        deterministic_fn = MagicMock(return_value="fallback_result")

        result = chain.execute_with_fallback(
            CAPABILITY_REASONING,
            request_fn,
            deterministic_fn=deterministic_fn,
            max_retries_per_provider=1,
        )

    assert result.success is True
    assert result.response == "fallback_result"
    assert result.deterministic_fallback is True
    assert deterministic_fn.called


def test_execute_with_simple_fallback():
    """Test execute_with_simple_fallback without retries."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "p1"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]  # All providers have healthy keys

    with patch("src.providers.fallback_chain.manifest_registry", registry):
        with patch("src.providers.capability_router.manifest_registry", registry):
            chain = FallbackChain(config, km)
            request_fn = MagicMock(return_value="success")
            result = chain.execute_with_simple_fallback(
                CAPABILITY_REASONING,
                request_fn,
            )

    assert result.success is True
    assert result.response == "success"


def test_execute_with_simple_fallback_all_fail():
    """Test execute_with_simple_fallback returns failure when all providers fail."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "p1"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.fallback_chain.manifest_registry", registry):
        with patch("src.providers.capability_router.manifest_registry", registry):
            chain = FallbackChain(config, km)
            request_fn = MagicMock(side_effect=Exception("All fail"))
            result = chain.execute_with_simple_fallback(
                CAPABILITY_REASONING,
                request_fn,
            )

    assert result.success is False
    assert result.error is not None


def test_execute_with_fallback_exclude_providers():
    """Test execute_with_fallback respects exclude_providers."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "p1"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.fallback_chain.manifest_registry", registry):
        chain = FallbackChain(config, km)
        request_fn = MagicMock(return_value="success")
        result = chain.execute_with_fallback(
            CAPABILITY_REASONING,
            request_fn,
            exclude_providers=["p1", "p2"],
            max_retries_per_provider=1,
        )

    # p1 and p2 excluded, p3 is unhealthy, so deterministic fallback or failure
    assert result.success is False  # No providers available


def test_execute_with_fallback_records_attempts():
    """Test execute_with_fallback records all attempts."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "p1"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.fallback_chain.manifest_registry", registry):
        chain = FallbackChain(config, km)
        call_count = 0
        def request_fn(api_key):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("Fail")
            return "success"

        result = chain.execute_with_fallback(
            CAPABILITY_REASONING,
            request_fn,
            max_retries_per_provider=1,
        )

    assert len(result.attempts) >= 2


def run_all():
    """Run all fallback chain tests."""
    tests = [
        test_fallback_result_defaults,
        test_fallback_result_with_values,
        test_fallback_chain_init,
        test_execute_with_fallback_success,
        test_execute_with_fallback_fallback_to_next_provider,
        test_execute_with_fallback_deterministic,
        test_execute_with_simple_fallback,
        test_execute_with_simple_fallback_all_fail,
        test_execute_with_fallback_exclude_providers,
        test_execute_with_fallback_records_attempts,
    ]
    for test in tests:
        test()
    return len(tests)


if __name__ == "__main__":
    n = run_all()
    print(f"All {n} fallback chain tests passed!")
