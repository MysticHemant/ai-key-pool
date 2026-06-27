"""Dashboard data generators for AI Key Pool.

Writes JSON files consumed by the GitHub Pages dashboard.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from ..key_pool import KeyManager
from ..utils.config import Config
from ..utils.logger import get_logger


logger = get_logger("dashboard")


def generate_status_json(
    key_manager: KeyManager,
    config: Config,
    output_path: Path,
) -> None:
    """Write status.json for the dashboard.

    Args:
        key_manager: Key manager instance
        config: System configuration
        output_path: Path to dashboard/data/ directory
    """
    stats = key_manager.get_all_stats()
    registry = stats["registry"]

    active_key = key_manager.get_active_key(config.active_provider)
    last_success = None
    last_failure = None

    # Find most recent success/failure across all keys
    for key_id, entry in key_manager.registry.keys.items():
        if entry.last_used:
            if entry.success_count > 0 and (not last_success or entry.last_used > last_success):
                last_success = entry.last_used
            if entry.failure_count > 0 and (not last_failure or entry.last_used > last_failure):
                last_failure = entry.last_used

    providers = {}
    for provider_name in key_manager.registry.get_all_providers():
        providers[provider_name] = key_manager.get_provider_summary(provider_name)

    status = {
        "active_provider": config.active_provider,
        "active_key": {
            "key_id": active_key.key_id if active_key else None,
            "provider": active_key.provider if active_key else None,
            "status": active_key.status.value if active_key else None,
        } if active_key else None,
        "total_keys": registry["total_keys"],
        "healthy_keys": registry["by_status"].get("active", 0),
        "exhausted_keys": registry["by_status"].get("exhausted", 0),
        "disabled_keys": registry["by_status"].get("disabled", 0),
        "last_success": last_success,
        "last_failure": last_failure,
        "last_update": datetime.now(timezone.utc).isoformat(),
        "providers": providers,
    }

    output_path.mkdir(parents=True, exist_ok=True)
    with open(output_path / "status.json", "w") as f:
        json.dump(status, f, indent=2)

    logger.info("Generated status.json — %d keys", registry["total_keys"])


def generate_recommendations_json(
    research_data: dict,
    output_path: Path,
) -> None:
    """Write recommendations.json for the dashboard.

    Args:
        research_data: Research findings from daily research
        output_path: Path to dashboard/data/ directory
    """
    findings = research_data.get("findings", [])

    recommendations = {
        "research_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "new_providers": [f for f in findings if f.get("type") == "provider"],
        "free_tiers": [f for f in findings if f.get("type") == "free_tier"],
        "new_models": [f for f in findings if f.get("type") == "model"],
        "provider_changes": [f for f in findings if f.get("type") == "change"],
        "recommendations": [
            {
                "priority": "high" if f.get("action") == "add_key" else "medium",
                "action": f.get("description", "No action"),
                "reason": f.get("name", ""),
            }
            for f in findings if f.get("action") in ("add_key", "monitor")
        ],
        "summary": research_data.get("summary", "No research data"),
    }

    output_path.mkdir(parents=True, exist_ok=True)
    with open(output_path / "recommendations.json", "w") as f:
        json.dump(recommendations, f, indent=2)

    logger.info("Generated recommendations.json — %d findings", len(findings))
