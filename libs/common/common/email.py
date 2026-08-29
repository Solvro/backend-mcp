import asyncio
import logging
import random
from email.message import EmailMessage
from functools import lru_cache
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

from common.errors import EmailSendError
from common.settings import CommonSettings

logger = logging.getLogger(__name__)



@runtime_checkable
class EmailSender(Protocol):
    async def send(
        self,
        to: List[str],
        subject: str,
        plain: str,
        html: Optional[str] = None,
    ) -> None:
        ...


class NoopEmailSender:
    """Async no-op sender for tests. Does not send or log addresses."""

    async def send(
        self,
        to: List[str],
        subject: str,
        plain: str,
        html: Optional[str] = None,
    ) -> None:
        return None


class ConsoleEmailSender:
    """Console sender for local debugging. Does not print addresses."""

    async def send(
        self,
        to: List[str],
        subject: str,
        plain: str,
        html: Optional[str] = None,
    ) -> None:
        logger.info("email: subject=%s sent (console)", subject)


def _render_template(name: str, context: Optional[Dict] = None) -> Tuple[str, Optional[str]]:
    try:
        env = _get_jinja_env()
    except RuntimeError as e:
        logger.debug("skipping template rendering: %s", e)
        return "", None

    ctx = context or {}
    txt: Optional[str] = None
    html: Optional[str] = None
    try:
        txt = env.get_template(f"email/{name}.txt").render(**ctx)
    except Exception as e:
        logger.warning("failed to render plaintext template email/%s.txt: %s", name, e)
    try:
        html = env.get_template(f"email/{name}.html").render(**ctx)
    except Exception as e:
        logger.warning("failed to render html template email/%s.html: %s", name, e)
    return txt or "", html


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
    settings: Optional[CommonSettings] = None,
) -> Any:
    s = settings or CommonSettings()
    attempts = int(s.email_retry_attempts)
    base = float(s.email_retry_base_delay)
    max_delay = float(s.email_retry_max_delay)
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            if attempt == attempts:
                logger.exception("email send failed after retries")
                raise EmailSendError() from e
            delay = min(max_delay, base * (2 ** (attempt - 1)) + random.uniform(0, base))
            await asyncio.sleep(delay)


class SMTPEmailSender:
    def __init__(self, settings: Optional[CommonSettings] = None):
        self.settings = settings or CommonSettings()

    async def send(
        self,
        to: List[str],
        subject: str,
        plain: str,
        html: Optional[str] = None,
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
            async with smtp:
                if self.settings.smtp_starttls:
                    await smtp.starttls()
                if self.settings.smtp_user and self.settings.smtp_pass:
                    await smtp.login(self.settings.smtp_user, self.settings.smtp_pass)
                await smtp.send_message(msg)

        await _retry(_send, settings=self.settings)


def get_email_sender(settings: Optional[CommonSettings] = None) -> EmailSender:
    s = settings or CommonSettings()
    provider = (s.smtp_provider or "smtp").lower()
    if provider == "noop":
        return NoopEmailSender()
    if provider == "console":
        return ConsoleEmailSender()
    return SMTPEmailSender(s)
