"""Daily maintenance orchestrator for AI Key Pool.

Runs the full research cycle as a continuous session:
0. Validate configuration and secrets
1. Synchronize provider keys
2. Discover providers and load plugins
3. Health check all keys
4. Research loop: iterate until quality targets met or limits reached
5. Generate final report
6. Generate dashboard JSON
7. Generate recommendations JSON
8. Send email summary
9. Archive cycle and reset runtime
10. Log diagnostics

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
from ..startup import sync_provider_keys, load_provider_keys
from ..providers.provider_factory import list_providers, get_provider_status, get_manifest_registry
from .research import research_providers, generate_final_report, generate_research_plan, compress_memory
from .discovery import discover_providers, save_discovery_results
from .history_tracker import HistoryTracker
from .dashboard_gen import generate_status_json, generate_recommendations_json
from .email_sender import send_daily_summary, EmailDeliveryError
from .runtime_manager import RuntimeManager


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


def _log_startup_diagnostics(
    config: Config,
    key_manager: KeyManager,
    available_providers: list[str],
    provider_status: dict,
) -> None:
    """Log provider diagnostics before maintenance begins.

    Shows a clear provider summary table with status for each provider.
    Filters empty provider IDs. Reports exactly why each provider was skipped.
    Never logs actual API key values.
    """
    from ..providers.manifest import manifest_registry

    registry = key_manager.registry
    health = key_manager.health_checker

    # Collect all known providers: manifest registry + config providers + available
    all_provider_ids = set()
    all_provider_ids.update(manifest_registry.list_provider_ids())
    all_provider_ids.update(config.providers.keys())
    all_provider_ids.update(available_providers)
    all_provider_ids.discard("")  # Filter empty provider IDs

    # Per-provider healthy key count
    all_healthy_keys = set(health.get_all_healthy_keys())
    healthy_by_provider: dict[str, int] = {}
    for key_id, entry in registry.keys.items():
        if key_id in all_healthy_keys:
            healthy_by_provider.setdefault(entry.provider, 0)
            healthy_by_provider[entry.provider] += 1

    registry_stats = registry.get_stats()
    by_provider = registry_stats.get("by_provider", {})

    # ── Provider Summary Table ──
    logger.info("STARTUP DIAGNOSTICS: Provider Summary")
    logger.info("─" * 60)

    for pname in sorted(all_provider_ids):
        if not pname or not pname.strip():
            continue  # Skip empty provider IDs

        manifest = manifest_registry.get(pname)
        has_keys = pname in config.providers and len(config.providers[pname].keys) > 0
        key_count = len(config.providers[pname].keys) if has_keys else 0
        healthy_count = healthy_by_provider.get(pname, 0)
        total_in_registry = by_provider.get(pname, {}).get("total", 0)
        is_configured = has_keys or total_in_registry > 0

        # Determine health status
        if manifest:
            health_status = manifest.health
        elif healthy_count > 0:
            health_status = "healthy"
        elif total_in_registry > 0:
            # Check if all keys are unhealthy/disabled
            pinfo = by_provider.get(pname, {})
            active_count = pinfo.get("active", 0)
            if active_count > 0:
                health_status = "healthy"
            else:
                health_status = "unhealthy"
        else:
            health_status = "not configured"

        # Determine skip reason
        skip_reason = ""
        if not is_configured:
            skip_reason = "missing key"
        elif healthy_count == 0 and total_in_registry > 0:
            skip_reason = "all keys unhealthy or disabled"

        # Log provider line
        if is_configured and health_status in ("healthy", "unknown"):
            logger.info(
                "  ✓ %s — configured, %s, %d key(s)",
                pname, health_status, key_count,
            )
        elif is_configured:
            logger.info(
                "  ✗ %s — configured, %s, %d key(s)",
                pname, health_status, key_count,
            )
        else:
            logger.info(
                "  ✗ %s — not configured, reason: %s",
                pname, skip_reason or "no keys",
            )

    logger.info("─" * 60)

    # ── Active provider ──
    active = config.active_provider
    if active:
        active_keys = registry.get_healthy_keys(active)
        if active_keys:
            logger.info(
                "STARTUP DIAGNOSTICS: Active provider for LLM calls = [%s] (%d healthy key(s))",
                active, len(active_keys),
            )
        else:
            logger.warning(
                "STARTUP DIAGNOSTICS: Active provider = [%s] but NO healthy keys — LLM calls will fail",
                active,
            )
    else:
        logger.warning("STARTUP DIAGNOSTICS: No active provider (AIKEYPOOL_ACTIVE_PROVIDER unset)")

    # ── Health summary ──
    health_stats = health.get_stats()
    logger.info(
        "STARTUP DIAGNOSTICS: Health — healthy=%d, degraded=%d, unhealthy=%d, unknown=%d",
        health_stats.get("healthy", 0),
        health_stats.get("degraded", 0),
        health_stats.get("unhealthy", 0),
        health_stats.get("unknown", 0),
    )


def _run_single_iteration(
    config: Config,
    key_manager: KeyManager,
    runtime_manager: RuntimeManager,
    history_path: Path,
) -> dict:
    """Execute a single research iteration.

    Uses the research queue to determine focus, checks for repetition,
    and saves structured findings alongside markdown.

    Returns:
        Dict with 'success', 'findings_count', 'has_llm_summary', 'duration_seconds'
    """
    iteration = runtime_manager.determine_current_iteration()
    runtime_state = runtime_manager.state

    logger.info("===== ITERATION %d =====", iteration)

    # Initialize queue on first iteration
    if iteration == 1:
        runtime_manager.initialize_research_queue()

    # Get research focus from queue
    research_focus = runtime_manager.get_research_focus()
    logger.info("Research focus: category=%s, objectives=%s",
                research_focus.get("category", "unknown"),
                research_focus.get("objectives", []))

    # Inject focus into runtime state for the planner
    runtime_state["current_plan"] = {
        "objectives": research_focus.get("objectives", []),
        "category": research_focus.get("category", "exploration"),
        "queue_items": [
            {"topic": item.get("topic", ""), "category": item.get("category", "")}
            for item in research_focus.get("queue_items", [])
        ],
    }

    # Research planning (enhanced with queue context)
    if config.research_planner_enabled:
        # Check if plan should be reused (skips LLM call)
        from .research import _should_reuse_plan
        if _should_reuse_plan(runtime_state):
            logger.info("Plan reused — skipping LLM planner (no significant changes)")
        else:
            logger.info("Generating research plan for iteration %d", iteration)
            plan = generate_research_plan(config, key_manager, runtime_state)
            # Merge queue objectives with LLM plan
            if plan.get("objectives"):
                runtime_state["current_plan"]["objectives"] = plan["objectives"]
            if plan.get("claims_to_verify"):
                runtime_state["current_plan"]["claims_to_verify"] = plan["claims_to_verify"]
            if plan.get("questions_to_answer"):
                runtime_state["current_plan"]["questions_to_answer"] = plan["questions_to_answer"]
            runtime_manager.save_state()
            logger.info("Research plan: Objectives=%s", runtime_state["current_plan"].get("objectives", []))

    # Memory compression
    if iteration > config.memory_compression_threshold:
        logger.info("Compressing memory for iteration %d", iteration)
        compressed = compress_memory(config, key_manager, runtime_state)
        runtime_manager.state["long_term_memory"] = compressed
        runtime_manager.save_state()

    # Research
    logger.info("Researching iteration %d", iteration)
    research_result, research_duration = _time_step(
        research_providers, config, key_manager, history_path, runtime_state
    )

    if research_result is _EXCEPTION:
        logger.error("Research failed with exception on iteration %d", iteration)
        return {
            "success": False,
            "findings_count": 0,
            "has_llm_summary": False,
            "duration_seconds": round(research_duration, 2),
        }

    research_data = research_result
    findings_count = len(research_data.get("findings", []))
    research_success = research_data.get("_success", True)

    if not research_success:
        logger.error("Research returned failure on iteration %d: %s",
                     iteration, research_data.get("summary", "unknown"))
        return {
            "success": False,
            "findings_count": findings_count,
            "has_llm_summary": research_data.get("has_llm_summary", False),
            "duration_seconds": round(research_duration, 2),
        }

    # Check for repetition before updating state
    current_findings = research_data.get("findings", [])
    repetition = runtime_manager.detect_repetition(current_findings)
    if repetition.get("is_repeated"):
        logger.warning(
            "REPETITION DETECTED: %.1f similarity with iteration %d. Applying strategy shift.",
            repetition.get("similarity", 0),
            repetition.get("previous_iteration", 0),
        )
        strategy = repetition.get("strategy_shift", {})
        if strategy:
            # Override plan with shifted strategy
            runtime_state["current_plan"] = {
                "objectives": strategy.get("objectives", []),
                "category": strategy.get("category", "exploration"),
                "reason": strategy.get("reason", "Repetition detected"),
            }
            runtime_manager.save_state()
            logger.info("Strategy shift applied: %s", strategy.get("reason", ""))

    # Save iteration report (markdown export)
    research_dir = config.data_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    iter_file = research_dir / f"iteration_{iteration}.md"

    report_data = research_data.get("iteration_report", {})
    markdown_content = f"""# Research Iteration {iteration}

