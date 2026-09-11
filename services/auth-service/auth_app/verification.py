import hashlib
import secrets

from redis.asyncio import Redis

from auth_app.settings import get_settings


def generate_verification_token() -> str:
    """Generate a secure random verification token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash the verification token using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_and_store_token(redis: Redis, user_id: int) -> str:
    """Create a verification token, hash it, and store it in Redis."""
    settings = get_settings()
    token = generate_verification_token()
    hashed_token = hash_token(token)

    redis_key = f"emailverify:{hashed_token}"
    ttl_seconds = settings.verification_token_ttl_minutes * 60
    await redis.set(redis_key, str(user_id), ex=ttl_seconds)

    return token


async def consume_token(redis: Redis, raw_token: str) -> int | None:
    """Consume a verification token and return the associated user ID if valid."""
    hashed_token = hash_token(raw_token)
    redis_key = f"emailverify:{hashed_token}"

    async with redis.pipeline(transaction=True) as pipe:
        pipe.get(redis_key)
        pipe.delete(redis_key)
        results = await pipe.execute()

    user_id_raw = results[0]
    if not user_id_raw:
        return None

    return int(user_id_raw)


async def is_on_cooldown(redis: Redis, email: str) -> bool:
    """Check if the email is currently on cooldown."""
    clean_email = email.lower().strip()
    cooldown_key = f"cooldown:emailverify:{clean_email}"
    return await redis.exists(cooldown_key) > 0


async def set_cooldown(redis: Redis, email: str) -> None:
    """Set the cooldown flag for the email."""
    settings = get_settings()
    clean_email = email.lower().strip()
    cooldown_key = f"cooldown:emailverify:{clean_email}"
    ttl_seconds = settings.verification_cooldown_minutes * 60
    await redis.set(cooldown_key, "1", ex=ttl_seconds)
