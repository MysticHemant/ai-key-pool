"""Tests for the Provider Manifest system."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.providers.manifest import (
    ProviderManifest, ManifestRegistry, manifest_registry,
    CAPABILITY_REASONING, CAPABILITY_CODING, CAPABILITY_FAST_INFERENCE,
    CAPABILITY_LONG_CONTEXT, CAPABILITY_VISION,
)


def test_provider_manifest_defaults():
    """Test ProviderManifest default values."""
    m = ProviderManifest(provider_id="test", display_name="Test", adapter="builtin")
    assert m.provider_id == "test"
    assert m.display_name == "Test"
    assert m.adapter == "builtin"
    assert m.supported_models == []
    assert m.capabilities == []
    assert m.priority == 10
    assert m.health == "unknown"
    assert m.enabled is True
    assert m.endpoint == ""
    assert m.default_model == ""


def test_provider_manifest_custom():
    """Test ProviderManifest with custom values."""
    m = ProviderManifest(
        provider_id="groq",
        display_name="Groq",
        adapter="builtin",
        supported_models=["llama-3.3-70b", "mixtral-8x7b"],
        capabilities=[CAPABILITY_REASONING, CAPABILITY_CODING, CAPABILITY_FAST_INFERENCE],
        priority=1,
        health="healthy",
        enabled=True,
        endpoint="https://api.groq.com/openai/v1/chat/completions",
        default_model="llama-3.3-70b-versatile",
    )
    assert m.priority == 1
    assert m.health == "healthy"
    assert len(m.supported_models) == 2
    assert CAPABILITY_REASONING in m.capabilities


def test_manifest_registry_register_get():
    """Test registering and retrieving manifests."""
    registry = ManifestRegistry()
    m = ProviderManifest(provider_id="test", display_name="Test", adapter="builtin")
    registry.register(m)
    assert registry.get("test") is m
    assert registry.get("nonexistent") is None


def test_manifest_registry_contains():
    """Test __contains__ operator."""
    registry = ManifestRegistry()
    m = ProviderManifest(provider_id="test", display_name="Test", adapter="builtin")
    registry.register(m)
    assert "test" in registry
    assert "nonexistent" not in registry


def test_manifest_registry_unregister():
    """Test unregistering manifests."""
    registry = ManifestRegistry()
    m = ProviderManifest(provider_id="test", display_name="Test", adapter="builtin")
    registry.register(m)
    assert "test" in registry
    registry.unregister("test")
    assert "test" not in registry


def test_manifest_registry_unregister_nonexistent():
    """Test unregistering nonexistent provider doesn't raise."""
    registry = ManifestRegistry()
    registry.unregister("nonexistent")  # Should not raise


def test_manifest_registry_get_all():
    """Test get_all returns all manifests."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="a", display_name="A", adapter="builtin")
    m2 = ProviderManifest(provider_id="b", display_name="B", adapter="builtin")
    registry.register(m1)
    registry.register(m2)
    all_m = registry.get_all()
    assert len(all_m) == 2
    assert "a" in all_m
    assert "b" in all_m


def test_manifest_registry_get_enabled():
    """Test get_enabled filters disabled providers."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="a", display_name="A", adapter="builtin", enabled=True)
    m2 = ProviderManifest(provider_id="b", display_name="B", adapter="builtin", enabled=False)
    registry.register(m1)
    registry.register(m2)
    enabled = registry.get_enabled()
    assert len(enabled) == 1
    assert "a" in enabled


def test_manifest_registry_get_healthy():
    """Test get_healthy filters unhealthy providers."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="a", display_name="A", adapter="builtin", health="healthy")
    m2 = ProviderManifest(provider_id="b", display_name="B", adapter="builtin", health="unhealthy")
    m3 = ProviderManifest(provider_id="c", display_name="C", adapter="builtin", health="unknown")
    m4 = ProviderManifest(provider_id="d", display_name="D", adapter="builtin", health="degraded")
    m5 = ProviderManifest(provider_id="e", display_name="E", adapter="builtin", enabled=False, health="healthy")
    for m in [m1, m2, m3, m4, m5]:
        registry.register(m)
    healthy = registry.get_healthy()
    assert len(healthy) == 2
    assert "a" in healthy  # healthy
    assert "c" in healthy  # unknown counts as healthy
    assert "b" not in healthy  # unhealthy
    assert "d" not in healthy  # degraded
    assert "e" not in healthy  # disabled


def test_manifest_registry_get_by_capability():
    """Test get_by_capability returns matching providers sorted by priority."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="slow", display_name="Slow", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], priority=10)
    m2 = ProviderManifest(provider_id="fast", display_name="Fast", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING, CAPABILITY_FAST_INFERENCE], priority=1)
    m3 = ProviderManifest(provider_id="other", display_name="Other", adapter="builtin",
                          capabilities=[CAPABILITY_CODING], priority=5)
    for m in [m1, m2, m3]:
        registry.register(m)
    reasoning = registry.get_by_capability(CAPABILITY_REASONING)
    assert len(reasoning) == 2
    assert reasoning[0].provider_id == "fast"  # priority 1
    assert reasoning[1].provider_id == "slow"  # priority 10


