"""Unified key loader for AI Key Pool.

Supports multiple key storage formats:
- AIKEYPOOL_PROVIDER_KEYS: JSON object mapping provider names to key lists
- AIKEYPOOL_PROVIDER_<NAME>_KEYS: Individual provider key lists (legacy)

Both formats are supported simultaneously. JSON takes precedence.
Individual env vars are additive (merge into JSON-defined providers).
"""

import os
import json
from typing import Optional
from ..utils.config import Config, ProviderConfig
from ..utils.logger import get_logger


logger = get_logger("key_loader")


def load_provider_keys(config: Config) -> Config:
    """Load provider keys from all supported sources.

    Priority order:
    1. AIKEYPOOL_PROVIDER_KEYS (JSON) — primary source
    2. AIKEYPOOL_PROVIDER_<NAME>_KEYS (individual env vars) — additive

    When both exist, JSON keys are used first, then individual env vars
    are merged in (new keys appended, duplicates skipped).

    Args:
        config: Config instance to populate with provider keys

    Returns:
        Updated Config with providers populated
    """
    # Step 1: Load from AIKEYPOOL_PROVIDER_KEYS JSON
    json_loaded = _load_from_json(config)

    # Step 2: Load from individual env vars (additive)
    env_loaded = _load_from_env_vars(config)

    # Step 3: Log summary
    total_providers = len(config.providers)
    total_keys = sum(len(pc.keys) for pc in config.providers.values())

    logger.info(
        "KEY LOADER: %d providers, %d total keys (json=%d, env=%d)",
        total_providers, total_keys, json_loaded, env_loaded,
    )

    # Step 4: Auto-register providers in manifest registry
    _register_providers_in_manifest(config)

    return config


def _load_from_json(config: Config) -> int:
    """Load provider keys from AIKEYPOOL_PROVIDER_KEYS JSON env var.

    Format: {"provider_name": ["key1", "key2"], ...}

    Args:
        config: Config to populate

    Returns:
        Number of providers loaded from JSON
    """
    json_str = os.environ.get("AIKEYPOOL_PROVIDER_KEYS", "").strip()
    if not json_str:
        return 0

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("KEY LOADER: Failed to parse AIKEYPOOL_PROVIDER_KEYS JSON: %s", e)
        return 0

    if not isinstance(data, dict):
        logger.error("KEY LOADER: AIKEYPOOL_PROVIDER_KEYS must be a JSON object, got %s", type(data).__name__)
        return 0

    count = 0
    for provider_name, keys in data.items():
        provider_name = provider_name.lower().strip()
        if not provider_name:
            continue

        if not isinstance(keys, list):
            logger.warning("KEY LOADER: Keys for %s must be a list, got %s", provider_name, type(keys).__name__)
            continue

        # Filter empty keys
        valid_keys = [k.strip() for k in keys if k and k.strip()]
        if not valid_keys:
            continue

        if provider_name not in config.providers:
            config.providers[provider_name] = ProviderConfig(name=provider_name)

        config.providers[provider_name].keys = valid_keys
        count += 1

        logger.info("KEY LOADER: Loaded %d keys for %s from JSON", len(valid_keys), provider_name)

    return count


def _load_from_env_vars(config: Config) -> int:
    """Load provider keys from individual AIKEYPOOL_PROVIDER_<NAME>_KEYS env vars.

    Args:
        config: Config to populate

    Returns:
        Number of providers loaded from env vars
    """
    count = 0
    for key, value in os.environ.items():
        if key.startswith("AIKEYPOOL_PROVIDER_") and key.endswith("_KEYS"):
            provider_name = key[len("AIKEYPOOL_PROVIDER_"):-len("_KEYS")].lower()
            if not provider_name:
                continue

            keys = [k.strip() for k in value.split(",") if k.strip()]
            if not keys:
                continue

            if provider_name not in config.providers:
                config.providers[provider_name] = ProviderConfig(name=provider_name)
                config.providers[provider_name].keys = keys
                count += 1
            else:
                # Merge: add new keys that don't already exist
                existing_keys = set(config.providers[provider_name].keys)
                new_keys = [k for k in keys if k not in existing_keys]
                if new_keys:
                    config.providers[provider_name].keys.extend(new_keys)
                    count += 1

            logger.debug("KEY LOADER: Loaded %d keys for %s from env var", len(keys), provider_name)

    return count


def _register_providers_in_manifest(config: Config) -> None:
    """Auto-register discovered providers in the manifest registry.

    Args:
        config: Config with providers populated
    """
    from ..providers.manifest import manifest_registry, ProviderManifest

    for provider_name, provider_config in config.providers.items():
        if not provider_config.keys:
            continue

        # Skip if already registered (builtin providers)
        if manifest_registry.get(provider_name):
            continue

        # Create manifest for auto-discovered provider
        try:
            from ..providers.plugins.generic_openai import GenericOpenAIProvider
            generic = GenericOpenAIProvider(provider_name)
            manifest = generic.get_manifest()
            manifest_registry.register(manifest)
            logger.info("KEY LOADER: Auto-registered manifest for %s", provider_name)
        except Exception as e:
            logger.warning("KEY LOADER: Could not auto-register manifest for %s: %s", provider_name, e)


def get_configured_providers() -> list[str]:
    """Get list of provider names that have keys configured.

    Checks both JSON and individual env vars.

    Returns:
        Sorted list of provider names with at least one key
    """
    providers = set()

    # Check JSON
    json_str = os.environ.get("AIKEYPOOL_PROVIDER_KEYS", "").strip()
    if json_str:
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                for name, keys in data.items():
                    if isinstance(keys, list) and any(k.strip() for k in keys if k):
                        providers.add(name.lower().strip())
        except json.JSONDecodeError:
            pass

    # Check individual env vars
    for key, value in os.environ.items():
        if key.startswith("AIKEYPOOL_PROVIDER_") and key.endswith("_KEYS"):
            provider_name = key[len("AIKEYPOOL_PROVIDER_"):-len("_KEYS")].lower()
            if provider_name and value.strip():
                providers.add(provider_name)

    return sorted(providers)
