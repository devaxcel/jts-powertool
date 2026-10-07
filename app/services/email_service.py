import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

from app.tools.secrets_manager import get_secret

logger = logging.getLogger("jts_powertool.email_service")

def send_password_setup_email(
    to_email: str,
    recipient_name: str,
    token: str,
    workspace_name: str = "JTS Powertool",
    custom_base_url: str = None,
) -> dict:
    """
    Sends an invitation email with a secure token to allow the user to set their password.
    Supports AWS Secrets Manager & environment variables.
    If SMTP credentials are not configured, logs the link and returns gracefully in mock mode.
    """
    if not to_email:
        return {"success": False, "error": "Recipient email is required."}

    base_url = (custom_base_url or get_secret("FRONTEND_BASE_URL", os.getenv("FRONTEND_BASE_URL", "http://localhost:3000"))).rstrip("/")
    setup_url = f"{base_url}/set-password?token={token}"

    smtp_host = get_secret("SMTP_HOST", os.getenv("SMTP_HOST", "smtp.gmail.com")).strip()
    smtp_port_raw = get_secret("SMTP_PORT", os.getenv("SMTP_PORT", "587")).strip()
    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        smtp_port = 587

    smtp_user = get_secret("SMTP_USER", os.getenv("SMTP_USER", "")).strip()
    smtp_password_raw = get_secret("SMTP_PASSWORD", os.getenv("SMTP_PASSWORD", "")).strip()
    smtp_password = smtp_password_raw.replace(" ", "").replace('"', '').replace("'", "")
    from_email = get_secret("SMTP_FROM_EMAIL", os.getenv("SMTP_FROM_EMAIL", smtp_user or "noreply@jts-powertool.com")).strip()
    from_name = get_secret("SMTP_FROM_NAME", os.getenv("SMTP_FROM_NAME", "JTS Powertool")).strip()

    display_name = recipient_name.strip() if recipient_name else "there"

    # Dev/Mock fallback if credentials are unset
    if not smtp_user or not smtp_password:
        logger.warning(
            f"[EMAIL_SERVICE] SMTP credentials not set. Simulating password setup email to {to_email}.\n"
            f"🔗 Setup URL: {setup_url}"
        )
        return {
            "success": True,
            "mode": "mock",
            "url": setup_url,
            "message": f"SMTP not configured. Generated setup link logged for {to_email}."
        }

    # Prepare email content
    subject = f"Welcome to {workspace_name} - Set Your Password"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>{subject}</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 0; color: #334155; }}
        .container {{ max-width: 580px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
        .header {{ background-color: #4f46e5; padding: 28px 32px; text-align: center; }}
        .header h1 {{ margin: 0; color: #ffffff; font-size: 22px; font-weight: 700; letter-spacing: -0.02em; }}
        .body {{ padding: 32px; }}
        .body p {{ margin: 0 0 16px; line-height: 1.6; font-size: 15px; }}
        .button-wrap {{ text-align: center; margin: 32px 0; }}
        .btn {{ display: inline-block; background-color: #4f46e5; color: #ffffff !important; text-decoration: none; padding: 12px 28px; border-radius: 8px; font-weight: 600; font-size: 15px; }}
        .footer {{ padding: 20px 32px; background-color: #f1f5f9; border-top: 1px solid #e2e8f0; font-size: 13px; color: #64748b; text-align: center; }}
        .link-text {{ word-break: break-all; color: #4f46e5; }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <h1>{workspace_name}</h1>
        </div>
        <div class="body">
          <p>Hello <strong>{display_name}</strong>,</p>
          <p>An account has been created for you on the <strong>{workspace_name}</strong> platform.</p>
          <p>Please click the button below to set up your password and access your dashboard. This link is valid for <strong>24 hours</strong>.</p>
          <div class="button-wrap">
            <a href="{setup_url}" class="btn" target="_blank">Set Your Password</a>
          </div>
          <p style="font-size: 13px; color: #64748b;">If the button above does not work, copy and paste this link into your browser:</p>
          <p style="font-size: 13px;"><a href="{setup_url}" class="link-text">{setup_url}</a></p>
          <p style="font-size: 13px; color: #94a3b8; margin-top: 24px;">If you did not request this invitation, you can safely ignore this email.</p>
        </div>
        <div class="footer">
          &copy; {workspace_name}. All rights reserved.
        </div>
      </div>
    </body>
    </html>
    """

    plain_text = f"""Hello {display_name},

An account has been created for you on the {workspace_name} platform.

Please use the following link to set your password (valid for 24 hours):
{setup_url}

If you did not request this invitation, you can safely ignore this email.
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = to_email

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=12)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=12)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(smtp_user, smtp_password)
        server.sendmail(from_email, [to_email], msg.as_string())
        server.quit()

        logger.info(f"[EMAIL_SERVICE] Successfully sent password setup email to {to_email}")
        return {
            "success": True,
            "mode": "live",
            "message": f"Password setup email successfully sent to {to_email}"
        }
    except Exception as e:
        logger.error(f"[EMAIL_SERVICE] Failed to send email to {to_email} via SMTP: {e}", exc_info=True)
        return {
            "success": False,
            "mode": "error",
            "error": f"Failed to send email: {str(e)}"
        }


def send_simple_email(to_emails, subject: str, text: str) -> dict:
    """Plain notification email to one or more people (budget alerts, ...). Mock mode (logged only) when SMTP isn't configured."""
    recipients = [e.strip() for e in (to_emails or []) if e and e.strip()]
    if not recipients:
        return {"success": False, "error": "No recipients."}
    smtp_host = get_secret("SMTP_HOST", os.getenv("SMTP_HOST", "smtp.gmail.com")).strip()
    try:
        smtp_port = int(get_secret("SMTP_PORT", os.getenv("SMTP_PORT", "587")).strip())
    except ValueError:
        smtp_port = 587
    smtp_user = get_secret("SMTP_USER", os.getenv("SMTP_USER", "")).strip()
    smtp_password = get_secret("SMTP_PASSWORD", os.getenv("SMTP_PASSWORD", "")).strip().replace(" ", "").replace('"', "").replace("'", "")
    from_email = get_secret("SMTP_FROM_EMAIL", os.getenv("SMTP_FROM_EMAIL", smtp_user or "noreply@jts-powertool.com")).strip()
    from_name = get_secret("SMTP_FROM_NAME", os.getenv("SMTP_FROM_NAME", "JTS Powertool")).strip()

    if not smtp_user or not smtp_password:
        logger.warning(f"[EMAIL_SERVICE] SMTP not set. Simulating email '{subject}' to {recipients}: {text}")
        return {"success": True, "mode": "mock"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=12)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=12)
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(smtp_user, smtp_password)
        server.sendmail(from_email, recipients, msg.as_string())
        server.quit()
        logger.info(f"[EMAIL_SERVICE] Sent '{subject}' to {len(recipients)} recipient(s)")
        return {"success": True, "mode": "live"}
    except Exception as e:
        logger.error(f"[EMAIL_SERVICE] Could not send '{subject}': {type(e).__name__}")
        return {"success": False, "error": "Email could not be sent."}
