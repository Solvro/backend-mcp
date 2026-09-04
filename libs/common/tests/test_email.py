import sys
import types
from unittest.mock import AsyncMock, patch

import pytest
from common.email import (
    ConsoleEmailSender,
    EmailSendError,
    NoopEmailSender,
    SMTPEmailSender,
    _retry,
    get_email_sender,
    render_template,
    send_template_email,
)
from common.settings import CommonSettings


@pytest.fixture
def mailpit_settings() -> CommonSettings:
    return CommonSettings(
        smtp_provider="smtp",
        smtp_host="localhost",
        smtp_port=1025,
        smtp_from="no-reply@example.com",
        smtp_starttls=False,
        smtp_timeout=5,
    )


@pytest.mark.unit
def test_get_email_sender_factory_variants():
    s_console = CommonSettings(smtp_provider="console")
    assert isinstance(get_email_sender(s_console), ConsoleEmailSender)

    s_smtp = CommonSettings(smtp_provider="smtp")
    assert isinstance(get_email_sender(s_smtp), SMTPEmailSender)


@pytest.mark.unit
def test_render_template_missing():
    with pytest.raises(Exception):
        render_template("non_existent_template_name")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_noop_sender_send_and_send_template(monkeypatch):
    sender = NoopEmailSender()
    await sender.send(["a@b.com"], "subj", "plain")

    def fake_render(name, ctx=None):
        return ("plain", "<p>html</p>")

    monkeypatch.setattr("common.email.render_template", fake_render)
    mock_sender = AsyncMock()
    await send_template_email(mock_sender, ["a@b.com"], "subj", "template_name")
    mock_sender.send.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_smtp_sender_happy_path(monkeypatch):
    sent = {"called": False}

    class DummySMTP:
        def __init__(self, hostname=None, port=None, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def starttls(self):
            return None

        async def login(self, user, pwd):
            return None

        async def send_message(self, msg):
            sent["called"] = True

    mod = types.ModuleType("aiosmtplib")
    mod.SMTP = DummySMTP
    monkeypatch.setitem(sys.modules, "aiosmtplib", mod)

    settings = CommonSettings(
        smtp_provider="smtp",
        smtp_host="localhost",
        smtp_port=1025,
        smtp_starttls=False,
        smtp_timeout=1,
    )
    sender = SMTPEmailSender(settings)
    await sender.send(["test@example.com"], "subj", "plain")
    assert sent["called"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_smtp_sender_against_mailpit(mailpit_settings):
    sender = SMTPEmailSender(mailpit_settings)
    await sender.send(["test@example.com"], "integration test", "plain body")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_rejects_non_positive_attempts():
    mock_coro = AsyncMock(side_effect=ConnectionRefusedError("SMTP server down"))
    settings = CommonSettings(
        email_retry_attempts=0,
        email_retry_base_delay=0.01,
        email_retry_max_delay=0.05,
    )

    with pytest.raises(ValueError, match="greater than 0"):
        await _retry(mock_coro, settings=settings)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_exhausts_and_chains_exception():
    mock_coro = AsyncMock(side_effect=ConnectionRefusedError("SMTP server down"))
    settings = CommonSettings(
        email_retry_attempts=2,
        email_retry_base_delay=0.01,
        email_retry_max_delay=0.05,
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(EmailSendError) as exc_info:
            await _retry(mock_coro, settings=settings)

    assert mock_coro.call_count == 2
    assert exc_info.value.__cause__ is None