def test_manifest_registry_get_by_capability_excludes_disabled():
    """Test get_by_capability excludes disabled providers."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="a", display_name="A", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], enabled=True)
    m2 = ProviderManifest(provider_id="b", display_name="B", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], enabled=False)
    for m in [m1, m2]:
        registry.register(m)
    reasoning = registry.get_by_capability(CAPABILITY_REASONING)
    assert len(reasoning) == 1
    assert reasoning[0].provider_id == "a"


def test_manifest_registry_get_healthy_by_capability():
    """Test get_healthy_by_capability filters by health and capability."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="a", display_name="A", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], health="healthy", priority=1)
    m2 = ProviderManifest(provider_id="b", display_name="B", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], health="unhealthy", priority=2)
    m3 = ProviderManifest(provider_id="c", display_name="C", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING], health="unknown", priority=3)
    for m in [m1, m2, m3]:
        registry.register(m)
    healthy_reasoning = registry.get_healthy_by_capability(CAPABILITY_REASONING)
    assert len(healthy_reasoning) == 2
    assert healthy_reasoning[0].provider_id == "a"
    assert healthy_reasoning[1].provider_id == "c"


def test_manifest_registry_update_health():
    """Test updating provider health."""
    registry = ManifestRegistry()
    m = ProviderManifest(provider_id="test", display_name="Test", adapter="builtin", health="unknown")
    registry.register(m)
    assert m.health == "unknown"
    registry.update_health("test", "healthy")
    assert m.health == "healthy"
    registry.update_health("test", "unhealthy")
    assert m.health == "unhealthy"


def test_manifest_registry_update_health_nonexistent():
    """Test updating health for nonexistent provider doesn't raise."""
    registry = ManifestRegistry()
    registry.update_health("nonexistent", "healthy")  # Should not raise


def test_manifest_registry_set_enabled():
    """Test enabling/disabling providers."""
    registry = ManifestRegistry()
    m = ProviderManifest(provider_id="test", display_name="Test", adapter="builtin", enabled=True)
    registry.register(m)
    registry.set_enabled("test", False)
    assert m.enabled is False
    registry.set_enabled("test", True)
    assert m.enabled is True


def test_manifest_registry_set_enabled_nonexistent():
    """Test set_enabled for nonexistent provider doesn't raise."""
    registry = ManifestRegistry()
    registry.set_enabled("nonexistent", False)  # Should not raise


def test_manifest_registry_list_provider_ids():
    """Test list_provider_ids returns sorted IDs."""
    registry = ManifestRegistry()
    for pid in ["z Provider", "a Provider", "m Provider"]:
        m = ProviderManifest(provider_id=pid, display_name=pid, adapter="builtin")
        registry.register(m)
    ids = registry.list_provider_ids()
    assert ids == ["a Provider", "m Provider", "z Provider"]


def test_manifest_registry_list_capabilities():
    """Test list_capabilities returns all unique capabilities."""
    registry = ManifestRegistry()
    m1 = ProviderManifest(provider_id="a", display_name="A", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING, CAPABILITY_CODING])
    m2 = ProviderManifest(provider_id="b", display_name="B", adapter="builtin",
                          capabilities=[CAPABILITY_REASONING, CAPABILITY_VISION])
    for m in [m1, m2]:
        registry.register(m)
    caps = registry.list_capabilities()
    assert CAPABILITY_REASONING in caps
    assert CAPABILITY_CODING in caps
    assert CAPABILITY_VISION in caps
    assert len(caps) == 3


def test_manifest_registry_to_dict():
    """Test to_dict serialization."""
    registry = ManifestRegistry()
    m = ProviderManifest(provider_id="test", display_name="Test", adapter="builtin",
                         capabilities=[CAPABILITY_REASONING], priority=5)
    registry.register(m)
    d = registry.to_dict()
    assert "test" in d
    assert d["test"]["provider_id"] == "test"
    assert d["test"]["capabilities"] == [CAPABILITY_REASONING]
    assert d["test"]["priority"] == 5


def test_capability_constants():
    """Test capability constants are defined."""
    assert CAPABILITY_REASONING == "reasoning"
    assert CAPABILITY_CODING == "coding"
    assert CAPABILITY_FAST_INFERENCE == "fast_inference"
    assert CAPABILITY_LONG_CONTEXT == "long_context"
    assert CAPABILITY_VISION == "vision"


def test_global_registry_is_singleton():
    """Test global manifest_registry is a singleton."""
    from src.providers.manifest import manifest_registry as r1
    from src.providers.manifest import manifest_registry as r2
    assert r1 is r2


def run_all():
    """Run all manifest tests."""
    tests = [
        test_provider_manifest_defaults,
        test_provider_manifest_custom,
        test_manifest_registry_register_get,
        test_manifest_registry_contains,
        test_manifest_registry_unregister,
        test_manifest_registry_unregister_nonexistent,
        test_manifest_registry_get_all,
        test_manifest_registry_get_enabled,
        test_manifest_registry_get_healthy,
        test_manifest_registry_get_by_capability,
        test_manifest_registry_get_by_capability_excludes_disabled,
        test_manifest_registry_get_healthy_by_capability,
        test_manifest_registry_update_health,
        test_manifest_registry_update_health_nonexistent,
        test_manifest_registry_set_enabled,
        test_manifest_registry_set_enabled_nonexistent,
        test_manifest_registry_list_provider_ids,
        test_manifest_registry_list_capabilities,
        test_manifest_registry_to_dict,
        test_capability_constants,
        test_global_registry_is_singleton,
    ]
    for test in tests:
        test()
    return len(tests)


if __name__ == "__main__":
    n = run_all()
    print(f"All {n} manifest tests passed!")
