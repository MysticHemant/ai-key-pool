"""Email sender for AI Key Pool daily summaries.

Uses generic SMTP with detailed stage-by-stage logging.
All credentials from environment variables — never hardcoded.
Never exposes passwords in logs or email content.

Supports two TLS modes:
  - Port 465: SMTP_SSL (implicit TLS) — connection is encrypted from start
  - Port 587: SMTP + STARTTLS (explicit TLS) — upgrade plain connection
  - Override with SMTP_TLS=ssl or SMTP_TLS=starttls
"""

import os
import smtplib
import ssl
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

from ..utils.logger import get_logger


logger = get_logger("email")


class EmailDeliveryError(Exception):
    """Raised when email delivery fails at a specific stage.

    Attributes:
        stage: The SMTP stage where failure occurred.
        detail: Human-readable error description.
    """
    def __init__(self, stage: str, detail: str):
        self.stage = stage
        self.detail = detail
        super().__init__(f"Email delivery failed at {stage}: {detail}")


def send_daily_summary(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    recipient: str,
    status: dict,
    recommendations: dict,
    errors: list[str],
    maintenance_duration: float = 0.0,
    workflow_status: str = "unknown",
    configured_providers: list[str] = None,
    discovery_results: dict = None,
    provider_health: dict = None,
    iterations_completed: int = 0,
) -> bool:
    """Send daily summary email with detailed stage logging.

    Logs every stage: config check, connection, TLS, auth, sender,
    recipient, send result. Returns detailed errors on failure.
    Passwords are never exposed.

    Args:
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port
        smtp_user: SMTP username (sender address)
        smtp_password: SMTP password (never logged)
        recipient: Email recipient
        status: System status dict
        recommendations: Research recommendations dict
        errors: List of error messages from maintenance cycle
        maintenance_duration: Total maintenance duration in seconds
        workflow_status: Overall workflow status string

    Returns:
        True if sent successfully, False otherwise

    Raises:
        EmailDeliveryError: On detailed failure (caller may catch)
    """
    # ── Stage 1: Config validation ──
    logger.info("EMAIL STAGE: Config validation")
    missing = []
    if not smtp_host:
        missing.append("SMTP_HOST")
    if not smtp_port:
        missing.append("SMTP_PORT")
    if not smtp_user:
        missing.append("SMTP_USER")
    if not smtp_password:
        missing.append("SMTP_PASSWORD")
    if not recipient:
        missing.append("EMAIL_RECIPIENT")

    if missing:
        logger.warning(
            "EMAIL SKIP: Missing environment variables: %s",
            ", ".join(missing),
        )
        return False

    logger.info("EMAIL STAGE: Config OK — host=%s, port=%d, sender=%s, recipient=%s",
                smtp_host, smtp_port, smtp_user, recipient)

    subject = f"AI Key Pool Intelligence Briefing — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    body = _build_html_body(status, recommendations, errors, maintenance_duration, workflow_status,
                            configured_providers, discovery_results, provider_health,
                            iterations_completed)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(body, "html"))

    start_time = time.monotonic()

    # ── Stage 2: Determine connection mode ──
    # Port 465 = implicit SSL/TLS (SMTP_SSL)
    # Port 587 = explicit TLS (SMTP + STARTTLS)
    # SMTP_TLS env var can override: "ssl" for SMTP_SSL, "starttls" for STARTTLS
    tls_mode = os.environ.get("SMTP_TLS", "").lower()
    if tls_mode == "ssl":
        use_ssl = True
    elif tls_mode == "starttls":
        use_ssl = False
    else:
        # Auto-detect from port
        use_ssl = (smtp_port == 465)

    if use_ssl:
        logger.info("EMAIL STAGE: Connection mode=SMTP_SSL (port %d, implicit TLS)", smtp_port)
    else:
        logger.info("EMAIL STAGE: Connection mode=SMTP+STARTTLS (port %d, explicit TLS)", smtp_port)

    # ── Stage 3: SMTP connection ──
    logger.info("EMAIL STAGE: Connecting to %s:%d (timeout=30s)", smtp_host, smtp_port)
    server = None
    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            logger.info("EMAIL STAGE: SMTP_SSL with modern TLS context (protocol=%s, verify=%s)",
                        ctx.protocol, ctx.verify_mode)
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30, context=ctx)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
    except smtplib.SMTPConnectError as e:
        logger.error("EMAIL STAGE FAIL: SMTPConnectError at connection — %s", e)
        raise EmailDeliveryError("connection", f"SMTP connect failed: {type(e).__name__}: {e}")
    except ConnectionResetError as e:
        logger.error("EMAIL STAGE FAIL: ConnectionResetError at connection — %s", e)
        raise EmailDeliveryError("connection", f"Connection reset by server: {type(e).__name__}: {e}")
    except BrokenPipeError as e:
        logger.error("EMAIL STAGE FAIL: BrokenPipeError at connection — %s", e)
        raise EmailDeliveryError("connection", f"Broken pipe during connect: {type(e).__name__}: {e}")
    except OSError as e:
        logger.error("EMAIL STAGE FAIL: OSError at connection — errno=%s %s", getattr(e, 'errno', '?'), e)
        raise EmailDeliveryError("connection", f"Network error: {type(e).__name__}: {e}")
    except Exception as e:
        logger.error("EMAIL STAGE FAIL: Unexpected error at connection — %s: %s", type(e).__name__, e)
        raise EmailDeliveryError("connection", f"Connection failed: {type(e).__name__}: {e}")

    # ── Stage 3b: EHLO and server greeting ──
    try:
        ehlo_resp = server.ehlo()
        greeting_code = ehlo_resp[0] if isinstance(ehlo_resp, tuple) else ehlo_resp
        logger.info("EMAIL STAGE: EHLO greeting received — code=%s", greeting_code)
        caps = server.esmtp_features
        if caps:
            logger.info("EMAIL STAGE: Server capabilities — %s", ", ".join(sorted(caps.keys())))
        else:
            logger.info("EMAIL STAGE: No ESMTP capabilities advertised")
    except smtplib.SMTPException as e:
        logger.error("EMAIL STAGE FAIL: SMTPException at EHLO — %s: %s", type(e).__name__, e)
        _safe_quit(server)
        raise EmailDeliveryError("greeting", f"EHLO failed: {type(e).__name__}: {e}")
    except ConnectionResetError as e:
        logger.error("EMAIL STAGE FAIL: ConnectionResetError at EHLO — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("greeting", f"Connection reset during EHLO: {type(e).__name__}: {e}")
    except BrokenPipeError as e:
        logger.error("EMAIL STAGE FAIL: BrokenPipeError at EHLO — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("greeting", f"Broken pipe during EHLO: {type(e).__name__}: {e}")
    except OSError as e:
        logger.error("EMAIL STAGE FAIL: OSError at EHLO — %s: %s", type(e).__name__, e)
        _safe_quit(server)
        raise EmailDeliveryError("greeting", f"Network error during EHLO: {type(e).__name__}: {e}")

    # ── Stage 4: TLS (STARTTLS only — SMTP_SSL is already encrypted) ──
    if use_ssl:
        logger.info("EMAIL STAGE: TLS already active (SMTP_SSL mode)")
    else:
        logger.info("EMAIL STAGE: Starting STARTTLS with %s:%d", smtp_host, smtp_port)
        try:
            server.starttls()
            logger.info("EMAIL STAGE: STARTTLS established with %s:%d", smtp_host, smtp_port)
        except smtplib.SMTPException as e:
            logger.error("EMAIL STAGE FAIL: SMTPException at STARTTLS — %s: %s", type(e).__name__, e)
            _safe_quit(server)
            raise EmailDeliveryError("tls", f"STARTTLS negotiation failed: {type(e).__name__}: {e}")
        except ConnectionResetError as e:
            logger.error("EMAIL STAGE FAIL: ConnectionResetError at STARTTLS — %s", e)
            _safe_quit(server)
            raise EmailDeliveryError("tls", f"Connection reset during STARTTLS: {type(e).__name__}: {e}")
        except BrokenPipeError as e:
            logger.error("EMAIL STAGE FAIL: BrokenPipeError at STARTTLS — %s", e)
            _safe_quit(server)
            raise EmailDeliveryError("tls", f"Broken pipe during STARTTLS: {type(e).__name__}: {e}")
        except OSError as e:
            logger.error("EMAIL STAGE FAIL: OSError at STARTTLS — %s: %s", type(e).__name__, e)
            _safe_quit(server)
            raise EmailDeliveryError("tls", f"Network error during STARTTLS: {type(e).__name__}: {e}")

    # ── Stage 5: Authentication ──
    logger.info("EMAIL STAGE: Authenticating as %s", smtp_user)
    try:
        server.login(smtp_user, smtp_password)
        logger.info("EMAIL STAGE: Authentication successful for %s", smtp_user)
    except smtplib.SMTPAuthenticationError as e:
        logger.error("EMAIL STAGE FAIL: SMTPAuthenticationError — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("auth", f"SMTP authentication failed (check username/password): {type(e).__name__}: {e}")
    except smtplib.SMTPException as e:
        logger.error("EMAIL STAGE FAIL: SMTPException at auth — %s: %s", type(e).__name__, e)
        _safe_quit(server)
        raise EmailDeliveryError("auth", f"Authentication error: {type(e).__name__}: {e}")
    except ConnectionResetError as e:
        logger.error("EMAIL STAGE FAIL: ConnectionResetError at auth — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("auth", f"Connection reset during auth: {type(e).__name__}: {e}")
    except BrokenPipeError as e:
        logger.error("EMAIL STAGE FAIL: BrokenPipeError at auth — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("auth", f"Broken pipe during auth: {type(e).__name__}: {e}")
    except OSError as e:
        logger.error("EMAIL STAGE FAIL: OSError at auth — %s: %s", type(e).__name__, e)
        _safe_quit(server)
        raise EmailDeliveryError("auth", f"Network error during auth: {type(e).__name__}: {e}")

    # ── Stage 6: Send ──
    logger.info("EMAIL STAGE: Sending to %s from %s", recipient, smtp_user)
    try:
        server.sendmail(smtp_user, [recipient], msg.as_string())
        elapsed = time.monotonic() - start_time
        logger.info("EMAIL STAGE: Send successful in %.1fs to %s", elapsed, recipient)
        _safe_quit(server)
        return True
    except smtplib.SMTPRecipientsRefused as e:
        logger.error("EMAIL STAGE FAIL: SMTPRecipientsRefused — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("send", f"Recipient rejected: {type(e).__name__}: {e}")
    except smtplib.SMTPSenderRefused as e:
        logger.error("EMAIL STAGE FAIL: SMTPSenderRefused — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("send", f"Sender rejected: {type(e).__name__}: {e}")
    except smtplib.SMTPDataError as e:
        logger.error("EMAIL STAGE FAIL: SMTPDataError — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("send", f"Data error: {type(e).__name__}: {e}")
    except smtplib.SMTPException as e:
        logger.error("EMAIL STAGE FAIL: SMTPException at send — %s: %s", type(e).__name__, e)
        _safe_quit(server)
        raise EmailDeliveryError("send", f"Send failed: {type(e).__name__}: {e}")
    except ConnectionResetError as e:
        logger.error("EMAIL STAGE FAIL: ConnectionResetError at send — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("send", f"Connection reset during send: {type(e).__name__}: {e}")
    except BrokenPipeError as e:
        logger.error("EMAIL STAGE FAIL: BrokenPipeError at send — %s", e)
        _safe_quit(server)
        raise EmailDeliveryError("send", f"Broken pipe during send: {type(e).__name__}: {e}")
    except OSError as e:
        logger.error("EMAIL STAGE FAIL: OSError at send — %s: %s", type(e).__name__, e)
        _safe_quit(server)
        raise EmailDeliveryError("send", f"Network error during send: {type(e).__name__}: {e}")


def _safe_quit(server) -> None:
    """Quit SMTP connection safely. Works for both SMTP and SMTP_SSL."""
    try:
        server.quit()
    except Exception:
        pass


def _build_html_body(
    status: dict,
    recommendations: dict,
    errors: list[str],
    maintenance_duration: float = 0.0,
    workflow_status: str = "unknown",
    configured_providers: list[str] = None,
    discovery_results: dict = None,
    provider_health: dict = None,
    iterations_completed: int = 0,
) -> str:
    """Build executive intelligence briefing HTML email.

    User-centric structure answering:
    - What changed since last report?
    - Why does it matter?
    - What should I do?
    - Which providers should I evaluate?
    - What capability gaps exist?

    Never repeats findings. Never includes raw markdown.
    Shows "Research unavailable" with reason when data is missing.
    """
    import html as html_mod

    configured_set = set(configured_providers or [])
    sections = recommendations.get("sections", {})
    findings = recommendations.get("findings", [])
    summary = recommendations.get("summary", "")
    provider_summaries = status.get("providers", {})

    # ── Determine research availability ──
    has_findings = len(findings) > 0
    has_sections = bool(sections)
    research_available = has_findings or has_sections
    research_unavailable_reason = ""
    if not research_available:
        if errors:
            research_unavailable_reason = errors[0]
        elif iterations_completed == 0:
            research_unavailable_reason = "No research iterations completed"
        else:
            research_unavailable_reason = "No structured findings produced"

    # ── Compute stats for metadata line ──
    total_findings = sections.get("statistics", {}).get("total_findings", len(findings))
    providers_analyzed = sections.get("statistics", {}).get("providers_analyzed", 0)
    quality_score = sections.get("statistics", {}).get("overall_quality", 0)
    duration_str = f"{maintenance_duration:.0f}s" if maintenance_duration > 0 else "N/A"

    html = """<!DOCTYPE html>
<html>
<head><style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 720px; margin: 0 auto; padding: 20px; color: #1a1a1a; line-height: 1.6; }
h1 { font-size: 22px; color: #111; border-bottom: 3px solid #1976D2; padding-bottom: 8px; margin-bottom: 4px; }
h2 { font-size: 16px; color: #333; margin-top: 28px; margin-bottom: 8px; border-bottom: 1px solid #e0e0e0; padding-bottom: 4px; }
p { margin: 6px 0; }
ul { list-style: none; padding: 0; margin: 8px 0; }
li { padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
th, td { border: 1px solid #e0e0e0; padding: 8px 10px; text-align: left; }
th { background: #f8f9fa; font-weight: 600; color: #333; }
.ok { color: #2e7d32; font-weight: 600; }
.warn { color: #e65100; font-weight: 600; }
.error { color: #c62828; font-weight: 600; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.tag-high { background: #ffcdd2; color: #b71c1c; }
.tag-medium { background: #ffe0b2; color: #e65100; }
.tag-low { background: #c8e6c9; color: #1b5e20; }
.tag-verified { background: #e8f5e9; color: #2e7d32; }
.tag-contradiction { background: #fce4ec; color: #c62828; }
.exec-summary { background: #f5f7fa; border-left: 4px solid #1976D2; padding: 14px 18px; margin: 12px 0; font-size: 14px; }
.exec-summary p { margin: 4px 0; }
.unavailable { background: #fff8e1; border-left: 4px solid #ffa000; padding: 12px 16px; margin: 12px 0; font-size: 13px; color: #5d4037; }
.development-card { background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px 16px; margin: 8px 0; }
.development-title { font-weight: 700; color: #111; font-size: 14px; }
.development-meta { font-size: 12px; color: #666; margin-top: 4px; }
.gap-row td:first-child { font-weight: 600; }
.gap-covered { color: #2e7d32; }
.gap-missing { color: #c62828; }
.health-table td { font-size: 13px; }
.action-item { background: #fff3e0; border-left: 3px solid #ff9800; padding: 8px 12px; margin: 6px 0; font-size: 13px; }
.action-urgent { background: #ffebee; border-left-color: #c62828; }
.metadata { display: flex; gap: 16px; flex-wrap: wrap; margin: 8px 0 16px 0; font-size: 12px; color: #666; }
.metadata span { background: #f5f5f5; padding: 4px 10px; border-radius: 3px; }
a { color: #1565c0; }
.footer { color: #999; font-size: 11px; margin-top: 32px; border-top: 1px solid #eee; padding-top: 10px; }
</style></head>
<body>

<h1>AI Key Pool — Intelligence Briefing</h1>
<p style="color:#666; font-size:13px;">""" + datetime.now(timezone.utc).strftime('%B %d, %Y') + """</p>

<div class="metadata">
  <span>Generation time: """ + duration_str + """</span>
  <span>Research iterations: """ + str(iterations_completed) + """</span>
  <span>Sources analyzed: """ + str(total_findings) + """</span>
  <span>Providers tracked: """ + str(providers_analyzed) + """</span>
</div>
"""

    # ═══════════════════════════════════════════════════════════
    # 1. EXECUTIVE SUMMARY — What changed? Why does it matter? What should I do?
    # ═══════════════════════════════════════════════════════════
    if not research_available:
        html += f"""<div class="unavailable">
<strong>Research unavailable</strong><br>
{html_mod.escape(research_unavailable_reason)}<br>
The report below shows system health and provider status only.
</div>
"""
    else:
        exec_summary = sections.get("executive_summary", summary)
        if exec_summary:
            clean = _strip_markdown(exec_summary)
            lines = [l.strip() for l in clean.split("\n") if l.strip()][:8]
            html += '<div class="exec-summary">\n'
            html += '<strong>Executive Summary</strong>\n'
            for line in lines:
                html += f'<p>{html_mod.escape(line)}</p>\n'
            html += '</div>\n'
        else:
            # Build a deterministic executive summary from available data
            changed_providers = [p for p in provider_summaries if provider_summaries[p].get("healthy_keys", 0) > 0]
            html += '<div class="exec-summary"><strong>Executive Summary</strong>\n'
            if changed_providers:
                html += f'<p>Active providers: {", ".join(sorted(changed_providers))}. '
                html += f'{healthy_count(status)} of {status.get("total_keys", 0)} keys healthy. '
            else:
                html += '<p>No active providers with healthy keys. '
            if quality_score > 0:
                html += f'Research quality score: {quality_score}%. '
            html += 'Review provider health and capability gaps below.</p>\n'
            html += '</div>\n'

    # ═══════════════════════════════════════════════════════════
    # 2. WHAT'S NEW SINCE LAST REPORT
    # ═══════════════════════════════════════════════════════════
    changes = sections.get("changes_since_last_report", {})
    if changes and not changes.get("is_first_report", False):
        new_providers = changes.get("new_providers", [])
        new_models = changes.get("new_models", [])
        rate_limits = changes.get("rate_limit_changes", [])
        free_tiers = changes.get("free_tier_changes", [])
        outages = changes.get("outages", [])

        has_any_change = any([new_providers, new_models, rate_limits, free_tiers, outages])
        if has_any_change:
            html += '<h2>What Changed Since Last Report</h2>\n<ul>\n'
            for p in new_providers[:3]:
                name = p.get("provider", "") if isinstance(p, dict) else str(p)
                html += f'<li><span class="tag tag-high">new</span> {html_mod.escape(name)} added to tracked providers</li>\n'
            for m in new_models[:3]:
                model = m.get("model", "") if isinstance(m, dict) else str(m)
                prov = m.get("provider", "") if isinstance(m, dict) else ""
                html += f'<li><span class="tag tag-medium">model</span> {html_mod.escape(model)}'
                if prov:
                    html += f' ({html_mod.escape(prov)})'
                html += '</li>\n'
            for rl in rate_limits[:2]:
                change = rl.get("change", str(rl)) if isinstance(rl, dict) else str(rl)
                html += f'<li><span class="tag tag-contradiction">rate limit</span> {html_mod.escape(change[:120])}</li>\n'
            for ft in free_tiers[:2]:
                change = ft.get("change", str(ft)) if isinstance(ft, dict) else str(ft)
                html += f'<li><span class="tag tag-verified">free tier</span> {html_mod.escape(change[:120])}</li>\n'
            for o in outages[:2]:
                reason = o.get("reason", "unknown") if isinstance(o, dict) else str(o)
                html += f'<li><span class="tag tag-contradiction">outage</span> {html_mod.escape(reason[:120])}</li>\n'
            html += '</ul>\n'

    # ═══════════════════════════════════════════════════════════
    # 3. TOP 3 RECOMMENDED PROVIDERS (never already configured)
    # ═══════════════════════════════════════════════════════════
    suggested = sections.get("suggested_providers", [])
    if not suggested and discovery_results:
        for s in discovery_results.get("suggestions", []):
            name = s.get("name", "").lower()
            if name and name not in configured_set:
                suggested.append(s)

    # Also check findings for provider recommendations
    if not suggested:
        for f in findings:
            if f.get("action") == "add_key" or f.get("importance") == "add_provider":
                prov = f.get("provider", "").lower()
                if prov and prov not in configured_set:
                    suggested.append({"name": prov, "description": f.get("claim", f.get("description", ""))})

    if suggested:
        html += '<h2>Recommended Providers to Evaluate</h2>\n'
        html += '<p style="font-size:13px;color:#666;">Only providers not yet configured that may fill capability gaps:</p>\n'
        html += '<table>\n'
        html += '<tr><th>Provider</th><th>Why Consider</th><th>Free Tier</th></tr>\n'
        seen_names = set()
        shown = 0
        for s in suggested:
            if shown >= 3:
                break
            name = s.get("name", "").lower() if isinstance(s, dict) else str(s).lower()
            if name in seen_names or name in configured_set:
                continue
            seen_names.add(name)
            display = html_mod.escape(str(s.get("display_name", s.get("name", name)))) if isinstance(s, dict) else html_mod.escape(str(s))
            why = html_mod.escape(str(s.get("why", s.get("description", "May fill capability gap"))))[:120] if isinstance(s, dict) else ""
            free_tier = "Yes" if isinstance(s, dict) and s.get("free_tier") else "—"
            html += f'<tr><td><strong>{display}</strong></td>'
            html += f'<td>{why}</td>'
            html += f'<td>{free_tier}</td></tr>\n'
            shown += 1
        html += '</table>\n'

    # ═══════════════════════════════════════════════════════════
    # 4. CAPABILITY GAP ANALYSIS
    # ═══════════════════════════════════════════════════════════
    from ..providers.manifest import manifest_registry, CAPABILITY_REASONING, CAPABILITY_CODING, CAPABILITY_LONG_CONTEXT, CAPABILITY_VISION, CAPABILITY_FAST_INFERENCE

    all_capabilities = [
        ("Reasoning", CAPABILITY_REASONING),
        ("Coding", CAPABILITY_CODING),
        ("Long Context", CAPABILITY_LONG_CONTEXT),
        ("Vision", CAPABILITY_VISION),
        ("Fast Inference", CAPABILITY_FAST_INFERENCE),
    ]

    # Build provider capability map from manifest
    provider_caps = {}
    for manifest in manifest_registry.get_all().values():
        provider_caps[manifest.provider_id] = set(manifest.capabilities)

    # Determine configured capabilities
    configured_capabilities = set()
    for pname in configured_set:
        if pname in provider_caps:
            configured_capabilities.update(provider_caps[pname])

    html += '<h2>Capability Gap Analysis</h2>\n'
    html += '<table>\n'
    html += '<tr><th>Capability</th><th>Status</th><th>Covered By</th></tr>\n'
    for cap_name, cap_id in all_capabilities:
        covered_by = []
        for pname in sorted(configured_set):
            if pname in provider_caps and cap_id in provider_caps[pname]:
                covered_by.append(pname)

        if covered_by:
            html += f'<tr class="gap-row"><td>{cap_name}</td>'
            html += f'<td class="gap-covered">✓ Covered</td>'
            html += f'<td>{", ".join(covered_by)}</td></tr>\n'
        else:
            # Check if any suggested provider covers this
            suggestions_for_cap = []
            for s in suggested:
                if isinstance(s, dict):
                    s_caps = set(s.get("capabilities", []))
                    if cap_id in s_caps:
                        suggestions_for_cap.append(s.get("name", ""))
            html += f'<tr class="gap-row"><td>{cap_name}</td>'
            html += f'<td class="gap-missing">✗ Missing</td>'
            if suggestions_for_cap:
                html += f'<td style="font-size:12px;">Consider: {", ".join(suggestions_for_cap[:2])}</td>'
            else:
                html += '<td style="font-size:12px;color:#999;">No provider available</td>'
            html += '</tr>\n'
    html += '</table>\n'

    # ═══════════════════════════════════════════════════════════
    # 5. KEY HEALTH (with status, failure reason, reliability, action)
    # ═══════════════════════════════════════════════════════════
    html += '<h2>Key Health</h2>\n'
    html += '<table class="health-table">\n'
    html += '<tr><th>Provider</th><th>Keys</th><th>Healthy</th><th>Status</th><th>Reliability</th><th>Recommended Action</th></tr>\n'

    for pname in sorted(provider_summaries.keys()):
        pinfo = provider_summaries[pname]
        ptotal = pinfo.get("total_keys", 0)
        phealthy = pinfo.get("healthy_keys", 0)
        pexhausted = pinfo.get("exhausted_keys", 0)
        pdisabled = pinfo.get("disabled_keys", 0)

        # Determine status and action
        if ptotal == 0:
            status_str = "Not Configured"
            status_cls = ""
            reliability = "—"
            action = "Add API keys"
        elif phealthy > 0:
            status_str = "Healthy"
            status_cls = "ok"
            reliability = f"{phealthy}/{ptotal} keys"
            action = "No action needed"
        elif pexhausted > 0:
            status_str = "Exhausted"
            status_cls = "warn"
            reliability = "0 healthy"
            action = "Add new keys or wait for rate limit reset"
        elif pdisabled > 0:
            status_str = "Disabled"
            status_cls = "error"
            reliability = "0 healthy"
            action = "Investigate failures, re-enable keys"
        else:
            status_str = "Unknown"
            status_cls = ""
            reliability = "—"
            action = "Check key configuration"

        # Add failure reason from manifest health
        manifest_health = provider_health.get(pname, "unknown") if provider_health else "unknown"
        if manifest_health == "unhealthy" and phealthy == 0:
            status_str = "Unhealthy"
            status_cls = "error"
            action = "Replace keys or check provider status"

        html += f'<tr><td><strong>{html_mod.escape(pname)}</strong></td>'
        html += f'<td>{ptotal}</td>'
        html += f'<td>{phealthy}</td>'
        html += f'<td class="{status_cls}">{status_str}</td>'
        html += f'<td>{reliability}</td>'
        html += f'<td style="font-size:12px;">{html_mod.escape(action)}</td></tr>\n'

    html += '</table>\n'

    # ═══════════════════════════════════════════════════════════
    # 6. TOP DEVELOPMENTS (deduplicated, max 5)
    # ═══════════════════════════════════════════════════════════
    if research_available:
        top_developments = sections.get("top_5_developments", [])
        if not top_developments and findings:
            seen_titles = set()
            for f in sorted(findings, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("confidence", "low"), 3)):
                title = f.get("title", f.get("claim", f.get("description", "")))
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                top_developments.append({
                    "title": title,
                    "provider": f.get("provider", ""),
                    "category": f.get("category", ""),
                    "confidence": f.get("confidence", "medium"),
                    "why_it_matters": f.get("description", f.get("claim", ""))[:150],
                })
                if len(top_developments) >= 5:
                    break

        if top_developments:
            html += '<h2>Top Developments</h2>\n'
            for i, dev in enumerate(top_developments[:5], 1):
                title = html_mod.escape(str(dev.get("title", "Unknown")))
                provider = html_mod.escape(str(dev.get("provider", "")))
                confidence = html_mod.escape(str(dev.get("confidence", "medium")))
                why = html_mod.escape(str(dev.get("why_it_matters", "")))
                conf_class = f"tag-{confidence}"
                html += f'<div class="development-card">\n'
                html += f'  <div class="development-title">{i}. {title}</div>\n'
                html += f'  <div class="development-meta">{provider} &middot; <span class="tag {conf_class}">{confidence}</span></div>\n'
                if why:
                    html += f'  <p style="font-size:13px;color:#444;margin-top:6px;">{why}</p>\n'
                html += '</div>\n'

    # ═══════════════════════════════════════════════════════════
    # 7. VERIFIED FINDINGS (high confidence only)
    # ═══════════════════════════════════════════════════════════
    if research_available:
        verified = sections.get("verified_findings", [])
        if not verified:
            for f in findings:
                if f.get("confidence") == "high":
                    verified.append({
                        "claim": f.get("claim", f.get("description", "")),
                        "source": f.get("source", ""),
                    })

        if verified:
            html += '<h2>Verified Findings</h2>\n<ul>\n'
            seen_claims = set()
            for v in verified[:8]:
                claim = html_mod.escape(str(v.get("claim", "")))
                if claim in seen_claims:
                    continue
                seen_claims.add(claim)
                source = html_mod.escape(str(v.get("source", ""))[:60])
                html += f'<li><span class="tag tag-verified">verified</span> <strong>{claim}</strong>'
                if source:
                    html += f' <span style="color:#999;font-size:11px;">({source})</span>'
                html += '</li>\n'
            html += '</ul>\n'

    # ═══════════════════════════════════════════════════════════
    # 8. CONTRADICTIONS (never silently removed)
    # ═══════════════════════════════════════════════════════════
    if research_available:
        contradictions = sections.get("contradictions", [])
        if contradictions:
            html += '<h2>Contradictions Detected</h2>\n'
            html += '<p style="font-size:12px;color:#666;">Conflicting information found across sources. Review before acting.</p>\n'
            html += '<ul>\n'
            for c in contradictions[:5]:
                if isinstance(c, dict):
                    claim = html_mod.escape(str(c.get("claim", str(c)))[:120])
                    resolution = c.get("resolution_status", "unresolved")
                else:
                    claim = html_mod.escape(str(c)[:120])
                    resolution = "unresolved"
                html += f'<li><span class="tag tag-contradiction">{html_mod.escape(resolution)}</span> {claim}</li>\n'
            html += '</ul>\n'

    # ═══════════════════════════════════════════════════════════
    # 9. ACTION ITEMS (max 5, specific, actionable)
    # ═══════════════════════════════════════════════════════════
    action_items = sections.get("action_items", [])
    if not action_items:
        action_items = recommendations.get("action_items", [])

    if action_items:
        html += '<h2>Action Items</h2>\n'
        seen_actions = set()
        shown = 0
        for item in action_items:
            if shown >= 5:
                break
            if isinstance(item, dict):
                action_text = html_mod.escape(str(item.get("action", item.get("reason", str(item)))))[:150]
                priority = item.get("priority", "medium")
            else:
                action_text = html_mod.escape(str(item))[:150]
                priority = "medium"
            if action_text in seen_actions:
                continue
            seen_actions.add(action_text)
            css_class = "action-item action-urgent" if priority == "high" else "action-item"
            html += f'<div class="{css_class}"><strong>[{html_mod.escape(priority.upper())}]</strong> {action_text}</div>\n'
            shown += 1

    # ═══════════════════════════════════════════════════════════
    # ERRORS / WARNINGS
    # ═══════════════════════════════════════════════════════════
    if errors:
        html += '<h2>System Warnings</h2>\n<ul>\n'
        seen_errors = set()
        for err in errors[:5]:
            err_text = html_mod.escape(str(err))
            if err_text in seen_errors:
                continue
            seen_errors.add(err_text)
            html += f'<li class="error">{err_text}</li>\n'
        html += '</ul>\n'

    # ═══════════════════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════════════════
    html += f"""
<div class="footer">
  AI Key Pool Intelligence Briefing &middot; {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &middot; v1.2.0
</div>
</body></html>"""

    return html


def healthy_count(status: dict) -> int:
    """Get healthy key count from status dict."""
    return status.get("healthy_keys", 0)


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting from text. Never outputs raw markdown."""
    if not text:
        return ""
    import re
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

    return html
