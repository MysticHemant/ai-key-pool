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
    for provider_name in key_manager.registry.get_all_providers():
        providers[provider_name] = key_manager.get_provider_summary(provider_name)

    # Configuration health
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
            "providers_configured": config_report.providers_configured,
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
) -> None:
    """Write recommendations.json for the dashboard.

    Includes: findings with URLs, publication dates, priority, confidence.

    Args:
        research_data: Research findings from daily research
        output_path: Path to dashboard/data/ directory
    """
    findings = research_data.get("findings", [])

    enriched_findings = []
    for f in findings:
        enriched_findings.append({
            "provider": f.get("provider", ""),
            "model": f.get("model"),
            "description": f.get("description", ""),
            "url": f.get("url", ""),
            "type": f.get("type", ""),
            "action": f.get("action", "none"),
            "confidence": f.get("confidence", "medium"),
            "priority": _classify_priority(f),
        })

    # Derive lists from findings if not explicitly provided (backward compat)
    new_providers = research_data.get("new_providers") or [
        f.get("name", f.get("provider", ""))
        for f in findings if f.get("type") == "provider"
    ]
    new_models = research_data.get("new_models") or [
        f.get("name", f.get("model", ""))
        for f in findings if f.get("type") == "model"
    ]
    pricing_changes = research_data.get("pricing_changes") or [
        f.get("description", f.get("name", ""))
        for f in findings if f.get("type") == "pricing"
    ]
    free_tier_changes = research_data.get("free_tier_changes") or [
        f.get("description", f.get("name", ""))
        for f in findings if f.get("type") == "free_tier"
    ]
    breaking_changes = research_data.get("breaking_changes") or [
        f.get("description", f.get("name", ""))
        for f in findings if f.get("type") in ("deprecation", "breaking")
    ]

    recommendations = {
        "research_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "findings": enriched_findings,
        "new_providers": new_providers,
        "new_models": new_models,
        "pricing_changes": pricing_changes,
        "free_tier_changes": free_tier_changes,
        "breaking_changes": breaking_changes,
        "recommendations": [
            {
                "priority": "high" if f.get("action") == "add_key" else "medium",
                "action": f.get("description", "No action"),
                "reason": f.get("provider", f.get("name", "")),
            }
            for f in findings if f.get("action") in ("add_key", "monitor")
        ],
        "summary": research_data.get("summary", "No research data"),
    }

    output_path.mkdir(parents=True, exist_ok=True)
    with open(output_path / "recommendations.json", "w") as f:
        json.dump(recommendations, f, indent=2)

    logger.info("Generated recommendations.json — %d findings", len(findings))


def _classify_priority(finding: dict) -> str:
    """Classify a finding's priority level.

    Args:
        finding: Finding dict

    Returns:
        Priority string: "high", "medium", or "low"
    """
    action = finding.get("action", "")
    confidence = finding.get("confidence", "medium")
    ftype = finding.get("type", "")

    if action == "add_key" and confidence == "high":
        return "high"
    if ftype in ("deprecation", "breaking"):
        return "high"
    if action == "add_key":
        return "medium"
    if action == "monitor":
        return "low"
    return "medium"
