import pytest
from auth_app.verification import (
    TokenPurpose,
    consume_token,
    create_and_store_token,
    is_on_cooldown,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_password_reset_tokens_live_in_their_own_namespace(redis_client) -> None:
    raw = await create_and_store_token(redis_client, 7, purpose=TokenPurpose.PASSWORD_RESET)

    keys = [k async for k in redis_client.scan_iter("*")]
    assert len(keys) == 1
    assert keys[0].startswith(b"pwdreset:")
    assert raw.encode() not in keys[0]  # hashed at rest


@pytest.mark.asyncio
async def test_token_cannot_be_consumed_for_another_purpose(redis_client) -> None:
    raw = await create_and_store_token(redis_client, 7, purpose=TokenPurpose.PASSWORD_RESET)

    assert await consume_token(redis_client, raw, purpose=TokenPurpose.EMAIL_VERIFY) is None
    assert await consume_token(redis_client, raw, purpose=TokenPurpose.PASSWORD_RESET) == 7


@pytest.mark.asyncio
async def test_default_purpose_is_email_verification(redis_client) -> None:
    raw = await create_and_store_token(redis_client, 3)

    keys = [k async for k in redis_client.scan_iter("*")]
    assert keys[0].startswith(b"emailverify:")
    assert await consume_token(redis_client, raw) == 3


@pytest.mark.asyncio
async def test_cooldowns_are_per_purpose(redis_client) -> None:
    assert (
        await is_on_cooldown(redis_client, "a@x.io", purpose=TokenPurpose.PASSWORD_RESET) is False
    )
    assert await is_on_cooldown(redis_client, "a@x.io", purpose=TokenPurpose.PASSWORD_RESET) is True
    assert await is_on_cooldown(redis_client, "a@x.io") is False
