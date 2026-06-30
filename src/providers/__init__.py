"""AI Key Pool - Provider adapters for AI services."""

from .manifest import ProviderManifest, ManifestRegistry, manifest_registry
from .manifest import (
    CAPABILITY_REASONING,
    CAPABILITY_CODING,
    CAPABILITY_LONG_CONTEXT,
    CAPABILITY_VISION,
    CAPABILITY_SEARCH,
    CAPABILITY_FAST_INFERENCE,
    CAPABILITY_LOW_COST,
)
