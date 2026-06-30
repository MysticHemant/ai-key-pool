"""Historical Intelligence tracker for AI Key Pool.

Maintains long-term history of providers, models, rate limits,
free tiers, and outages. Generates "Changes Since Last Report"
instead of repeating the same information.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field

from ..utils.logger import get_logger


logger = get_logger("history_tracker")


@dataclass
class ProviderHistory:
    """History entry for a provider."""
    first_seen: str = ""
    last_active: str = ""
    status: str = "unknown"
    models: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


@dataclass
class ModelHistory:
    """History entry for a model."""
    provider: str = ""
    released: str = ""
    status: str = "active"  # active, deprecated, removed
    first_seen: str = ""


@dataclass
class RateLimitChange:
    """Record of a rate limit change."""
    provider: str = ""
    date: str = ""
    change: str = ""
    old_value: str = ""
    new_value: str = ""


@dataclass
class FreeTierChange:
    """Record of a free tier change."""
    provider: str = ""
    date: str = ""
    change: str = ""


@dataclass
class ProviderOutage:
    """Record of a provider outage."""
    provider: str = ""
    start: str = ""
    end: str = ""
    reason: str = ""


class HistoryTracker:
    """Tracks historical intelligence across research cycles."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.history_file = data_dir / "intelligence_history.json"
        self.history = self._load_history()

    def _load_history(self) -> dict:
        """Load history from disk."""
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("HISTORY: Failed to load history: %s", e)

        return {
            "last_report_date": "",
            "known_providers": {},
            "model_history": {},
            "rate_limit_changes": [],
            "free_tier_changes": [],
            "outages": [],
            "discoveries": [],
        }

    def save_history(self) -> None:
        """Save history to disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.history_file, "w") as f:
                json.dump(self.history, f, indent=2)
            logger.info("HISTORY: Saved history to %s", self.history_file)
        except Exception as e:
            logger.error("HISTORY: Failed to save history: %s", e)

    def update_provider(
        self,
        provider_id: str,
        status: str = "unknown",
        models: list[str] = None,
        capabilities: list[str] = None,
    ) -> None:
        """Update provider history.

        Args:
            provider_id: Provider identifier
            status: Current status
            models: List of supported models
            capabilities: List of capabilities
        """
        now = datetime.now(timezone.utc).isoformat()
        known = self.history["known_providers"]

        if provider_id not in known:
            known[provider_id] = {
                "first_seen": now,
                "last_active": now,
                "status": status,
                "models": models or [],
                "capabilities": capabilities or [],
            }
            logger.info("HISTORY: New provider discovered: %s", provider_id)
        else:
            known[provider_id]["last_active"] = now
            known[provider_id]["status"] = status
            if models:
                known[provider_id]["models"] = models
            if capabilities:
                known[provider_id]["capabilities"] = capabilities

    def update_model(
        self,
        model_name: str,
        provider: str,
        status: str = "active",
    ) -> None:
        """Update model history.

        Args:
            model_name: Model identifier
            provider: Provider name
            status: Model status
        """
        now = datetime.now(timezone.utc).isoformat()
        models = self.history["model_history"]

        if model_name not in models:
            models[model_name] = {
                "provider": provider,
                "released": now,
                "status": status,
                "first_seen": now,
            }
            logger.info("HISTORY: New model discovered: %s from %s", model_name, provider)
        else:
            models[model_name]["status"] = status
            models[model_name]["provider"] = provider

    def record_rate_limit_change(
        self,
        provider: str,
        change: str,
        old_value: str = "",
        new_value: str = "",
    ) -> None:
        """Record a rate limit change.

        Args:
            provider: Provider name
            change: Description of change
            old_value: Old rate limit value
            new_value: New rate limit value
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = {
            "provider": provider,
            "date": now,
            "change": change,
            "old_value": old_value,
            "new_value": new_value,
        }
        self.history["rate_limit_changes"].append(entry)
        logger.info("HISTORY: Rate limit change recorded for %s: %s", provider, change)

    def record_free_tier_change(
        self,
        provider: str,
        change: str,
    ) -> None:
        """Record a free tier change.

        Args:
            provider: Provider name
            change: Description of change
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = {
            "provider": provider,
            "date": now,
            "change": change,
        }
        self.history["free_tier_changes"].append(entry)
        logger.info("HISTORY: Free tier change recorded for %s: %s", provider, change)

    def record_outage(
        self,
        provider: str,
        reason: str = "",
        start: str = "",
        end: str = "",
    ) -> None:
        """Record a provider outage.

        Args:
            provider: Provider name
            reason: Outage reason
            start: Start time (ISO format)
            end: End time (ISO format)
        """
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "provider": provider,
            "start": start or now,
            "end": end,
            "reason": reason,
        }
        self.history["outages"].append(entry)
        logger.info("HISTORY: Outage recorded for %s", provider)

    def record_discovery(
        self,
        provider: str,
        source: str,
        details: dict = None,
    ) -> None:
        """Record a provider discovery.

        Args:
            provider: Provider name
            source: Discovery source
            details: Additional details
        """
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "provider": provider,
            "source": source,
            "date": now,
            "details": details or {},
        }
        self.history["discoveries"].append(entry)
        logger.info("HISTORY: Discovery recorded for %s from %s", provider, source)

    def get_changes_since(self, since_date: str) -> dict:
        """Get all changes since a specific date.

        Args:
            since_date: ISO date string to compare against

        Returns:
            Dict with changes since the given date
        """
        changes = {
            "new_providers": [],
            "new_models": [],
            "rate_limit_changes": [],
            "free_tier_changes": [],
            "outages": [],
            "discoveries": [],
        }

        # Check for new providers
        for provider_id, info in self.history["known_providers"].items():
            if info.get("first_seen", "") > since_date:
                changes["new_providers"].append({
                    "provider": provider_id,
                    "first_seen": info["first_seen"],
                })

        # Check for new models
        for model_name, info in self.history["model_history"].items():
            if info.get("first_seen", "") > since_date:
                changes["new_models"].append({
                    "model": model_name,
                    "provider": info.get("provider", ""),
                    "released": info.get("released", ""),
                })

        # Check for rate limit changes
        for entry in self.history["rate_limit_changes"]:
            if entry.get("date", "") > since_date:
                changes["rate_limit_changes"].append(entry)

        # Check for free tier changes
        for entry in self.history["free_tier_changes"]:
            if entry.get("date", "") > since_date:
                changes["free_tier_changes"].append(entry)

        # Check for outages
        for entry in self.history["outages"]:
            if entry.get("start", "") > since_date:
                changes["outages"].append(entry)

        # Check for discoveries
        for entry in self.history["discoveries"]:
            if entry.get("date", "") > since_date:
                changes["discoveries"].append(entry)

        return changes

    def get_changes_since_last_report(self) -> dict:
        """Get changes since the last report.

        Returns:
            Dict with changes since last report
        """
        last_date = self.history.get("last_report_date", "")
        if not last_date:
            # First report — return everything as new
            return {
                "is_first_report": True,
                "new_providers": [
                    {"provider": pid, "first_seen": info.get("first_seen", "")}
                    for pid, info in self.history["known_providers"].items()
                ],
                "new_models": [
                    {"model": mid, "provider": info.get("provider", "")}
                    for mid, info in self.history["model_history"].items()
                ],
                "rate_limit_changes": self.history["rate_limit_changes"],
                "free_tier_changes": self.history["free_tier_changes"],
                "outages": self.history["outages"],
                "discoveries": self.history["discoveries"],
            }

        return self.get_changes_since(last_date)

    def mark_report_generated(self) -> None:
        """Mark that a report has been generated (update last_report_date)."""
        self.history["last_report_date"] = datetime.now(timezone.utc).isoformat()
        self.save_history()

    def get_provider_summary(self) -> dict:
        """Get a summary of all known providers.

        Returns:
            Dict with provider summary
        """
        return {
            "total_known": len(self.history["known_providers"]),
            "total_models": len(self.history["model_history"]),
            "providers": self.history["known_providers"],
        }

    def format_changes_since_last_report(self) -> str:
        """Format changes since last report as a readable string.

        Returns:
            Formatted string with changes
        """
        changes = self.get_changes_since_last_report()

        if changes.get("is_first_report"):
            lines = ["Initial Report — All providers and models are new:\n"]
        else:
            last_date = self.history.get("last_report_date", "unknown")
            lines = [f"Changes Since Last Report ({last_date}):\n"]

        # New providers
        new_providers = changes.get("new_providers", [])
        if new_providers:
            lines.append("NEW PROVIDERS:")
            for p in new_providers:
                lines.append(f"  + {p['provider']} (first seen: {p.get('first_seen', '')[:10]})")
            lines.append("")

        # New models
        new_models = changes.get("new_models", [])
        if new_models:
            lines.append("NEW MODELS:")
            for m in new_models:
                lines.append(f"  + {m['model']} from {m.get('provider', 'unknown')}")
            lines.append("")

        # Rate limit changes
        rl_changes = changes.get("rate_limit_changes", [])
        if rl_changes:
            lines.append("RATE LIMIT CHANGES:")
            for r in rl_changes:
                lines.append(f"  ~ {r['provider']}: {r['change']}")
            lines.append("")

        # Free tier changes
        ft_changes = changes.get("free_tier_changes", [])
        if ft_changes:
            lines.append("FREE TIER CHANGES:")
            for f in ft_changes:
                lines.append(f"  ~ {f['provider']}: {f['change']}")
            lines.append("")

        # Outages
        outages = changes.get("outages", [])
        if outages:
            lines.append("OUTAGES:")
            for o in outages:
                lines.append(f"  ! {o['provider']}: {o.get('reason', 'unknown')}")
            lines.append("")

        # Discoveries
        discoveries = changes.get("discoveries", [])
        if discoveries:
            lines.append("DISCOVERIES:")
            for d in discoveries:
                lines.append(f"  * {d['provider']} from {d.get('source', 'unknown')}")
            lines.append("")

        if not any([new_providers, new_models, rl_changes, ft_changes, outages, discoveries]):
            lines.append("No significant changes since last report.")

        return "\n".join(lines)
