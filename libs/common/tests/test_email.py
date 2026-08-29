from unittest.mock import AsyncMock, patch

import pytest
from common.email import (
    ConsoleEmailSender,
    EmailSendError,
    SMTPEmailSender,
    _render_template,
    _retry,
    get_email_sender,
)
from common.settings import CommonSettings


def test_get_email_sender_factory_variants():
    s_console = CommonSettings(smtp_provider="console")
    assert isinstance(get_email_sender(s_console), ConsoleEmailSender)

    s_smtp = CommonSettings(smtp_provider="smtp")
    assert isinstance(get_email_sender(s_smtp), SMTPEmailSender)


def test_render_template_missing():
    txt, html = _render_template("non_existent_template_name")
    assert txt == ""
    assert html is None


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
    assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)
