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

    subject = f"AI Key Pool Daily Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    body = _build_html_body(status, recommendations, errors, maintenance_duration, workflow_status)

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
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        logger.info("EMAIL STAGE: Connected to %s:%d", smtp_host, smtp_port)
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
) -> str:
    """Build comprehensive HTML email body.

    Includes: system status, provider summary, research summary,
    recommendations, warnings, maintenance duration, workflow status.

    Args:
        status: System status dict
        recommendations: Research data dict
        errors: Error messages
        maintenance_duration: Duration in seconds
        workflow_status: Workflow status string

    Returns:
        Complete HTML email body
    """
    import html as html_mod

    healthy = status.get("healthy_keys", 0)
    exhausted = status.get("exhausted_keys", 0)
    disabled = status.get("disabled_keys", 0)
    total = status.get("total_keys", 0)
    provider = status.get("active_provider", "unknown")
    provider_summaries = status.get("providers", {})

    findings = recommendations.get("findings", [])
    summary = recommendations.get("summary", "No research data available")
    new_providers = recommendations.get("new_providers", [])
    new_models = recommendations.get("new_models", [])
    pricing_changes = recommendations.get("pricing_changes", [])
    free_tier_changes = recommendations.get("free_tier_changes", [])
    breaking_changes = recommendations.get("breaking_changes", [])

    duration_str = f"{maintenance_duration:.1f}s" if maintenance_duration else "N/A"
    status_class = "ok" if workflow_status in ("completed",) else "warn" if "error" in workflow_status else "error"

    html = f"""<!DOCTYPE html>
<html>
<head><style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 700px; margin: 0 auto; padding: 20px; color: #333; }}
h2 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 8px; }}
h3 {{ color: #555; margin-top: 24px; }}
ul {{ list-style: none; padding: 0; }}
li {{ padding: 4px 0; line-height: 1.5; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f5f5f5; font-weight: 600; }}
.ok {{ color: #4CAF50; font-weight: 600; }}
.warn {{ color: #FF9800; font-weight: 600; }}
.error {{ color: #f44336; font-weight: 600; }}
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-right: 4px; }}
.tag-high {{ background: #ffebee; color: #c62828; }}
.tag-medium {{ background: #fff3e0; color: #e65100; }}
.tag-low {{ background: #e8f5e9; color: #2e7d32; }}
a {{ color: #1976D2; }}
</style></head>
<body>

<h2>AI Key Pool — Daily Report</h2>
<p><strong>Date:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>

<h3>System Status</h3>
<table>
  <tr><th>Metric</th><th>Value</th></tr>
  <tr><td>Workflow Status</td><td class="{status_class}">{html_mod.escape(workflow_status)}</td></tr>
  <tr><td>Active Provider</td><td>{html_mod.escape(provider)}</td></tr>
  <tr><td>Total Keys</td><td>{total}</td></tr>
  <tr><td class="ok">Healthy Keys</td><td>{healthy}</td></tr>
  <tr><td class="warn">Exhausted Keys</td><td>{exhausted}</td></tr>
  <tr><td class="error">Disabled Keys</td><td>{disabled}</td></tr>
  <tr><td>Maintenance Duration</td><td>{duration_str}</td></tr>
</table>
"""

    # Provider summaries
    if provider_summaries:
        html += "<h3>Provider Summary</h3>\n<table>\n"
        html += "<tr><th>Provider</th><th>Total Keys</th><th>Healthy</th></tr>\n"
        for pname, pinfo in provider_summaries.items():
            ptotal = pinfo.get("total_keys", 0)
            phealthy = pinfo.get("healthy_keys", 0)
            html += f"  <tr><td>{html_mod.escape(pname)}</td><td>{ptotal}</td><td>{phealthy}</td></tr>\n"
        html += "</table>\n"

    # Research summary
    html += f"<h3>Research Summary</h3>\n<p>{html_mod.escape(summary)}</p>\n"

    if new_providers:
        html += "<h3>New Providers</h3><ul>\n"
        for p in new_providers:
            html += f"  <li>{html_mod.escape(str(p))}</li>\n"
        html += "</ul>\n"

    if new_models:
        html += "<h3>New Models</h3><ul>\n"
        for m in new_models:
            html += f"  <li>{html_mod.escape(str(m))}</li>\n"
        html += "</ul>\n"

    if pricing_changes:
        html += "<h3>Pricing Changes</h3><ul>\n"
        for c in pricing_changes:
            html += f"  <li>{html_mod.escape(str(c))}</li>\n"
        html += "</ul>\n"

    if free_tier_changes:
        html += "<h3>Free Tier Changes</h3><ul>\n"
        for c in free_tier_changes:
            html += f"  <li>{html_mod.escape(str(c))}</li>\n"
        html += "</ul>\n"

    if breaking_changes:
        html += "<h3>Breaking Changes</h3><ul>\n"
        for c in breaking_changes:
            html += f'  <li class="error">{html_mod.escape(str(c))}</li>\n'
        html += "</ul>\n"

    # Detailed findings
    if findings:
        html += "<h3>Detailed Findings</h3>\n<table>\n"
        html += "<tr><th>Provider</th><th>Description</th><th>Type</th><th>Action</th><th>Confidence</th></tr>\n"
        for f in findings[:15]:
            fprovider = html_mod.escape(str(f.get("provider", "")))
            fdesc = html_mod.escape(str(f.get("description", ""))[:100])
            ftype = html_mod.escape(str(f.get("type", "")))
            faction = html_mod.escape(str(f.get("action", "")))
            fconfidence = html_mod.escape(str(f.get("confidence", "")))
            furl = f.get("url", "")
            if furl:
                fdesc = f'<a href="{html_mod.escape(furl)}">{fdesc}</a>'
            confidence_class = "tag-high" if fconfidence == "high" else "tag-medium" if fconfidence == "medium" else "tag-low"
            html += f"  <tr><td>{fprovider}</td><td>{fdesc}</td><td>{ftype}</td><td>{faction}</td><td><span class=\"tag {confidence_class}\">{fconfidence}</span></td></tr>\n"
        html += "</table>\n"

    # Recommendations
    recs = recommendations.get("recommendations", [])
    if recs:
        html += "<h3>Recommended Actions</h3><ul>\n"
        for r in recs:
            priority = r.get("priority", "medium")
            action = html_mod.escape(str(r.get("action", "")))
            reason = html_mod.escape(str(r.get("reason", "")))
            html += f'  <li><span class="tag tag-{priority}">{priority}</span> {action} — {reason}</li>\n'
        html += "</ul>\n"

    # Links to official announcements
    urls_seen = set()
    for f in findings:
        url = f.get("url", "")
        if url and url not in urls_seen:
            urls_seen.add(url)
    if urls_seen:
        html += "<h3>Official Sources</h3><ul>\n"
        for url in list(urls_seen)[:10]:
            html += f'  <li><a href="{html_mod.escape(url)}">{html_mod.escape(url[:80])}</a></li>\n'
        html += "</ul>\n"

    # Warnings / errors
    if errors:
        html += "<h3>Warnings</h3><ul>\n"
        for err in errors:
            html += f'  <li class="error">{html_mod.escape(str(err))}</li>\n'
        html += "</ul>\n"

    html += f"""
<p style="color: #999; font-size: 12px; margin-top: 40px; border-top: 1px solid #eee; padding-top: 12px;">
  Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — AI Key Pool v1.2.0
</p>
</body></html>"""

    return html
