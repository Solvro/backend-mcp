from email.message import EmailMessage

import aiosmtplib

from auth_app.settings import get_settings


async def send_verification_email(to_email: str, link: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to_email
    message["Subject"] = "Verify your email address"
    message.set_content(f"Please verify your email by clicking the following link: {link}")

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
    )
