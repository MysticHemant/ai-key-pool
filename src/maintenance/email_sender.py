"""Email sender for AI Key Pool daily summaries.

Uses generic SMTP. All credentials from environment variables.
Never includes API keys in email content.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

from ..utils.logger import get_logger


logger = get_logger("email")


def send_daily_summary(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    recipient: str,
    status: dict,
    recommendations: dict,
    errors: list[str],
) -> bool:
    """Send daily summary email.

    Args:
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port
        smtp_user: SMTP username
        smtp_password: SMTP password
        recipient: Email recipient
        status: System status dict
        recommendations: Research recommendations dict
        errors: List of error messages from maintenance cycle

    Returns:
        True if sent successfully, False otherwise
    """
    if not all([smtp_host, smtp_user, smtp_password, recipient]):
        logger.warning("Email not configured — skipping daily summary")
        return False

    subject = f"AI Key Pool Daily Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    body = _build_html_body(status, recommendations, errors)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [recipient], msg.as_string())
        logger.info("Daily summary sent to %s", recipient)
        return True
    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False


def _build_html_body(status: dict, recommendations: dict, errors: list[str]) -> str:
    """Build HTML email body from status and recommendations."""
    import html as html_mod

    healthy = status.get("healthy_keys", 0)
    exhausted = status.get("exhausted_keys", 0)
    disabled = status.get("disabled_keys", 0)
    total = status.get("total_keys", 0)
    provider = status.get("active_provider", "unknown")

    findings = recommendations.get("findings", [])
    summary = recommendations.get("summary", "No research data available")

    html = f"""<!DOCTYPE html>
<html>
<head><style>
body {{ font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; }}
h2 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 8px; }}
ul {{ list-style: none; padding: 0; }}
li {{ padding: 4px 0; }}
.ok {{ color: #4CAF50; }}
.warn {{ color: #FF9800; }}
.error {{ color: #f44336; }}
</style></head>
<body>
<h2>AI Key Pool — Daily Report</h2>

<h3>System Health</h3>
<ul>
  <li><strong>Active Provider:</strong> {provider}</li>
  <li><strong>Total Keys:</strong> {total}</li>
  <li class="ok"><strong>Healthy:</strong> {healthy}</li>
  <li class="warn"><strong>Exhausted:</strong> {exhausted}</li>
  <li class="error"><strong>Disabled:</strong> {disabled}</li>
</ul>

<h3>Research Summary</h3>
<p>{summary}</p>
"""

    if findings:
        html += "<h3>Recommendations</h3><ul>"
        for f in findings[:5]:
            name = html_mod.escape(f.get("name", "Unknown"))
            desc = html_mod.escape(f.get("description", ""))
            action = html_mod.escape(f.get("action", "none"))
            html += f'<li><strong>{name}</strong> — {desc} [Action: {action}]</li>'
        html += "</ul>"

    if errors:
        html += "<h3>Warnings</h3><ul>"
        for err in errors:
            html += f'<li class="error">{html_mod.escape(str(err))}</li>'
        html += "</ul>"

    html += f"""
<p style="color: #999; font-size: 12px; margin-top: 40px;">
  Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — AI Key Pool v1.0
</p>
</body></html>"""

    return html
