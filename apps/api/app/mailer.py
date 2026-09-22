from email.message import EmailMessage
from smtplib import SMTP

from app.config import get_settings


def send_email(*, recipient: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from:
        if settings.is_production:
            raise RuntimeError("SMTP is not configured")
        return
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
