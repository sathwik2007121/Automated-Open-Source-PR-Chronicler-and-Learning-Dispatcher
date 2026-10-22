import asyncio
import smtplib
from email.message import EmailMessage
from typing import Optional, Tuple

from app.config import settings
from app.models import PRReflection
from app.logger import logger, log_smtp_status


def generate_plain_text_body(
    repo_name: str,
    pr_number: int,
    pr_title: str,
    pr_url: Optional[str],
    reflection: PRReflection,
) -> str:
    """Generates a clean, structured plain-text email body."""
    learnings_text = "\n".join([f"  {idx+1}. {item}" for idx, item in enumerate(reflection.key_learnings)])
    
    return f"""======================================================================
OPEN-SOURCE PR CHRONICLER & PORTFOLIO LOG
Repository: {repo_name}
Pull Request: #{pr_number} - {pr_title}
URL: {pr_url or 'N/A'}
======================================================================

1. ISSUE DETAILS
--------------------------------------------------
{reflection.issue_details}


2. EXECUTION SUMMARY
--------------------------------------------------
{reflection.execution_summary}


3. KEY LEARNINGS
--------------------------------------------------
{learnings_text}

======================================================================
Generated automatically by Automated Open-Source PR Chronicler (GenAI 101)
======================================================================
"""


def generate_html_body(
    repo_name: str,
    pr_number: int,
    pr_title: str,
    pr_url: Optional[str],
    reflection: PRReflection,
) -> str:
    """Generates an aesthetic, responsive HTML email body with separated sections."""
    learnings_items = "".join([
        f"""
        <li style="margin-bottom: 10px; line-height: 1.5; color: #1e293b;">
            <strong style="color: #2563eb;">Takeaway {idx+1}:</strong> {item}
        </li>
        """
        for idx, item in enumerate(reflection.key_learnings)
    ])

    pr_link_html = f'<a href="{pr_url}" style="color: #2563eb; text-decoration: none;">View PR #{pr_number} on GitHub</a>' if pr_url else f"PR #{pr_number}"

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>[OS-Log] PR Merged: {repo_name} (#{pr_number})</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0f172a;">
    <div style="max-width: 680px; margin: 30px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1); border: 1px solid #e2e8f0;">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #ffffff; padding: 28px 32px;">
            <span style="display: inline-block; background-color: #10b981; color: #ffffff; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; padding: 4px 10px; border-radius: 9999px; margin-bottom: 12px;">Merged & Chronicled</span>
            <h1 style="margin: 0 0 6px 0; font-size: 22px; font-weight: 700; line-height: 1.3;">{repo_name}</h1>
            <p style="margin: 0; font-size: 15px; color: #cbd5e1;">#{pr_number}: {pr_title}</p>
            <p style="margin: 8px 0 0 0; font-size: 13px;">{pr_link_html}</p>
        </div>

        <div style="padding: 32px;">

            <!-- 1. Issue Details -->
            <div style="margin-bottom: 28px; background-color: #f8fafc; border-left: 4px solid #64748b; padding: 18px 20px; border-radius: 0 8px 8px 0;">
                <h2 style="margin: 0 0 8px 0; font-size: 16px; font-weight: 700; color: #334155; text-transform: uppercase; letter-spacing: 0.04em;">
                    1. Issue Details
                </h2>
                <p style="margin: 0; font-size: 14px; line-height: 1.6; color: #1e293b; white-space: pre-wrap;">{reflection.issue_details}</p>
            </div>

            <!-- 2. Execution Summary -->
            <div style="margin-bottom: 28px; background-color: #f0fdf4; border-left: 4px solid #16a34a; padding: 18px 20px; border-radius: 0 8px 8px 0;">
                <h2 style="margin: 0 0 8px 0; font-size: 16px; font-weight: 700; color: #15803d; text-transform: uppercase; letter-spacing: 0.04em;">
                    2. Execution Summary
                </h2>
                <p style="margin: 0; font-size: 14px; line-height: 1.6; color: #1e293b; white-space: pre-wrap;">{reflection.execution_summary}</p>
            </div>

            <!-- 3. Key Learnings -->
            <div style="margin-bottom: 16px; background-color: #eff6ff; border-left: 4px solid #2563eb; padding: 18px 20px; border-radius: 0 8px 8px 0;">
                <h2 style="margin: 0 0 12px 0; font-size: 16px; font-weight: 700; color: #1d4ed8; text-transform: uppercase; letter-spacing: 0.04em;">
                    3. Key Learnings
                </h2>
                <ul style="margin: 0; padding-left: 20px;">
                    {learnings_items}
                </ul>
            </div>

        </div>

        <!-- Footer -->
        <div style="background-color: #f8fafc; padding: 16px 32px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b; text-align: center;">
            Automated Open-Source PR Chronicler & Learning Dispatcher | GenAI 101
        </div>

    </div>
</body>
</html>
"""


def _send_smtp_sync(
    subject: str,
    plain_body: str,
    html_body: str,
    to_email: str,
) -> Tuple[bool, str]:
    """
    Synchronous SMTP transmission using standard smtplib and EmailMessage.
    Runs inside a worker thread via asyncio.to_thread to avoid blocking async endpoints.
    """
    sender_email = settings.SMTP_EMAIL
    app_password = settings.SMTP_APP_PASSWORD
    smtp_host = settings.SMTP_HOST
    smtp_port = settings.SMTP_PORT

    if not sender_email or not app_password:
        msg = "SMTP_EMAIL or SMTP_APP_PASSWORD is not set in environment. Skipping live email dispatch."
        log_smtp_status(success=False, recipient=to_email, message=msg)
        return False, msg

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender_email
    message["To"] = to_email

    # Plain text fallback
    message.set_content(plain_body)
    # HTML alternative
    message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            if server.has_extn("STARTTLS"):
                server.starttls()
                server.ehlo()
            server.login(sender_email, app_password)
            server.send_message(message)

        log_smtp_status(
            success=True,
            recipient=to_email,
            message=f"Sent via {smtp_host}:{smtp_port} with subject '{subject}'"
        )
        return True, "Email successfully transmitted via SMTP."

    except smtplib.SMTPException as exc:
        err_msg = f"SMTP error occurred: {type(exc).__name__}: {exc}"
        log_smtp_status(success=False, recipient=to_email, message=err_msg, exc=exc)
        return False, err_msg
    except Exception as exc:
        err_msg = f"Network or unexpected error occurred: {type(exc).__name__}: {exc}"
        log_smtp_status(success=False, recipient=to_email, message=err_msg, exc=exc)
        return False, err_msg


async def dispatch_reflection_email(
    repo_name: str,
    pr_number: int,
    pr_title: str,
    pr_url: Optional[str],
    reflection: PRReflection,
) -> Tuple[bool, str]:
    """
    Asynchronously dispatches the formatted PR reflection email to the student's inbox.
    Subject format: [OS-Log] PR Merged: <Repository Name> (#<PR Number>)
    """
    subject = f"[OS-Log] PR Merged: {repo_name} (#{pr_number})"
    recipient = settings.RECIPIENT_EMAIL

    plain_body = generate_plain_text_body(
        repo_name=repo_name,
        pr_number=pr_number,
        pr_title=pr_title,
        pr_url=pr_url,
        reflection=reflection,
    )

    html_body = generate_html_body(
        repo_name=repo_name,
        pr_number=pr_number,
        pr_title=pr_title,
        pr_url=pr_url,
        reflection=reflection,
    )

    # Offload blocking network I/O to a background worker thread
    return await asyncio.to_thread(
        _send_smtp_sync,
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        to_email=recipient,
    )