## Summary
{report_data.get('summary', 'No summary provided.')}

## Evidence
{report_data.get('evidence', 'No evidence provided.')}

## Sources
{report_data.get('sources', 'No sources provided.')}

## Confidence
{report_data.get('confidence', 'medium')}

## Assumptions
{chr(10).join(f"- {a}" for a in report_data.get('assumptions', [])) if report_data.get('assumptions') else "None"}

## Unanswered Questions
{chr(10).join(f"- {q}" for q in report_data.get('unanswered_questions', [])) if report_data.get('unanswered_questions') else "None"}

## Contradictions
{chr(10).join(f"- {c}" for c in report_data.get('contradictions', [])) if report_data.get('contradictions') else "None"}

## Recommendations for the next iteration
{report_data.get('recommendations_next', 'None')}
"""
    try:
        iter_file.write_text(markdown_content, encoding="utf-8")
        logger.info("Saved iteration report to %s (markdown export)", iter_file)
    except Exception as e:
        logger.error("Failed to write iteration report to %s: %s", iter_file, e)

    # Update runtime state (this also saves structured findings JSON)
    eval_data = research_data.get("evaluation", {})
    logger.info("=== EVAL_DATA BEFORE RuntimeManager ===")
    logger.info("type(eval_data): %s", type(eval_data).__name__)
    logger.info("eval_data.keys(): %s", list(eval_data.keys()) if isinstance(eval_data, dict) else "NOT A DICT")
    if isinstance(eval_data, dict):
        logger.info("eval_data overall_quality: %s", eval_data.get("overall_quality", "MISSING"))
        logger.info("eval_data coverage: %s", eval_data.get("coverage", "MISSING"))
        logger.info("eval_data verification: %s", eval_data.get("verification", "MISSING"))
        logger.info("eval_data verified_claims count: %d", len(eval_data.get("verified_claims", [])))
    logger.info("=======================================")
    runtime_manager.update_state(eval_data, current_findings)

    # Consume queue items that were researched this iteration
    consumed = runtime_manager.consume_queue_items(count=len(research_focus.get("queue_items", [])) or 2)
    logger.info("Consumed %d queue items this iteration", len(consumed))

    # Add new queue items from unanswered questions and contradictions
    report_unanswered = report_data.get("unanswered_questions", [])
    report_contradictions = report_data.get("contradictions", [])
    new_items = []
    for q in report_unanswered[:3]:
        new_items.append({
            "topic": f"Answer: {q}" if isinstance(q, str) else str(q),
            "category": "gap_filling",
            "priority": 2,
        })
    for c in report_contradictions[:3]:
        new_items.append({
            "topic": f"Resolve: {c}" if isinstance(c, str) else str(c),
            "category": "contradiction_resolution",
            "priority": 1,
        })
    if new_items:
        runtime_manager.add_queue_items(new_items)

    # Iteration diagnostics
    metrics = runtime_manager.state.get("quality_metrics", {})
    logger.info("RESEARCH ITERATION %d COMPLETE", iteration)
    logger.info("  Findings: %d", findings_count)
    logger.info("  Quality: Overall=%d, Coverage=%d, Verification=%d, SourceDiversity=%d",
                metrics.get("overall_quality", 0),
                metrics.get("coverage", 0),
                metrics.get("verification", 0),
                metrics.get("source_diversity", 0))
    logger.info("  Verified Claims: %d", len(runtime_manager.state.get("verified_claims", [])))
    logger.info("  Open Questions: %d", len(runtime_manager.state.get("open_questions", [])))
    logger.info("  Queue Items Pending: %d",
                len([i for i in runtime_manager.state.get("research_queue", [])
                     if isinstance(i, dict) and i.get("status") == "pending"]))
    logger.info("===== END ITERATION %d =====", iteration)

    return {
        "success": True,
        "findings_count": findings_count,
        "has_llm_summary": research_data.get("has_llm_summary", False),
        "duration_seconds": round(research_duration, 2),
    }


def _run_research_loop(
    config: Config,
    key_manager: KeyManager,
    runtime_manager: RuntimeManager,
    history_path: Path,
    session_start: float,
) -> tuple[dict, str]:
    """Run the research loop until completion or safety limits.

    Returns:
        (research_data, completion_reason) tuple
    """
    max_iterations = config.research_max_iterations
    max_runtime_seconds = config.research_max_runtime_minutes * 60
    max_api_budget = config.research_max_api_budget

    research_data: dict = {"findings": [], "summary": "Not yet researched"}
    completion_reason = ""
    iterations_completed = 0

    while True:
        iteration = runtime_manager.determine_current_iteration()

        # ── Safety limit: max iterations ──
        if iteration > max_iterations:
            completion_reason = "Maximum iterations reached (%d/%d)." % (iteration - 1, max_iterations)
            logger.info("SAFETY LIMIT: %s", completion_reason)
            break

        # ── Safety limit: max runtime ──
        elapsed = time.monotonic() - session_start
        if elapsed >= max_runtime_seconds:
            completion_reason = "Maximum runtime reached (%.0fs / %ds)." % (elapsed, max_runtime_seconds)
            logger.info("SAFETY LIMIT: %s", completion_reason)
            break

        # ── Run single iteration ──
        iter_result = _run_single_iteration(config, key_manager, runtime_manager, history_path)
        iterations_completed += 1

        # Build research_data from latest iteration
        if iter_result["success"]:
            # Re-read the latest state to get the full research data
            research_dir = config.data_dir / "research"
            iter_file = research_dir / f"iteration_{iteration}.md"
            if iter_file.exists():
                try:
                    research_data = {
                        "findings": runtime_manager.state.get("verified_claims", []),
                        "summary": iter_file.read_text(encoding="utf-8")[:2000],
                        "_success": True,
                        "has_llm_summary": iter_result.get("has_llm_summary", False),
                        "iteration_report": {
                            "summary": iter_file.read_text(encoding="utf-8")[:500],
                        },
                    }
                except Exception:
                    pass

        # ── Check completion ──
        force_email = os.environ.get("AIKEYPOOL_FORCE_EMAIL", "").lower() == "true"
        runtime_manager.log_completion_decision()

        if runtime_manager.should_send_email() or force_email:
            if force_email and not runtime_manager.should_send_email():
                completion_reason = "Forced email (AIKEYPOOL_FORCE_EMAIL=true)."
            else:
                metrics = runtime_manager.state.get("quality_metrics", {})
                iteration_count = runtime_manager.determine_current_iteration()
                max_iter = runtime_manager.state.get("max_iterations", max_iterations)

                quality_target = config.research_quality_threshold
                overall_quality = metrics.get("overall_quality", 0)
                coverage = metrics.get("coverage", 0)
                verification = metrics.get("verification", 0)
                verified_count = len(runtime_manager.state.get("verified_claims", []))

                quality_met = (
                    overall_quality >= quality_target
                    and coverage >= config.min_coverage
                    and verification >= config.min_verification_score
                    and verified_count >= 3
                )
                max_reached = iteration_count >= max_iter

                if quality_met and max_reached:
                    completion_reason = "Quality threshold reached and maximum iterations reached."
                elif quality_met:
                    completion_reason = "Quality threshold reached."
                else:
                    completion_reason = "Maximum iterations reached (%d/%d)." % (iteration_count, max_iter)

            logger.info("Completion reason: %s", completion_reason)
            break

        # ── Not done yet — increment and continue ──
        logger.info("Continuing to next iteration...")
        runtime_manager.increment_iteration()

    # ── Session summary ──
    elapsed = time.monotonic() - session_start
    logger.info("Research Session Complete")
    logger.info("Iterations: %d", iterations_completed)
    logger.info("Reason: %s", completion_reason)
    logger.info("Total research time: %.1fs", elapsed)

    return research_data, completion_reason


def run_daily_maintenance() -> dict:
    """Execute the full research cycle as a continuous session.

    Runs all iterations in a loop, then generates the final report,
    sends email, archives, and resets. One schedule = one complete
    research session.

    Returns:
        Summary dict with results of each step
    """
    overall_start = time.monotonic()

    # ── Step 0: Initialize ──
    logger.info("=" * 60)
    logger.info("AI KEY POOL RESEARCH SESSION — START")
    logger.info("Date: %s", datetime.now(timezone.utc).isoformat())
    logger.info("=" * 60)

    errors: list[str] = []
    step_results: dict = {}

    config = load_config()
    runtime_manager = RuntimeManager(config.data_dir, config)
    runtime_state = runtime_manager.state

    logger.info("Config: max_iterations=%d, max_runtime=%dm, quality_threshold=%d",
                config.research_max_iterations,
                config.research_max_runtime_minutes,
                config.research_quality_threshold)

    # Dashboard output directory (GitHub Pages serves from here)
    dashboard_data = Path(__file__).parent.parent.parent / "dashboard" / "data"

    # ── Step 0a: Validate configuration ──
    logger.info("STEP START: Configuration validation")
    config_report = validate_config(dashboard_data)
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

    # ── Step 0b: Dynamic key loading ──
    logger.info("STEP START: Dynamic key loading")
    key_load_start = time.monotonic()
    try:
        load_provider_keys(config)
        key_load_duration = time.monotonic() - key_load_start
        logger.info("STEP END: Dynamic key loading — %.1fs", key_load_duration)
    except Exception as e:
        key_load_duration = time.monotonic() - key_load_start
        logger.error("STEP FAIL: Dynamic key loading — %s (%.1fs)", e, key_load_duration)
        errors.append(f"Key loading: {e}")

    # ── Step 0c: Key synchronization ──
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

    # ── Startup diagnostics ──
    _log_startup_diagnostics(config, key_manager, available_providers, provider_status)

    # Derive loaded_providers/keys_loaded for downstream result dict and GitHub Actions diagnostics
    loaded_providers = list(key_manager.registry.get_all_providers())
    keys_loaded = len(key_manager.registry.keys)

    stats = {"registry": {"total_keys": 0, "by_status": {}}, "health": {}}

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

    # ── Step 1b: GitHub Discovery ──
    logger.info("STEP START: GitHub Discovery")
    discovery_result, discovery_duration = _time_step(discover_providers, config)
    if discovery_result is not _EXCEPTION:
        try:
            save_discovery_results(discovery_result, config.data_dir)
        except Exception as e:
            logger.warning("Could not save discovery results: %s", e)
        step_results["discovery"] = {
            "status": "ok",
            "duration_seconds": round(discovery_duration, 2),
            "new_suggestions": discovery_result.get("new_suggestions", 0),
            "sources_checked": discovery_result.get("sources_checked", 0),
        }
        logger.info(
            "STEP END: GitHub Discovery — %d new suggestions in %.1fs",
            discovery_result.get("new_suggestions", 0), discovery_duration,
        )
    else:
        step_results["discovery"] = {
            "status": "error",
            "duration_seconds": round(discovery_duration, 2),
        }
        logger.error("STEP FAIL: GitHub Discovery (%.1fs)", discovery_duration)

    # ── Step 1c: History Tracking ──
    logger.info("STEP START: History Tracking")
    history_tracker = HistoryTracker(config.data_dir)
    # Update provider history from manifest registry
    from ..providers.manifest import manifest_registry
    for manifest in manifest_registry.get_all().values():
        history_tracker.update_provider(
            manifest.provider_id,
            status=manifest.health,
            models=manifest.supported_models,
            capabilities=manifest.capabilities,
        )
    # Record discoveries
    if discovery_result is not _EXCEPTION:
        for suggestion in discovery_result.get("suggestions", []):
            history_tracker.record_discovery(
                suggestion.get("name", ""),
                suggestion.get("source", "github_discovery"),
                suggestion,
            )
    history_tracker.save_history()
    step_results["history_tracking"] = {"status": "ok"}
    logger.info("STEP END: History Tracking")

    # ── Step 2: Research loop (continuous iterations) ──
    logger.info("STEP START: Research loop")
    history_path = config.data_dir / "research_history.json"
    research_data, completion_reason = _run_research_loop(
        config, key_manager, runtime_manager, history_path, overall_start
    )
    logger.info("STEP END: Research loop — reason: %s", completion_reason)

    # ── Step 3: Generate final report ──
    logger.info("STEP START: Final report generation")
    # Inject research_dir into runtime state for deterministic fallback
    runtime_manager.state["_research_dir"] = str(config.data_dir / "research")
    final_report, report_duration = _time_step(
        generate_final_report, config, key_manager, runtime_manager.state
    )
    if final_report is not _EXCEPTION:
        research_data = final_report
        step_results["final_report"] = {
            "status": "ok",
            "duration_seconds": round(report_duration, 2),
        }
        logger.info("STEP END: Final report in %.1fs", report_duration)
    else:
        step_results["final_report"] = {
            "status": "error",
            "duration_seconds": round(report_duration, 2),
        }
        errors.append("Final report generation failed")
        logger.error("STEP FAIL: Final report (%.1fs)", report_duration)

    # ── Step 4: Dashboard status ──
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

    # ── Step 5: Dashboard recommendations ──
    logger.info("STEP START: Dashboard recommendations generation")
    # Get configured providers and discovery results for smart recommendations
    configured_providers_list = list(key_manager.registry.get_all_providers())
    discovery_data = step_results.get("discovery", {})
    # Load discovery results from disk if available
    from .discovery import load_discovery_results
    discovery_results = load_discovery_results(config.data_dir)
    recs_result, recs_duration = _time_step(
        generate_recommendations_json,
        research_data, dashboard_data,
        configured_providers_list, discovery_results,
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

    # ── Step 6: Email ──
    logger.info("Generating Final Report")
    logger.info("Sending Email")
    logger.info("STEP START: Email delivery")
    email_result = False
    email_duration = 0.0

    email_result, email_duration = _time_step(
        _do_send_email, config, stats, research_data, errors,
        available_providers, discovery_results if discovery_result is not _EXCEPTION else None,
    )
    if email_result is not _EXCEPTION:
        step_results["email"] = {
            "status": "sent" if email_result else "skipped",
            "duration_seconds": round(email_duration, 2),
        }
        logger.info("STEP END: Email — %s in %.1fs",
                     "sent" if email_result else "skipped", email_duration)
        logger.info("Email function was called: True")
    else:
        step_results["email"] = {
            "status": "error",
            "duration_seconds": round(email_duration, 2),
        }
        errors.append("Email delivery failed — see EMAIL FAILED log above for SMTP stage details")
        logger.error("STEP FAIL: Email (%.1fs)", email_duration)
        logger.info("Email function was called: True (but failed)")

    # ── Step 7: Archive and reset ──
    logger.info("STEP START: Archive and reset")
    try:
        runtime_manager.archive_cycle()
        logger.info("STEP END: Cycle archived and runtime reset")
    except Exception as e:
        logger.error("STEP FAIL: Archive — %s", e)
        errors.append(f"Archive failed: {e}")

    # ── Summary ──
    overall_duration = time.monotonic() - overall_start
    workflow_status = "completed" if not errors else "completed_with_errors"

    logger.info("=" * 60)
    logger.info("Research Session Complete")
    logger.info("Workflow Finished")
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
        "completion_reason": completion_reason,
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
    available_providers: list[str] = None,
    discovery_results: dict = None,
) -> bool:
    """Send email. Returns True if sent, False if skipped."""
    from ..providers.manifest import manifest_registry

    status_data = {
        "active_provider": config.active_provider,
        "total_keys": stats["registry"]["total_keys"],
        "healthy_keys": stats["registry"]["by_status"].get("active", 0),
        "exhausted_keys": stats["registry"]["by_status"].get("exhausted", 0),
        "disabled_keys": stats["registry"]["by_status"].get("disabled", 0),
        "providers": {},
    }
    # Per-provider summaries from the same registry stats (single source of truth)
    for pname, pdata in stats["registry"].get("by_provider", {}).items():
        status_data["providers"][pname] = {
            "total_keys": pdata["total"],
            "healthy_keys": pdata["active"],
        }

    # Provider health from manifest registry
    provider_health = {}
    for manifest in manifest_registry.get_all().values():
        provider_health[manifest.provider_id] = manifest.health

    # Configured providers list
    configured_providers = list(config.providers.keys())

    try:
        return send_daily_summary(
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            recipient=os.environ.get("EMAIL_RECIPIENT", ""),
            status=status_data,
            recommendations=research_data,
            errors=errors,
            configured_providers=configured_providers,
            discovery_results=discovery_results,
            provider_health=provider_health,
        )
    except EmailDeliveryError as e:
        logger.error("EMAIL FAILED at stage '%s': %s", e.stage, e.detail)
        raise


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

    # ── Section 1: Configuration ──
    logger.info("=== CONFIGURATION ===")
    logger.info("DIAGNOSTIC config_is_valid=%s", config_report.is_valid)
    logger.info("DIAGNOSTIC config_secrets_checked=%d", config_report.total_secrets_checked)
    logger.info("DIAGNOSTIC config_secrets_ok=%d", config_report.total_secrets_ok)
    logger.info("DIAGNOSTIC config_warnings=%d", len(config_report.warnings))
    logger.info("DIAGNOSTIC config_errors=%d", len(config_report.errors))
    logger.info("DIAGNOSTIC providers_detected=%s", json.dumps(config_report.providers_detected))
    logger.info("DIAGNOSTIC providers_configured=%s", json.dumps(config_report.providers_configured))
    for t in config_report.typo_suggestions:
        logger.warning("DIAGNOSTIC config_typo=%s", json.dumps(t))

    # ── Section 2: Providers ──
    logger.info("=== PROVIDERS ===")
    logger.info("DIAGNOSTIC loaded_providers=%s", json.dumps(loaded_providers))
    logger.info("DIAGNOSTIC available_providers=%s", json.dumps(available_providers))
    logger.info("DIAGNOSTIC active_provider=%s", active_provider)
    for pname, pstatus in provider_status.items():
        logger.info("DIAGNOSTIC plugin_%s=%s", pname, pstatus.get("adapter", "unknown"))

    # ── Section 3: Keys ──
    logger.info("=== KEYS ===")
    logger.info("DIAGNOSTIC keys_loaded=%d", keys_loaded)
    health_result = step_results.get("health_check", {})
    logger.info("DIAGNOSTIC health_check_status=%s", health_result.get("status", "unknown"))
    if health_result.get("by_status"):
        for status_name, count in health_result["by_status"].items():
            logger.info("DIAGNOSTIC keys_%s=%d", status_name, count)

    # ── Section 4: Research ──
    logger.info("=== RESEARCH ===")
    research_result = step_results.get("research", {})
    logger.info("DIAGNOSTIC research_status=%s", research_result.get("status", "unknown"))
    logger.info("DIAGNOSTIC research_findings=%d", research_result.get("findings_count", 0))
    logger.info("DIAGNOSTIC research_llm_summary=%s", research_result.get("has_llm_summary", False))

    # ── Section 5: Dashboard ──
    logger.info("=== DASHBOARD ===")
    dashboard_data = Path(__file__).parent.parent.parent / "dashboard" / "data"
    status_exists = (dashboard_data / "status.json").exists()
    recs_exists = (dashboard_data / "recommendations.json").exists()
    config_exists = (dashboard_data / "configuration_report.json").exists()
    logger.info("DIAGNOSTIC dashboard_status_json=%s", "present" if status_exists else "missing")
    logger.info("DIAGNOSTIC dashboard_recommendations_json=%s", "present" if recs_exists else "missing")
    logger.info("DIAGNOSTIC configuration_report_json=%s", "present" if config_exists else "missing")
    status_step = step_results.get("status_report", {})
    recs_step = step_results.get("recommendations", {})
    logger.info("DIAGNOSTIC status_generation=%s", status_step.get("status", "unknown"))
    logger.info("DIAGNOSTIC recommendations_generation=%s", recs_step.get("status", "unknown"))

    # ── Section 6: Email ──
    logger.info("=== EMAIL ===")
    email_step = step_results.get("email", {})
    email_status = email_step.get("status", "unknown")
    logger.info("DIAGNOSTIC email_status=%s", email_status)
    logger.info("DIAGNOSTIC email_duration=%.1fs", email_step.get("duration_seconds", 0))
    if email_status == "error":
        logger.warning("DIAGNOSTIC email_error=see EMAIL FAILED log above for stage details")

    # ── Section 7: Overall Result ──
    logger.info("=== OVERALL RESULT ===")
    logger.info("DIAGNOSTIC workflow_status=%s", workflow_status)
    logger.info("DIAGNOSTIC total_duration=%.1fs", overall_duration)
    logger.info("DIAGNOSTIC error_count=%d", len(errors))
    if errors:
        for err in errors:
            logger.warning("DIAGNOSTIC error=%s", err)

    logger.info("::endgroup::")


if __name__ == "__main__":
    result = run_daily_maintenance()
    print(json.dumps(result, indent=2))
