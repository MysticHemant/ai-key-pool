"""Tests for the Capability Router module."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.providers.manifest import ManifestRegistry, ProviderManifest, CAPABILITY_REASONING, CAPABILITY_CODING
from src.providers.capability_router import CapabilityRouter


def _make_registry_with_providers():
    """Create a registry with test providers."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="fast", display_name="Fast", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING, CAPABILITY_CODING], priority=1, health="healthy")
    m2 = ProviderManifest(provider_id="slow", display_name="Slow", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], priority=10, health="healthy")
    m3 = ProviderManifest(provider_id="unhealthy", display_name="Unhealthy", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], priority=5, health="unhealthy")
    m4 = ProviderManifest(provider_id="coding_only", display_name="Coding Only", adapter="builtin",
                          capabilities=[CAPABILITY_CODING], priority=3, health="healthy")
    for m in [m1, m2, m3, m4]:
        registry.register(m)
    return registry


def test_route_by_capability_returns_healthy_sorted():
    """Test route_by_capability returns healthy providers sorted by priority."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "fast"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]  # Has healthy keys

    with patch("src.providers.capability_router.manifest_registry", registry):
        router = CapabilityRouter(config, km)
        result = router.route_by_capability(CAPABILITY_REASONING)

    assert len(result) == 2  # fast (priority 1) and slow (priority 10), unhealthy excluded
    assert result[0].provider_id == "fast"
    assert result[1].provider_id == "slow"


def test_route_by_capability_excludes_providers():
    """Test route_by_capability excludes specified providers."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "fast"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.capability_router.manifest_registry", registry):
        router = CapabilityRouter(config, km)
        result = router.route_by_capability(CAPABILITY_REASONING, exclude_providers=["fast"])

    assert len(result) == 1
    assert result[0].provider_id == "slow"


def test_route_by_capability_excludes_no_healthy_keys():
    """Test route_by_capability excludes providers without healthy keys."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "fast"
    config.retry_count = 3
    km = MagicMock()
    # Only slow has healthy keys
    def mock_get_healthy_keys(provider_id):
        if provider_id == "slow":
            return [MagicMock()]
        return []

    km.registry.get_healthy_keys.side_effect = mock_get_healthy_keys

    with patch("src.providers.capability_router.manifest_registry", registry):
        router = CapabilityRouter(config, km)
        result = router.route_by_capability(CAPABILITY_REASONING)

    assert len(result) == 1
    assert result[0].provider_id == "slow"


def test_get_healthy_provider_for_capability():
    """Test get_healthy_provider_for_capability returns best match."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "fast"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.capability_router.manifest_registry", registry):
        router = CapabilityRouter(config, km)
        result = router.get_healthy_provider_for_capability(CAPABILITY_REASONING)

    assert result is not None
    assert result.provider_id == "fast"  # Highest priority


def test_get_healthy_provider_for_capability_none_available():
    """Test get_healthy_provider_for_capability returns None when no providers."""
    registry = ManifestRegistry()  # Empty
    config = MagicMock()
    config.active_provider = "fast"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.capability_router.manifest_registry", registry):
        router = CapabilityRouter(config, km)
        result = router.get_healthy_provider_for_capability(CAPABILITY_REASONING)

    assert result is None


def test_get_provider_status_summary():
    """Test get_provider_status_summary returns expected structure."""
    registry = _make_registry_with_providers()
    config = MagicMock()
    config.active_provider = "fast"
    config.retry_count = 3
    km = MagicMock()
    km.registry.get_healthy_keys.return_value = [MagicMock()]

    with patch("src.providers.capability_router.manifest_registry", registry):
        router = CapabilityRouter(config, km)
        summary = router.get_provider_status_summary()

    assert "total_providers" in summary
    assert "healthy_providers" in summary
    assert "by_capability" in summary
    assert "providers" in summary
    assert summary["total_providers"] == 4
    assert summary["healthy_providers"] == 3  # fast, slow, coding_only


def run_all():
    """Run all capability router tests."""
    tests = [
        test_route_by_capability_returns_healthy_sorted,
        test_route_by_capability_excludes_providers,
        test_route_by_capability_excludes_no_healthy_keys,
        test_get_healthy_provider_for_capability,
        test_get_healthy_provider_for_capability_none_available,
        test_get_provider_status_summary,
    ]
    for test in tests:
        test()
    return len(tests)


if __name__ == "__main__":
    n = run_all()
    print(f"All {n} capability router tests passed!")
