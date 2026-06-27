"""Key registry for AI Key Pool.

Manages the storage and state of API keys across multiple providers.
Supports multiple keys per provider with status tracking.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class KeyStatus(Enum):
    """Status of an API key."""
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    DISABLED = "disabled"


@dataclass
class KeyEntry:
    """Represents a single API key in the registry."""
    key_id: str
    provider: str
    key_value: str
    status: KeyStatus = KeyStatus.ACTIVE
    last_used: Optional[str] = None
    failure_count: int = 0
    success_count: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "KeyEntry":
        """Create from dictionary."""
        data["status"] = KeyStatus(data["status"])
        return cls(**data)


class KeyRegistry:
    """Registry for managing API keys across providers.

    Stores key entries with their status, usage statistics,
    and supports multiple keys per provider.
    """

    def __init__(self, data_dir: Path):
        """Initialize the key registry.

        Args:
            data_dir: Directory to store registry data
        """
        self.data_dir = data_dir
        self.registry_file = data_dir / "key_registry.json"
        self.keys: dict[str, KeyEntry] = {}
        self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        if not self.registry_file.exists():
            return

        try:
            with open(self.registry_file, "r") as f:
                data = json.load(f)
            for key_id, key_data in data.items():
                self.keys[key_id] = KeyEntry.from_dict(key_data)
        except (json.JSONDecodeError, KeyError):
            # Corrupted file, start fresh
            self.keys = {}

    def save(self) -> None:
        """Persist registry to disk."""
        data = {
            key_id: entry.to_dict()
            for key_id, entry in self.keys.items()
        }
        with open(self.registry_file, "w") as f:
            json.dump(data, f, indent=2)

    def register_key(
        self,
        key_id: str,
        provider: str,
        key_value: str,
        status: KeyStatus = KeyStatus.ACTIVE,
    ) -> KeyEntry:
        """Register a new key.

        Args:
            key_id: Unique identifier for the key
            provider: Provider name (e.g., 'openai', 'anthropic')
            key_value: The actual API key value
            status: Initial status

        Returns:
            The created KeyEntry

        Raises:
            ValueError: If key_id, provider, or key_value is empty
        """
        if not key_id or not key_id.strip():
            raise ValueError("key_id cannot be empty")
        if not provider or not provider.strip():
            raise ValueError("provider cannot be empty")
        if not key_value or not key_value.strip():
            raise ValueError("key_value cannot be empty")

        entry = KeyEntry(
            key_id=key_id,
            provider=provider,
            key_value=key_value,
            status=status,
        )
        self.keys[key_id] = entry
        self.save()
        return entry

    def get_key(self, key_id: str) -> Optional[KeyEntry]:
        """Get a key entry by ID.

        Args:
            key_id: Unique key identifier

        Returns:
            KeyEntry if found, None otherwise
        """
        return self.keys.get(key_id)

    def get_keys_for_provider(self, provider: str) -> list[KeyEntry]:
        """Get all keys for a specific provider.

        Args:
            provider: Provider name

        Returns:
            List of KeyEntry objects
        """
        return [
            entry for entry in self.keys.values()
            if entry.provider == provider
        ]

    def get_healthy_keys(self, provider: str) -> list[KeyEntry]:
        """Get all healthy keys for a provider.

        Args:
            provider: Provider name

        Returns:
            List of healthy KeyEntry objects
        """
        return [
            entry for entry in self.keys.values()
            if entry.provider == provider
            and entry.status == KeyStatus.ACTIVE
        ]

    def update_status(self, key_id: str, status: KeyStatus) -> None:
        """Update the status of a key.

        Args:
            key_id: Unique key identifier
            status: New status
        """
        if key_id in self.keys:
            self.keys[key_id].status = status
            self.save()

    def record_usage(self, key_id: str, success: bool) -> None:
        """Record a usage attempt for a key.

        Args:
            key_id: Unique key identifier
            success: Whether the request succeeded
        """
        if key_id not in self.keys:
            return

        entry = self.keys[key_id]
        entry.last_used = datetime.now(timezone.utc).isoformat()

        if success:
            entry.success_count += 1
            # Reset failure count on success
            if entry.status == KeyStatus.EXHAUSTED:
                entry.status = KeyStatus.ACTIVE
        else:
            entry.failure_count += 1

        self.save()

    def disable_key(self, key_id: str) -> None:
        """Disable a key.

        Args:
            key_id: Unique key identifier
        """
        self.update_status(key_id, KeyStatus.DISABLED)

    def enable_key(self, key_id: str) -> None:
        """Enable a key.

        Args:
            key_id: Unique key identifier
        """
        self.update_status(key_id, KeyStatus.ACTIVE)

    def get_all_providers(self) -> list[str]:
        """Get all unique provider names.

        Returns:
            List of provider names
        """
        return list({entry.provider for entry in self.keys.values()})

    def get_stats(self) -> dict:
        """Get registry statistics.

        Returns:
            Dictionary with registry stats
        """
        total = len(self.keys)
        by_status = {}
        by_provider = {}

        for entry in self.keys.values():
            # Count by status
            status = entry.status.value
            by_status[status] = by_status.get(status, 0) + 1

            # Count by provider
            by_provider[entry.provider] = by_provider.get(entry.provider, 0) + 1

        return {
            "total_keys": total,
            "by_status": by_status,
            "by_provider": by_provider,
        }
