"""Daily maintenance orchestrator for AI Key Pool.

Runs the full daily cycle with independent failure handling:
0. Validate configuration and secrets
1. Synchronize provider keys
2. Discover providers and load plugins
3. Health check all keys
4. Research the AI ecosystem using real web data
5. Generate dashboard JSON
6. Generate recommendations JSON
7. Send email summary
8. Log diagnostics

Each subsystem fails independently — one failure never terminates
the entire workflow.
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone

from ..key_pool import KeyManager, KeyRotator
from ..utils.config import load_config, Config
from ..utils.config_validator import validate_config, ConfigValidationReport
from ..utils.logger import get_logger
from ..startup import sync_provider_keys
from ..providers.provider_factory import list_providers, get_provider_status
from .research import research_providers
from .dashboard_gen import generate_status_json, generate_recommendations_json
from .email_sender import send_daily_summary, EmailDeliveryError


logger = get_logger("maintenance")


_EXCEPTION = object()  # Sentinel for failed steps


def _time_step(func, *args, **kwargs):
    """Execute a function and return (result, duration_seconds).

    Returns (_EXCEPTION, duration) if the function raised an exception.
    The caller must check ``result is not _EXCEPTION`` to detect failure.
    """
    start = time.monotonic()
    try:
        result = func(*args, **kwargs)
        duration = time.monotonic() - start
        return result, duration
    except Exception as e:
        duration = time.monotonic() - start
        logger.error("Step failed after %.1fs: %s", duration, e)
        return _EXCEPTION, duration


def run_daily_maintenance() -> dict:
    """Execute the full daily maintenance cycle.

    Each subsystem runs independently. If one fails, the others
    still execute. The workflow never terminates early.

    Returns:
        Summary dict with results of each step
    """
    overall_start = time.monotonic()

    # ── Step 0: Initialize ──
    logger.info("=" * 60)
    logger.info("AI KEY POOL DAILY MAINTENANCE — START")
    logger.info("Date: %s", datetime.now(timezone.utc).isoformat())
    logger.info("=" * 60)

    errors: list[str] = []
    step_results: dict = {}

    config = load_config()

    # ── Step 0a: Validate configuration ──
    logger.info("STEP START: Configuration validation")
    config_report = validate_config(config.data_dir)
    step_results["config_validation"] = {
        "status": "ok" if config_report.is_valid else "warnings",
        "is_valid": config_report.is_valid,
        "providers_detected": config_report.providers_detected,
        "providers_configured": config_report.providers_configured,
        "total_secrets_checked": config_report.total_secrets_checked,
        "total_secrets_ok": config_report.total_secrets_ok,
        "warnings": config_report.warnings,
        "errors": config_report.errors,
        "typo_suggestions": config_report.typo_suggestions,
    }
    if config_report.errors:
        for e in config_report.errors:
            errors.append(f"Config: {e}")
    if config_report.warnings:
        for w in config_report.warnings:
            logger.warning("CONFIG WARNING: %s", w)
    logger.info(
        "STEP END: Configuration validation — %d/%d secrets OK, %d warnings, %d errors",
        config_report.total_secrets_ok,
        config_report.total_secrets_checked,
        len(config_report.warnings),
        len(config_report.errors),
    )

    key_manager = KeyManager(
        config.data_dir,
        config.max_consecutive_failures,
    )

    # ── Step 0b: Key synchronization ──
    logger.info("STEP START: Key synchronization")
    sync_start = time.monotonic()
    try:
        sync_provider_keys(config, key_manager.registry)
        sync_duration = time.monotonic() - sync_start
        logger.info("STEP END: Key synchronization — %.1fs", sync_duration)
    except Exception as e:
        sync_duration = time.monotonic() - sync_start
        logger.error("STEP FAIL: Key synchronization — %s (%.1fs)", e, sync_duration)
        errors.append(f"Key synchronization: {e}")

    # ── Step 0c: Provider discovery ──
    logger.info("STEP START: Provider discovery")
    disc_start = time.monotonic()
    try:
        available_providers = list_providers()
        provider_status = get_provider_status()
        disc_duration = time.monotonic() - disc_start
        logger.info(
            "STEP END: Provider discovery — %d providers in %.1fs: %s",
            len(available_providers), disc_duration, available_providers,
        )
    except Exception as e:
        disc_duration = time.monotonic() - disc_start
        logger.error("STEP FAIL: Provider discovery — %s", e)
        available_providers = []
        provider_status = {}

    # Log diagnostics
    loaded_providers = list(key_manager.registry.get_all_providers())
    keys_loaded = len(key_manager.registry.keys)
    logger.info("DIAGNOSTIC: Loaded providers: %s", loaded_providers)
    logger.info("DIAGNOSTIC: Keys loaded: %d", keys_loaded)
    logger.info("DIAGNOSTIC: Active provider: %s", config.active_provider)
    logger.info("DIAGNOSTIC: Available providers: %s", available_providers)

    stats = {"registry": {"total_keys": 0, "by_status": {}}, "health": {}}
    research_data: dict = {"findings": [], "summary": "Not yet researched"}
    dashboard_data = Path(__file__).parent.parent.parent / "dashboard" / "data"

    # ── Step 1: Health check ──
    logger.info("STEP START: Health check")
    health_result, health_duration = _time_step(_do_health_check, key_manager)
    if health_result is not _EXCEPTION:
        stats = health_result
        step_results["health_check"] = {
            "status": "ok",
            "duration_seconds": round(health_duration, 2),
            "total_keys": stats["registry"]["total_keys"],
            "by_status": stats["registry"]["by_status"],
        }
        logger.info("STEP END: Health check — %d keys in %.1fs",
                     stats["registry"]["total_keys"], health_duration)
    else:
        step_results["health_check"] = {
            "status": "error",
            "duration_seconds": round(health_duration, 2),
        }
        errors.append("Health check failed")
        logger.error("STEP FAIL: Health check (%.1fs)", health_duration)

    # ── Step 2: Research ──
    logger.info("STEP START: Provider research")
    history_path = config.data_dir / "research_history.json"
    research_result, research_duration = _time_step(
        research_providers, config, key_manager, history_path
    )
    if research_result is not _EXCEPTION:
        research_data = research_result
        findings_count = len(research_data.get("findings", []))
        step_results["research"] = {
            "status": "ok",
            "duration_seconds": round(research_duration, 2),
            "findings_count": findings_count,
            "has_llm_summary": research_data.get("has_llm_summary", False),
        }
        logger.info("STEP END: Research — %d findings in %.1fs", findings_count, research_duration)
    else:
        step_results["research"] = {
            "status": "error",
            "duration_seconds": round(research_duration, 2),
        }
        errors.append("Research failed")
        logger.error("STEP FAIL: Research (%.1fs)", research_duration)

    # ── Step 3: Dashboard status ──
    logger.info("STEP START: Dashboard status generation")
    status_result, status_duration = _time_step(
        generate_status_json, key_manager, config, dashboard_data
    )
    if status_result is not _EXCEPTION:
        step_results["status_report"] = {
            "status": "ok",
            "duration_seconds": round(status_duration, 2),
        }
        logger.info("STEP END: Dashboard status in %.1fs", status_duration)
    else:
        step_results["status_report"] = {
            "status": "error",
            "duration_seconds": round(status_duration, 2),
        }
        errors.append("Dashboard status generation failed")
        logger.error("STEP FAIL: Dashboard status (%.1fs)", status_duration)

    # ── Step 4: Dashboard recommendations ──
    logger.info("STEP START: Dashboard recommendations generation")
    recs_result, recs_duration = _time_step(
        generate_recommendations_json, research_data, dashboard_data
    )
    if recs_result is not _EXCEPTION:
        step_results["recommendations"] = {
            "status": "ok",
            "duration_seconds": round(recs_duration, 2),
        }
        logger.info("STEP END: Dashboard recommendations in %.1fs", recs_duration)
    else:
        step_results["recommendations"] = {
            "status": "error",
            "duration_seconds": round(recs_duration, 2),
        }
        errors.append("Dashboard recommendations generation failed")
        logger.error("STEP FAIL: Dashboard recommendations (%.1fs)", recs_duration)

    # ── Step 5: Email ──
    logger.info("STEP START: Email delivery")
    email_result, email_duration = _time_step(
        _do_send_email, config, stats, research_data, errors
    )
    if email_result is not _EXCEPTION:
        step_results["email"] = {
            "status": "sent" if email_result else "skipped",
            "duration_seconds": round(email_duration, 2),
        }
        logger.info("STEP END: Email — %s in %.1fs",
                     "sent" if email_result else "skipped", email_duration)
    else:
        step_results["email"] = {
            "status": "error",
            "duration_seconds": round(email_duration, 2),
        }
        errors.append("Email delivery failed")
        logger.error("STEP FAIL: Email (%.1fs)", email_duration)

    # ── Summary ──
    overall_duration = time.monotonic() - overall_start
    workflow_status = "completed" if not errors else "completed_with_errors"

    logger.info("=" * 60)
    logger.info("AI KEY POOL DAILY MAINTENANCE — COMPLETE")
    logger.info("Workflow status: %s", workflow_status)
    logger.info("Total duration: %.1fs", overall_duration)
    logger.info("Errors: %d", len(errors))
    for err in errors:
        logger.info("  - %s", err)
    logger.info("=" * 60)

    # ── Diagnostics for GitHub Actions ──
    _log_github_actions_diagnostics(
        loaded_providers=loaded_providers,
        keys_loaded=keys_loaded,
        active_provider=config.active_provider,
        available_providers=available_providers,
        provider_status=provider_status,
        config_report=config_report,
        step_results=step_results,
        overall_duration=overall_duration,
        workflow_status=workflow_status,
        errors=errors,
    )

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": workflow_status,
        "duration_seconds": round(overall_duration, 2),
        "steps": step_results,
        "errors": errors,
        "diagnostics": {
            "loaded_providers": loaded_providers,
            "keys_loaded": keys_loaded,
            "active_provider": config.active_provider,
            "available_providers": available_providers,
            "provider_status": provider_status,
            "config_is_valid": config_report.is_valid,
            "config_warnings": len(config_report.warnings),
            "config_errors": len(config_report.errors),
        },
    }

    # Write maintenance result to data dir for diagnostics
    try:
        result_path = config.data_dir / "last_maintenance.json"
        with open(result_path, "w") as f:
            json.dump(result, f, indent=2)
    except Exception as e:
        logger.warning("Could not write maintenance result: %s", e)

    return result


def _do_health_check(key_manager: KeyManager) -> dict:
    """Run health check. Returns stats dict."""
    return key_manager.get_all_stats()


def _do_send_email(
    config: Config,
    stats: dict,
    research_data: dict,
    errors: list[str],
) -> bool:
    """Send email. Returns True if sent, False if skipped."""
    status_data = {
        "active_provider": config.active_provider,
        "total_keys": stats["registry"]["total_keys"],
        "healthy_keys": stats["registry"]["by_status"].get("active", 0),
        "exhausted_keys": stats["registry"]["by_status"].get("exhausted", 0),
        "disabled_keys": stats["registry"]["by_status"].get("disabled", 0),
        "providers": {},
    }
    # Add provider summaries to status
    for pname in stats["registry"].get("by_provider", {}):
        status_data["providers"][pname] = {
            "total_keys": stats["registry"]["by_provider"][pname],
            "healthy_keys": 0,
        }

    return send_daily_summary(
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        recipient=os.environ.get("EMAIL_RECIPIENT", ""),
        status=status_data,
        recommendations=research_data,
        errors=errors,
    )


def _log_github_actions_diagnostics(
    loaded_providers: list[str],
    keys_loaded: int,
    active_provider: str,
    available_providers: list[str],
    provider_status: dict,
    config_report: ConfigValidationReport,
    step_results: dict,
    overall_duration: float,
    workflow_status: str,
    errors: list[str],
) -> None:
    """Log structured diagnostics for GitHub Actions.

    Outputs key-value pairs that GitHub Actions can parse from logs.
    Never logs secret values — only counts and status.
    """
    logger.info("::group::Diagnostics")
    logger.info("DIAGNOSTIC loaded_providers=%s", json.dumps(loaded_providers))
    logger.info("DIAGNOSTIC keys_loaded=%d", keys_loaded)
    logger.info("DIAGNOSTIC active_provider=%s", active_provider)
    logger.info("DIAGNOSTIC available_providers=%s", json.dumps(available_providers))

    # Config validation
    logger.info("DIAGNOSTIC config_is_valid=%s", config_report.is_valid)
    logger.info("DIAGNOSTIC config_secrets_checked=%d", config_report.total_secrets_checked)
    logger.info("DIAGNOSTIC config_secrets_ok=%d", config_report.total_secrets_ok)
    logger.info("DIAGNOSTIC config_warnings=%d", len(config_report.warnings))
    logger.info("DIAGNOSTIC config_errors=%d", len(config_report.errors))

    # Providers detected vs configured
    logger.info("DIAGNOSTIC providers_detected=%s", json.dumps(config_report.providers_detected))
    logger.info("DIAGNOSTIC providers_configured=%s", json.dumps(config_report.providers_configured))

    # Plugin status
    for pname, pstatus in provider_status.items():
        logger.info("DIAGNOSTIC plugin_%s=%s", pname, pstatus.get("adapter", "unknown"))

    # Step results
    for step_name, step_info in step_results.items():
        status = step_info.get("status", "unknown")
        duration = step_info.get("duration_seconds", 0)
        logger.info("DIAGNOSTIC step_%s=%s (%.1fs)", step_name, status, duration)

    logger.info("DIAGNOSTIC total_duration=%.1fs", overall_duration)
    logger.info("DIAGNOSTIC workflow_status=%s", workflow_status)
    logger.info("DIAGNOSTIC error_count=%d", len(errors))

    # Dashboard files
    dashboard_data = Path(__file__).parent.parent.parent / "dashboard" / "data"
    status_exists = (dashboard_data / "status.json").exists()
    recs_exists = (dashboard_data / "recommendations.json").exists()
    config_exists = (dashboard_data / "configuration_report.json").exists()
    logger.info("DIAGNOSTIC dashboard_status_json=%s", "present" if status_exists else "missing")
    logger.info("DIAGNOSTIC dashboard_recommendations_json=%s", "present" if recs_exists else "missing")
    logger.info("DIAGNOSTIC configuration_report_json=%s", "present" if config_exists else "missing")

    # Email sent
    email_step = step_results.get("email", {})
    logger.info("DIAGNOSTIC email_sent=%s", email_step.get("status", "unknown"))

    logger.info("::endgroup::")


if __name__ == "__main__":
    result = run_daily_maintenance()
    print(json.dumps(result, indent=2))
