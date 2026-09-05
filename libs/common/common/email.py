import asyncio
import logging
import random
from dataclasses import dataclass
from email.message import EmailMessage
from functools import lru_cache
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from common.errors import EmailSendError, NonRetriableError
from common.settings import CommonSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailFailure:
    exception_type: str
    smtp_code: int | None = None

    @classmethod
    def from_exception(cls, exc: Exception) -> "EmailFailure":
        target = exc.__cause__ or exc
        code = getattr(target, "code", None)
        return cls(
            exception_type=type(target).__name__,
            smtp_code=code if isinstance(code, int) else None,
        )


@runtime_checkable
class EmailSender(Protocol):
    async def send(
        self,
        to: list[str],
        subject: str,
        plain: str,
        html: str | None = None,
    ) -> None:
        ...


class NoopEmailSender:
    """Async no-op sender for tests. Does not send or log addresses."""

    async def send(
        self,
        to: list[str],
        subject: str,
        plain: str,
        html: str | None = None,
    ) -> None:
        return None


class ConsoleEmailSender:
    """Console sender for local debugging. Does not print addresses."""

    async def send(
        self,
        to: list[str],
        subject: str,
        plain: str,
        html: str | None = None,
    ) -> None:
        logger.info("email: subject=%s sent (console)", subject)


def render_template(name: str, context: dict[str, Any] | None = None) -> tuple[str, str | None]:
    """Renders required plaintext (.txt) and optional (.html) templates."""
    try:
        from jinja2.exceptions import TemplateError, TemplateNotFound
    except ImportError as e:
        raise RuntimeError("jinja2 is required for template rendering") from e

    env = _get_jinja_env()
    ctx = context or {}

    try:
        txt = env.get_template(f"email/{name}.txt").render(**ctx)
    except TemplateNotFound:
        logger.error("missing required email template: email/%s.txt", name)
        raise NonRetriableError() from None
    except TemplateError as e:
        logger.error("failed to render plaintext template email/%s.txt: %s", name, e)
        raise NonRetriableError() from None

    html: str | None = None
    try:
        html = env.get_template(f"email/{name}.html").render(**ctx)
    except TemplateNotFound:
        pass
    except TemplateError as e:
        logger.error("failed to render html template email/%s.html: %s", name, e)
        raise NonRetriableError() from None

    return txt, html


@lru_cache(maxsize=1)
def _get_jinja_env() -> Any:
    try:
        from jinja2 import Environment, PackageLoader, select_autoescape
    except ImportError as e:
        raise RuntimeError("jinja2 is required for template rendering") from e

    return Environment(
        loader=PackageLoader("common", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )


async def _retry(
    coro_factory: Callable[[], Awaitable[Any]],
    settings: CommonSettings | None = None,
    log_extra: dict[str, Any] | None = None,
) -> Any:
    s = settings or CommonSettings()
    attempts = int(s.email_retry_attempts)
    base = float(s.email_retry_base_delay)
    max_delay = float(s.email_retry_max_delay)
    if attempts <= 0:
        raise ValueError("email_retry_attempts must be greater than 0")
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            if isinstance(e, NonRetriableError) or attempt == attempts:
                failure = EmailFailure.from_exception(e.__cause__ or e)
                logger.error(
                    "email send failed: type=%s smtp_code=%s",
                    failure.exception_type,
                    failure.smtp_code,
                    extra=(log_extra or {}),
                )
                raise EmailSendError() from None
            delay = min(max_delay, base * (2 ** (attempt - 1)) + random.uniform(0, base))
            await asyncio.sleep(delay)


class SMTPEmailSender:
    def __init__(self, settings: CommonSettings | None = None):
        self.settings = settings or CommonSettings()

    async def send(
        self,
        to: list[str],
        subject: str,
        plain: str,
        html: str | None = None,
    ) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.settings.smtp_from
        msg["To"] = ", ".join(to)
        msg.set_content(plain)
        if html:
            msg.add_alternative(html, subtype="html")

        async def _send() -> None:
            try:
                import aiosmtplib
            except ImportError as e:
                raise RuntimeError("aiosmtplib is required for SMTPEmailSender") from e

            smtp = aiosmtplib.SMTP(
                hostname=self.settings.smtp_host,
                port=self.settings.smtp_port,
                timeout=self.settings.smtp_timeout,
            )
            try:
                async with smtp:
                    if self.settings.smtp_starttls:
                        await smtp.starttls()
                    if self.settings.smtp_user and self.settings.smtp_pass:
                        await smtp.login(self.settings.smtp_user, self.settings.smtp_pass)
                    await smtp.send_message(msg)

            except (
                aiosmtplib.SMTPAuthenticationError,
                aiosmtplib.SMTPRecipientsRefused,
                aiosmtplib.SMTPRecipientRefused,
                aiosmtplib.SMTPSenderRefused,
                aiosmtplib.SMTPDataError,
                aiosmtplib.SMTPHeloError,
                aiosmtplib.SMTPNotSupported,
                ValueError,
            ):
                raise NonRetriableError() from None
            except aiosmtplib.SMTPResponseException as e:
                try:
                    code = int(getattr(e, "code", 0))
                except Exception:
                    code = 0
                if 500 <= code < 600:
                    raise NonRetriableError() from e
                raise

        await _retry(_send, settings=self.settings, log_extra={"subject": subject})


def get_email_sender(settings: CommonSettings | None = None) -> EmailSender:
    s = settings or CommonSettings()
    provider = (s.smtp_provider or "smtp").lower()
    if provider == "noop":
        return NoopEmailSender()
    if provider == "console":
        return ConsoleEmailSender()
    return SMTPEmailSender(s)


async def send_template_email(
    sender: EmailSender,
    to: list[str],
    subject: str,
    template_name: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Entry point for templated emails

    Renders the required plaintext template and optional HTML template using
    `render_template`, then sends the message via the provided `sender`.
    """
    plain, html = render_template(template_name, context)
    await sender.send(to=to, subject=subject, plain=plain, html=html)
