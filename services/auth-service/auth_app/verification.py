import hashlib
import secrets
from enum import Enum

from redis.asyncio import Redis

from auth_app.settings import AuthSettings, get_settings


class TokenPurpose(Enum):
    EMAIL_VERIFY = "emailverify"
    PASSWORD_RESET = "pwdreset"

    def ttl_seconds(self, settings: AuthSettings) -> int:
        minutes = {
            TokenPurpose.EMAIL_VERIFY: settings.verification_token_ttl_minutes,
            TokenPurpose.PASSWORD_RESET: settings.password_reset_token_ttl_minutes,
        }[self]
        return minutes * 60

    def cooldown_seconds(self, settings: AuthSettings) -> int:
        minutes = {
            TokenPurpose.EMAIL_VERIFY: settings.verification_cooldown_minutes,
            TokenPurpose.PASSWORD_RESET: settings.password_reset_cooldown_minutes,
        }[self]
        return minutes * 60


def generate_verification_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash the verification token using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_and_store_token(
    redis: Redis, user_id: int, *, purpose: TokenPurpose = TokenPurpose.EMAIL_VERIFY
) -> str:
    token = generate_verification_token()
    redis_key = f"{purpose.value}:{hash_token(token)}"
    await redis.set(redis_key, str(user_id), ex=purpose.ttl_seconds(get_settings()))
    return token


async def consume_token(
    redis: Redis, raw_token: str, *, purpose: TokenPurpose = TokenPurpose.EMAIL_VERIFY
) -> int | None:
    redis_key = f"{purpose.value}:{hash_token(raw_token)}"

    async with redis.pipeline(transaction=True) as pipe:
        pipe.get(redis_key)
        pipe.delete(redis_key)
        results = await pipe.execute()

    user_id_raw = results[0]
    if not user_id_raw:
        return None

    return int(user_id_raw)


async def is_on_cooldown(
    redis: Redis, email: str, *, purpose: TokenPurpose = TokenPurpose.EMAIL_VERIFY
) -> bool:
    clean_email = email.lower().strip()
    cooldown_key = f"cooldown:{purpose.value}:{clean_email}"
    ttl_seconds = purpose.cooldown_seconds(get_settings())

    was_set = await redis.set(cooldown_key, "1", ex=ttl_seconds, nx=True)
    return not was_set
