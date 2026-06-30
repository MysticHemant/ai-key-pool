"""Dashboard data generators for AI Key Pool.

Writes JSON files consumed by the GitHub Pages dashboard.
Status includes: total keys, healthy keys, provider summaries, last update,
maintenance status, configuration health, plugin status.
Recommendations include: findings, official URLs, publication dates,
recommendation priority, confidence level.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from ..key_pool import KeyManager
from ..utils.config import Config
from ..utils.config_validator import ConfigValidationReport
from ..utils.logger import get_logger


logger = get_logger("dashboard")


def generate_status_json(
    key_manager: KeyManager,
    config: Config,
    output_path: Path,
    maintenance_duration: float = 0.0,
    workflow_status: str = "unknown",
    step_results: dict | None = None,
    config_report: ConfigValidationReport | None = None,
    provider_status: dict | None = None,
) -> None:
    """Write status.json for the dashboard.

    Includes: system status, configuration health, providers, plugin status,
    key health, workflow history.

    Args:
        key_manager: Key manager instance
        config: System configuration
        output_path: Path to dashboard/data/ directory
        maintenance_duration: Total maintenance duration in seconds
        workflow_status: Overall workflow status
        step_results: Dict of step name -> status info
        config_report: Configuration validation report
        provider_status: Provider adapter status dict
    """
    stats = key_manager.get_all_stats()
    registry = stats["registry"]

    active_key = key_manager.get_active_key(config.active_provider)
    last_success = None
    last_failure = None

    for key_id, entry in key_manager.registry.keys.items():
        if entry.last_used:
            if entry.success_count > 0 and (not last_success or entry.last_used > last_success):
                last_success = entry.last_used
            if entry.failure_count > 0 and (not last_failure or entry.last_used > last_failure):
                last_failure = entry.last_used

    providers = {}
    for provider_name, pdata in registry.get("by_provider", {}).items():
        providers[provider_name] = {
            "provider": provider_name,
            "total_keys": pdata["total"],
            "healthy_keys": pdata["active"],
            "exhausted_keys": pdata["exhausted"],
            "disabled_keys": pdata["disabled"],
            "keys": [
                {
                    "key_id": k.key_id,
                    "status": k.status.value,
                    "failure_count": k.failure_count,
                    "success_count": k.success_count,
                }
                for k in key_manager.registry.keys.values()
                if k.provider == provider_name
            ],
        }

    # Configuration health — registry is the source of truth for providers_configured
    config_health = {
        "is_valid": True,
        "providers_detected": [],
        "providers_configured": [],
        "warnings": [],
        "errors": [],
    }
    if config_report:
        config_health = {
            "is_valid": config_report.is_valid,
            "providers_detected": config_report.providers_detected,
            "providers_configured": sorted(key_manager.registry.get_all_providers()),
            "total_secrets_checked": config_report.total_secrets_checked,
            "total_secrets_ok": config_report.total_secrets_ok,
            "warnings": config_report.warnings,
            "errors": config_report.errors,
        }

    # Plugin status
    plugins = {}
    if provider_status:
        for pname, pinfo in provider_status.items():
            plugins[pname] = {
                "adapter": pinfo.get("adapter", "unknown"),
                "has_keys": pname in providers,
            }

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
        "by_provider": registry.get("by_provider", {}),
        "last_success": last_success,
        "last_failure": last_failure,
        "last_update": datetime.now(timezone.utc).isoformat(),
        "maintenance_duration_seconds": round(maintenance_duration, 2),
        "maintenance_status": workflow_status,
        "providers": providers,
        "config_health": config_health,
        "plugins": plugins,
    }

    if step_results:
        status["steps"] = step_results

    # Email status from step results
    if step_results and "email" in step_results:
        status["last_email_status"] = step_results["email"].get("status", "unknown")

    output_path.mkdir(parents=True, exist_ok=True)
    with open(output_path / "status.json", "w") as f:
        json.dump(status, f, indent=2)

    logger.info("Generated status.json — %d keys", registry["total_keys"])


def generate_recommendations_json(
    research_data: dict,
    output_path: Path,
    configured_providers: list[str] = None,
    discovery_results: dict = None,
) -> None:
    """Write recommendations.json for the dashboard.

    Produces an action-oriented report grouped by priority level.
    Only includes findings that have a concrete action or are breaking changes.
    Never recommends providers already configured.

    Args:
        research_data: Research findings from daily research
        output_path: Path to dashboard/data/ directory
        configured_providers: List of already-configured provider names
        discovery_results: Optional discovery results dict
    """
    findings = research_data.get("findings", [])
    configured_set = set(configured_providers or [])

    # Filter out findings for already-configured providers
    filtered_findings = []
    for f in findings:
        provider = f.get("provider", "").lower()
        action = f.get("action", "")
        # Never recommend adding a configured provider
        if provider in configured_set and action in ("add_provider", "add_key"):
            logger.info("RECOMMENDATIONS: Skipping %s (already configured)", provider)
            continue
        filtered_findings.append(f)

    # Enrich and classify each finding
    enriched_findings = []
    for f in filtered_findings:
        priority = _classify_priority(f)
        enriched_findings.append({
            "provider": f.get("provider", ""),
            "model": f.get("model"),
            "description": f.get("description", ""),
            "url": f.get("url", ""),
            "type": f.get("type", ""),
            "action": f.get("action", "none"),
            "confidence": f.get("confidence", "medium"),
            "priority": priority,
        })

    # Build concise action-oriented recommendations
    # Only include findings with actionable priority (exclude low-confidence noise)
    action_items = []
    for f in enriched_findings:
        priority = f["priority"]
        # Skip low-priority findings that are just general announcements
        if priority == "low" and f["action"] == "monitor" and f["confidence"] != "high":
            continue
        action_items.append({
            "priority": priority,
            "action": _format_action_line(f),
            "provider": f["provider"],
            "type": f["type"],
        })

    # Sort by priority: high first, then medium, then low
    priority_order = {"high": 0, "medium": 1, "low": 2}
    action_items.sort(key=lambda x: priority_order.get(x["priority"], 3))

    # Derive lists from findings if not explicitly provided (backward compat)
    new_providers = research_data.get("new_providers") or [
        f.get("name", f.get("provider", ""))
        for f in filtered_findings if f.get("type") == "provider"
    ]
    new_models = research_data.get("new_models") or [
        f.get("name", f.get("model", ""))
        for f in filtered_findings if f.get("type") == "model"
    ]
    pricing_changes = research_data.get("pricing_changes") or [
        f.get("description", f.get("name", ""))
        for f in filtered_findings if f.get("type") == "pricing"
    ]
    free_tier_changes = research_data.get("free_tier_changes") or [
        f.get("description", f.get("name", ""))
        for f in filtered_findings if f.get("type") == "free_tier"
    ]
    breaking_changes = research_data.get("breaking_changes") or [
        f.get("description", f.get("name", ""))
        for f in filtered_findings if f.get("type") in ("deprecation", "breaking")
    ]

    # Build suggested providers from discovery (exclude configured)
    suggested_providers = []
    if discovery_results:
        for suggestion in discovery_results.get("suggestions", []):
            name = suggestion.get("name", "").lower()
            if name and name not in configured_set:
                suggested_providers.append({
                    "name": suggestion.get("display_name", name),
                    "endpoint": suggestion.get("endpoint", ""),
                    "models": suggestion.get("models", []),
                    "free_tier": suggestion.get("free_tier", False),
                    "source": suggestion.get("source", ""),
                    "confidence": suggestion.get("confidence", "medium"),
                })

    recommendations = {
        "research_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "findings": enriched_findings,
        "recommendations": action_items,
        "new_providers": new_providers,
        "new_models": new_models,
        "pricing_changes": pricing_changes,
        "free_tier_changes": free_tier_changes,
        "breaking_changes": breaking_changes,
        "suggested_providers": suggested_providers,
        "configured_providers": sorted(configured_set),
        "summary": research_data.get("summary", "No research data"),
    }

    output_path.mkdir(parents=True, exist_ok=True)
    with open(output_path / "recommendations.json", "w") as f:
        json.dump(recommendations, f, indent=2)

    logger.info("Generated recommendations.json — %d findings, %d action items, %d suggested providers",
                len(filtered_findings), len(action_items), len(suggested_providers))


def _classify_priority(finding: dict) -> str:
    """Classify a finding's priority level.

    Priority order:
    1. HIGH: New provider not configured (action == "add_provider")
    2. HIGH: Breaking changes (type in "deprecation", "breaking")
    3. MEDIUM: Free-tier improvement (type == "free_tier")
    4. MEDIUM: New model (type == "model")
    5. LOW: Pricing changes (type == "pricing")
    6. LOW: General news / monitor (action == "monitor")

    Args:
        finding: Finding dict

    Returns:
        Priority string: "high", "medium", or "low"
    """
    action = finding.get("action", "")
    ftype = finding.get("type", "")

    if action == "add_provider":
        return "high"
    if ftype in ("deprecation", "breaking"):
        return "high"
    if ftype == "free_tier":
        return "medium"
    if ftype == "model":
        return "medium"
    if ftype == "pricing":
        return "low"
    if action == "monitor":
        return "low"
    return "low"


def _format_action_line(finding: dict) -> str:
    """Format a concise action line for the recommendations list.

    Examples:
        "Add GitHub Models provider"
        "Groq: Released Kimi K2"
        "Anthropic: Free tier expanded"
        "Fireworks: Pricing update"

    Args:
        finding: Enriched finding dict

    Returns:
        One-line action description
    """
    provider = finding.get("provider", "Unknown")
    action = finding.get("action", "")
    ftype = finding.get("type", "")
    model = finding.get("model")
    description = finding.get("description", "")

    if action == "add_provider":
        return f"Add {provider} provider"

    if ftype == "model":
        label = model if model else description[:60]
        return f"{provider.title()}: Released {label}" if model else f"{provider.title()}: {description[:80]}"

    if ftype == "free_tier":
        return f"{provider.title()}: Free tier update" + (f" — {description[:60]}" if description else "")

    if ftype in ("deprecation", "breaking"):
        return f"{provider.title()}: {description[:80]}" if description else f"{provider.title()}: Breaking change"

    if ftype == "pricing":
        return f"{provider.title()}: Pricing update" + (f" — {description[:50]}" if description else "")

    return f"{provider.title()}: {description[:80]}" if description else f"{provider.title()} announcement"
