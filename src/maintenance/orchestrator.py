"""Daily maintenance orchestrator for AI Key Pool.

Runs the full daily cycle:
1. Health check all keys
2. Generate status report
3. Run AI research
4. Generate recommendations
5. Write dashboard JSON files
6. Send email summary
"""

import os
import json
import smtplib
from pathlib import Path
from datetime import datetime, timezone

from ..key_pool import KeyManager, KeyRotator
from ..utils.config import load_config, Config
from ..utils.logger import get_logger
from ..startup import sync_provider_keys
from .research import research_providers
from .dashboard_gen import generate_status_json, generate_recommendations_json
from .email_sender import send_daily_summary


logger = get_logger("maintenance")


def run_daily_maintenance() -> dict:
    """Execute the full daily maintenance cycle.

    Returns:
        Summary dict with results of each step
    """
    config = load_config()
    key_manager = KeyManager(
        config.data_dir,
        config.max_consecutive_failures,
    )

    # Import provider keys into the registry before any maintenance work.
    sync_provider_keys(config, key_manager.registry)

    errors = []
    stats = {"registry": {"total_keys": 0, "by_status": {}}, "health": {}}
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "steps": {},
    }

    # Step 1: Health check
    logger.info("Step 1: Health check")
    try:
        stats = key_manager.get_all_stats()
        results["steps"]["health_check"] = {
            "status": "ok",
            "total_keys": stats["registry"]["total_keys"],
            "by_status": stats["registry"]["by_status"],
        }
    except Exception as e:
        logger.error("Health check failed: %s", e)
        errors.append(f"Health check: {e}")
        results["steps"]["health_check"] = {"status": "error", "error": str(e)}

    # Step 2: Generate status
    logger.info("Step 2: Generate status report")
    try:
        dashboard_data = Path(__file__).parent.parent.parent / "dashboard" / "data"
        generate_status_json(key_manager, config, dashboard_data)
        results["steps"]["status_report"] = {"status": "ok"}
    except Exception as e:
        logger.error("Status report failed: %s", e)
        errors.append(f"Status report: {e}")
        results["steps"]["status_report"] = {"status": "error", "error": str(e)}

    # Step 3: Research
    logger.info("Step 3: Run provider research")
    try:
        history_path = config.data_dir / "research_history.json"
        research_data = research_providers(config, key_manager, history_path)
        results["steps"]["research"] = {
            "status": "ok",
            "findings_count": len(research_data.get("findings", [])),
        }
    except Exception as e:
        logger.error("Research failed: %s", e)
        errors.append(f"Research: {e}")
        research_data = {"findings": [], "summary": "Research failed"}
        results["steps"]["research"] = {"status": "error", "error": str(e)}

    # Step 4: Generate recommendations
    logger.info("Step 4: Generate recommendations")
    try:
        dashboard_data = Path(__file__).parent.parent.parent / "dashboard" / "data"
        generate_recommendations_json(research_data, dashboard_data)
        results["steps"]["recommendations"] = {"status": "ok"}
    except Exception as e:
        logger.error("Recommendations failed: %s", e)
        errors.append(f"Recommendations: {e}")
        results["steps"]["recommendations"] = {"status": "error", "error": str(e)}

    # Step 5: Send email
    logger.info("Step 5: Send daily email")
    try:
        status_data = {
            "active_provider": config.active_provider,
            "total_keys": stats["registry"]["total_keys"],
            "healthy_keys": stats["registry"]["by_status"].get("active", 0),
            "exhausted_keys": stats["registry"]["by_status"].get("exhausted", 0),
            "disabled_keys": stats["registry"]["by_status"].get("disabled", 0),
        }

        email_sent = send_daily_summary(
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            recipient=os.environ.get("EMAIL_RECIPIENT", ""),
            status=status_data,
            recommendations=research_data,
            errors=errors,
        )
        results["steps"]["email"] = {"status": "sent" if email_sent else "skipped"}
    except Exception as e:
        logger.error("Email failed: %s", e)
        errors.append(f"Email: {e}")
        results["steps"]["email"] = {"status": "error", "error": str(e)}

    results["errors"] = errors
    results["status"] = "completed" if not errors else "completed_with_errors"

    logger.info("Maintenance complete — %d errors", len(errors))
    return results


if __name__ == "__main__":
    result = run_daily_maintenance()
    print(json.dumps(result, indent=2))
