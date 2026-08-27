"""Email delivery for completed automation reports.

Email is deliberately downstream of report persistence and the in-app hub. A
mail-provider outage must not make a successful automation disappear.
"""

from __future__ import annotations

import logging
import smtplib
import threading
from email.message import EmailMessage

from config.settings import settings
from llm import get_llm

from ..db.models import Automation, AutomationReport, User
from ..db.session import session_scope
from ..export import WRITERS, report_document

logger = logging.getLogger(__name__)


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    return str(content)


def _summary(report: AutomationReport) -> str:
    """Ask the configured chat model for a short, plain-text email summary."""
    prompt = (
        "Write a concise email summary of this report in the report's language. "
        "Use plain text, no greeting, no markdown, and do not invent facts. "
        f"Title: {report.title}\nReport:\n{report.body}"
    )
    response = get_llm("chat", max_tokens=500).invoke(prompt)
    return _content_text(response.content).strip()


def send_report_email(report_id) -> None:
    """Send one opted-in report; all failures are contained and logged."""
    if not settings.EMAIL_SMTP_HOST or not settings.EMAIL_FROM:
        logger.info("Email delivery skipped: SMTP is not configured")
        return

    with session_scope() as store:
        report = store.get(AutomationReport, report_id)
        if report is None:
            return
        automation = store.get(Automation, report.automation_id) if report.automation_id else None
        user = store.get(User, report.user_id)
        if user is None or automation is None or not automation.email_enabled:
            return
        recipient = user.notification_email or user.email
        file_format = automation.email_format
        document = report_document(title=report.title, body=report.body, citations=report.citations)

    try:
        body = _summary(report)
    except Exception:
        logger.exception("Report email summary failed report=%s; using report body", report_id)
        body = report.body

    try:
        payload = WRITERS[file_format].write(document)
        message = EmailMessage()
        message["Subject"] = report.title
        message["From"] = settings.EMAIL_FROM
        message["To"] = recipient
        message.set_content(body)
        message.add_attachment(
            payload,
            maintype=WRITERS[file_format].media_type.split("/", 1)[0],
            subtype=WRITERS[file_format].media_type.split("/", 1)[1].split(";", 1)[0],
            filename=f"{report.title}.{WRITERS[file_format].extension}",
        )
        with smtplib.SMTP(settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT, timeout=30) as smtp:
            if settings.EMAIL_USE_TLS:
                smtp.starttls()
            if settings.EMAIL_SMTP_USERNAME:
                smtp.login(settings.EMAIL_SMTP_USERNAME, settings.EMAIL_SMTP_PASSWORD)
            smtp.send_message(message)
        logger.info("Report email sent report=%s recipient=%s format=%s", report_id, recipient, file_format)
    except Exception:
        logger.exception("Report email delivery failed report=%s", report_id)


def queue_report_email(report_id) -> None:
    threading.Thread(
        target=send_report_email,
        args=(report_id,),
        name=f"tf26-report-email-{report_id}",
        daemon=True,
    ).start()
