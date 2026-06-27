"""Health checker for API keys.

Tracks health status, consecutive failures, and timestamps
for each key without making external API calls.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional


class HealthStatus(Enum):
    """Health status of a key."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class KeyHealth:
    """Health record for a single key."""
    key_id: str
    status: HealthStatus = HealthStatus.UNKNOWN
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_success: Optional[str] = None
    last_failure: Optional[str] = None
    last_checked: Optional[str] = None

    def record_success(self) -> None:
        """Record a successful request."""
        now = datetime.now(timezone.utc).isoformat()
        self.last_success = now
        self.last_checked = now
        self.consecutive_failures = 0
        self.total_successes += 1
        self.status = HealthStatus.HEALTHY

    def record_failure(self) -> None:
        """Record a failed request."""
        now = datetime.now(timezone.utc).isoformat()
        self.last_failure = now
        self.last_checked = now
        self.consecutive_failures += 1
        self.total_failures += 1

        # Update status based on failure count
        if self.consecutive_failures >= 5:
            self.status = HealthStatus.UNHEALTHY
        elif self.consecutive_failures >= 2:
            self.status = HealthStatus.DEGRADED
        else:
            self.status = HealthStatus.HEALTHY

    def is_healthy(self) -> bool:
        """Check if the key is considered healthy."""
        return self.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "KeyHealth":
        """Create from dictionary."""
        data["status"] = HealthStatus(data["status"])
        return cls(**data)


class HealthChecker:
    """Manages health records for all keys."""

    def __init__(self, data_dir: Path):
        """Initialize health checker.

        Args:
            data_dir: Directory to store health data
        """
        self.data_dir = data_dir
        self.health_file = data_dir / "key_health.json"
        self.records: dict[str, KeyHealth] = {}
        self._load()

    def _load(self) -> None:
        """Load health records from disk."""
        if not self.health_file.exists():
            return

        try:
            with open(self.health_file, "r") as f:
                data = json.load(f)
            for key_id, record_data in data.items():
                self.records[key_id] = KeyHealth.from_dict(record_data)
        except (json.JSONDecodeError, KeyError):
            # Corrupted file, start fresh
            self.records = {}

    def save(self) -> None:
        """Persist health records to disk."""
        data = {
            key_id: record.to_dict()
            for key_id, record in self.records.items()
        }
        with open(self.health_file, "w") as f:
            json.dump(data, f, indent=2)

    def get_health(self, key_id: str) -> KeyHealth:
        """Get health record for a key.

        Args:
            key_id: Unique key identifier

        Returns:
            KeyHealth record (creates new if not found)
        """
        if key_id not in self.records:
            self.records[key_id] = KeyHealth(key_id=key_id)
        return self.records[key_id]

    def record_success(self, key_id: str) -> KeyHealth:
        """Record a successful request for a key.

        Args:
            key_id: Unique key identifier

        Returns:
            Updated KeyHealth record
        """
        health = self.get_health(key_id)
        health.record_success()
        self.save()
        return health

    def record_failure(self, key_id: str) -> KeyHealth:
        """Record a failed request for a key.

        Args:
            key_id: Unique key identifier

        Returns:
            Updated KeyHealth record
        """
        health = self.get_health(key_id)
        health.record_failure()
        self.save()
        return health

    def is_key_healthy(self, key_id: str) -> bool:
        """Check if a key is healthy.

        Args:
            key_id: Unique key identifier

        Returns:
            True if key is healthy
        """
        health = self.get_health(key_id)
        return health.is_healthy()

    def get_all_healthy_keys(self) -> list[str]:
        """Get all key IDs that are healthy.

        Returns:
            List of healthy key IDs
        """
        return [
            key_id for key_id, health in self.records.items()
            if health.is_healthy()
        ]

    def reset_health(self, key_id: str) -> None:
        """Reset health record for a key.

        Args:
            key_id: Unique key identifier
        """
        self.records[key_id] = KeyHealth(key_id=key_id)
        self.save()

    def get_stats(self) -> dict:
        """Get aggregate health statistics.

        Returns:
            Dictionary with health statistics
        """
        total = len(self.records)
        healthy = sum(1 for h in self.records.values() if h.status == HealthStatus.HEALTHY)
        degraded = sum(1 for h in self.records.values() if h.status == HealthStatus.DEGRADED)
        unhealthy = sum(1 for h in self.records.values() if h.status == HealthStatus.UNHEALTHY)

        return {
            "total_keys": total,
            "healthy": healthy,
            "degraded": degraded,
            "unhealthy": unhealthy,
            "unknown": total - healthy - degraded - unhealthy,
        }
