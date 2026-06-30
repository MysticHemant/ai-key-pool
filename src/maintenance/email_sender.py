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
                            configured_providers, discovery_results, provider_health)

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
) -> str:
    """Build executive intelligence briefing HTML email.

    Structure:
    1. Executive Summary (max 8 lines)
    2. Top 5 AI Developments
    3. Provider Intelligence (comparison)
    4. New Providers Discovered
    5. Key Health
    6. Verified Findings
    7. Contradictions
    8. Action Items (max 5)

    Never repeats findings. Never includes raw markdown.
    Written like a senior AI industry analyst briefing.
    """
    import html as html_mod

    configured_set = set(configured_providers or [])
    sections = recommendations.get("sections", {})
    findings = recommendations.get("findings", [])
    summary = recommendations.get("summary", "")
    provider_summaries = status.get("providers", {})
    duration_str = f"{maintenance_duration:.1f}s" if maintenance_duration else "N/A"

    html = """<!DOCTYPE html>
<html>
<head><style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 720px; margin: 0 auto; padding: 20px; color: #1a1a1a; line-height: 1.6; }
h1 { font-size: 22px; color: #111; border-bottom: 3px solid #1976D2; padding-bottom: 8px; margin-bottom: 4px; }
h2 { font-size: 17px; color: #333; margin-top: 28px; margin-bottom: 8px; border-bottom: 1px solid #e0e0e0; padding-bottom: 4px; }
h3 { font-size: 14px; color: #555; margin-top: 16px; margin-bottom: 6px; }
p { margin: 6px 0; }
ul { list-style: none; padding: 0; margin: 8px 0; }
li { padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
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
.development-card { background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px 16px; margin: 8px 0; }
.development-title { font-weight: 700; color: #111; font-size: 14px; }
.development-meta { font-size: 12px; color: #666; margin-top: 4px; }
.action-item { background: #fff3e0; border-left: 3px solid #ff9800; padding: 8px 12px; margin: 6px 0; font-size: 13px; }
.health-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0; }
.health-card { padding: 8px 12px; border-radius: 4px; font-size: 13px; }
.health-healthy { background: #e8f5e9; }
.health-unhealthy { background: #ffebee; }
.health-unknown { background: #f5f5f5; }
a { color: #1565c0; }
.footer { color: #999; font-size: 11px; margin-top: 32px; border-top: 1px solid #eee; padding-top: 10px; }
</style></head>
<body>

<h1>AI Key Pool — Intelligence Briefing</h1>
<p style="color:#666; font-size:13px;">""" + datetime.now(timezone.utc).strftime('%B %d, %Y') + f""" &middot; Generated in {duration_str}</p>
"""

    # ═══════════════════════════════════════════════════════════
    # 1. EXECUTIVE SUMMARY (max 8 lines)
    # ═══════════════════════════════════════════════════════════
    exec_summary = sections.get("executive_summary", summary)
    if exec_summary:
        clean = _strip_markdown(exec_summary)
        # Limit to 8 lines
        lines = [l.strip() for l in clean.split("\n") if l.strip()][:8]
        html += '<div class="exec-summary">\n'
        html += '<strong>Executive Summary</strong>\n'
        for line in lines:
            html += f'<p>{html_mod.escape(line)}</p>\n'
        html += '</div>\n'
    else:
        html += '<div class="exec-summary"><strong>Executive Summary</strong>\n'
        html += '<p>No significant changes detected in this reporting period.</p>\n'
        html += '</div>\n'

    # ═══════════════════════════════════════════════════════════
    # 2. TOP 5 AI DEVELOPMENTS
    # ═══════════════════════════════════════════════════════════
    top_developments = sections.get("top_5_developments", [])
    if not top_developments and findings:
        # Derive from findings
        for f in sorted(findings, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("confidence", "low"), 3))[:5]:
            top_developments.append({
                "title": f.get("title", f.get("claim", f.get("description", "Unknown development"))),
                "provider": f.get("provider", "Unknown"),
                "category": f.get("category", ""),
                "confidence": f.get("confidence", "medium"),
                "why_it_matters": f.get("description", f.get("claim", ""))[:150],
            })

    if top_developments:
        html += '<h2>Top 5 AI Developments</h2>\n'
        for i, dev in enumerate(top_developments[:5], 1):
            title = html_mod.escape(str(dev.get("title", "Unknown")))
            provider = html_mod.escape(str(dev.get("provider", "")))
            category = html_mod.escape(str(dev.get("category", "")))
            confidence = html_mod.escape(str(dev.get("confidence", "medium")))
            why = html_mod.escape(str(dev.get("why_it_matters", "")))
            conf_class = f"tag-{confidence}"
            html += f'<div class="development-card">\n'
            html += f'  <div class="development-title">{i}. {title}</div>\n'
            html += f'  <div class="development-meta">{provider}'
            if category:
                html += f' &middot; {category}'
            html += f' &middot; <span class="tag {conf_class}">{confidence}</span></div>\n'
            if why:
                html += f'  <p style="font-size:13px;color:#444;margin-top:6px;">{why}</p>\n'
            html += '</div>\n'

    # ═══════════════════════════════════════════════════════════
    # 3. PROVIDER INTELLIGENCE (comparison)
    # ═══════════════════════════════════════════════════════════
    if provider_summaries:
        html += '<h2>Provider Intelligence</h2>\n'
        html += '<table>\n'
        html += '<tr><th>Provider</th><th>Keys</th><th>Healthy</th><th>Status</th></tr>\n'
        for pname, pinfo in sorted(provider_summaries.items()):
            ptotal = pinfo.get("total_keys", 0)
            phealthy = pinfo.get("healthy_keys", 0)
            status_str = "Healthy" if phealthy > 0 else "Degraded"
            status_cls = "ok" if phealthy > 0 else "warn"
            html += f'<tr><td><strong>{html_mod.escape(pname)}</strong></td>'
            html += f'<td>{ptotal}</td>'
            html += f'<td>{phealthy}</td>'
            html += f'<td class="{status_cls}">{status_str}</td></tr>\n'
        html += '</table>\n'

    # Provider comparison from research (if available)
    provider_comparison = sections.get("provider_comparison", [])
    if provider_comparison:
        html += '<table>\n'
        html += '<tr><th>Provider</th><th>Findings</th><th>Key Developments</th></tr>\n'
        for pc in provider_comparison[:8]:
            pc_name = html_mod.escape(str(pc.get("provider", "")))
            pc_count = pc.get("findings_count", 0)
            pc_summary = html_mod.escape(str(pc.get("summary", ""))[:120])
            html += f'<tr><td>{pc_name}</td><td>{pc_count}</td><td>{pc_summary}</td></tr>\n'
        html += '</table>\n'

    # ═══════════════════════════════════════════════════════════
    # 4. NEW PROVIDERS DISCOVERED (only non-configured)
    # ═══════════════════════════════════════════════════════════
    suggested = sections.get("suggested_providers", [])
    if not suggested and discovery_results:
        for s in discovery_results.get("suggestions", []):
            name = s.get("name", "").lower()
            if name and name not in configured_set:
                suggested.append(s)

    # Also check recommendations for new_providers
    rec_new_providers = recommendations.get("new_providers", [])
    for np in rec_new_providers:
        name = np.get("name", "").lower() if isinstance(np, dict) else str(np).lower()
        if name and name not in configured_set:
            if isinstance(np, dict) and np not in suggested:
                suggested.append(np)

    if suggested:
        html += '<h2>New Providers Discovered</h2>\n'
        html += '<p style="font-size:13px;color:#666;">Providers not yet configured that may fill capability gaps:</p>\n'
        html += '<table>\n'
        html += '<tr><th>Provider</th><th>Endpoint</th><th>Free Tier</th><th>Why Consider</th></tr>\n'
        seen_names = set()
        for s in suggested:
            name = s.get("name", "").lower() if isinstance(s, dict) else str(s).lower()
            if name in seen_names or name in configured_set:
                continue
            seen_names.add(name)
            display = html_mod.escape(str(s.get("display_name", s.get("name", name)))) if isinstance(s, dict) else html_mod.escape(str(s))
            endpoint = html_mod.escape(str(s.get("endpoint", "N/A")))[:60] if isinstance(s, dict) else "N/A"
            free_tier = "Yes" if isinstance(s, dict) and s.get("free_tier") else "Unknown"
            why = html_mod.escape(str(s.get("why", s.get("description", "May fill capability gap"))))[:100] if isinstance(s, dict) else ""
            html += f'<tr><td><strong>{display}</strong></td>'
            html += f'<td style="font-size:12px;">{endpoint}</td>'
            html += f'<td>{free_tier}</td>'
            html += f'<td>{why}</td></tr>\n'
        html += '</table>\n'

    # ═══════════════════════════════════════════════════════════
    # 5. KEY HEALTH
    # ═══════════════════════════════════════════════════════════
    html += '<h2>Key Health</h2>\n'
    html += '<div class="health-grid">\n'
    total = status.get("total_keys", 0)
    healthy = status.get("healthy_keys", 0)
    exhausted = status.get("exhausted_keys", 0)
    disabled = status.get("disabled_keys", 0)
    active_provider = status.get("active_provider", "unknown")

    html += f'<div class="health-card health-healthy"><strong>{active_provider}</strong><br>'
    html += f'{healthy}/{total} healthy keys<br>'
    if exhausted:
        html += f'<span class="warn">{exhausted} exhausted</span>'
    if disabled:
        html += f' <span class="error">{disabled} disabled</span>'
    html += '</div>\n'

    # Per-provider health from manifest
    if provider_health:
        for pname, ph in sorted(provider_health.items()):
            ph_class = "health-healthy" if ph == "healthy" else "health-unhealthy" if ph == "unhealthy" else "health-unknown"
            html += f'<div class="health-card {ph_class}"><strong>{html_mod.escape(pname)}</strong><br>{html_mod.escape(ph)}</div>\n'

    html += '</div>\n'

    # ═══════════════════════════════════════════════════════════
    # 6. VERIFIED FINDINGS
    # ═══════════════════════════════════════════════════════════
    verified = sections.get("verified_findings", [])
    if not verified:
        # Derive from high-confidence findings
        for f in findings:
            if f.get("confidence") == "high":
                verified.append({
                    "claim": f.get("claim", f.get("description", "")),
                    "evidence": f.get("evidence", ""),
                    "source": f.get("source", ""),
                    "confidence": "high",
                })

    if verified:
        html += '<h2>Verified Findings</h2>\n'
        html += '<ul>\n'
        for v in verified[:10]:
            claim = html_mod.escape(str(v.get("claim", "")))
            source = html_mod.escape(str(v.get("source", ""))[:80])
            html += f'<li><span class="tag tag-verified">verified</span> <strong>{claim}</strong>'
            if source:
                html += f' <span style="color:#999;font-size:12px;">({source})</span>'
            html += '</li>\n'
        html += '</ul>\n'

    # ═══════════════════════════════════════════════════════════
    # 7. CONTRADICTIONS
    # ═══════════════════════════════════════════════════════════
    contradictions = sections.get("contradictions", [])
    if contradictions:
        html += '<h2>Contradictions</h2>\n'
        html += '<ul>\n'
        for c in contradictions[:5]:
            claim = html_mod.escape(str(c.get("claim", c))) if isinstance(c, dict) else html_mod.escape(str(c))
            html += f'<li><span class="tag tag-contradiction">conflict</span> {claim}</li>\n'
        html += '</ul>\n'

    # ═══════════════════════════════════════════════════════════
    # 8. ACTION ITEMS (max 5)
    # ═══════════════════════════════════════════════════════════
    action_items = sections.get("action_items", [])
    if not action_items:
        action_items = recommendations.get("action_items", [])

    if action_items:
        html += '<h2>Action Items</h2>\n'
        for item in action_items[:5]:
            if isinstance(item, dict):
                action_text = html_mod.escape(str(item.get("action", item.get("reason", str(item)))))
                priority = item.get("priority", "medium")
            else:
                action_text = html_mod.escape(str(item))
                priority = "medium"
            html += f'<div class="action-item"><strong>[{html_mod.escape(priority.upper())}]</strong> {action_text}</div>\n'

    # ═══════════════════════════════════════════════════════════
    # ERRORS / WARNINGS
    # ═══════════════════════════════════════════════════════════
    if errors:
        html += '<h2>Warnings</h2>\n<ul>\n'
        for err in errors[:5]:
            html += f'<li class="error">{html_mod.escape(str(err))}</li>\n'
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
