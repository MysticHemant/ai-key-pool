"""Synchronize provider keys from environment into the registry."""

from ..utils.logger import get_logger

logger = get_logger("sync")


def sync_provider_keys(config, registry):
    """
    Import provider keys from configuration into the key registry.

    - Adds missing keys.
    - Skips existing keys.
    - Removes default demo keys on first real import.
    """

    imported = 0

    # Remove bundled demo keys once real keys exist
    demo_keys = {"test-key-1", "test-key-2"}

    if config.providers:
        for demo in demo_keys:
            if demo in registry.keys:
                del registry.keys[demo]

    for provider in config.providers.values():
        for index, api_key in enumerate(provider.keys, start=1):

            key_id = f"{provider.name}-{index}"

            existing = registry.get_key(key_id)
            if existing:
                continue

            registry.register_key(
                key_id=key_id,
                provider=provider.name,
                key_value=api_key,
            )

            imported += 1

    if imported:
        logger.info("Imported %d provider key(s)", imported)

    registry.save()