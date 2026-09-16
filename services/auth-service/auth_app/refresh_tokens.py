from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from auth_app.models import RefreshToken
from auth_app.verification import hash_token


def store_refresh_token(
    db: AsyncSession,
    *,
    raw_token: str,
    jti: str,
    user_id: int,
    expires_at: datetime,
    family_id: str | None = None,
) -> RefreshToken:
    row = RefreshToken(
        jti=jti,
        family_id=family_id or jti,
        user_id=user_id,
        token_hash=hash_token(raw_token),
        expires_at=expires_at,
    )
    db.add(row)
    return row


async def revoke_family(db: AsyncSession, family_id: str) -> None:
    """Revoke every token in a rotation chain (reuse detected, or logout)."""
    await db.execute(
        update(RefreshToken).where(RefreshToken.family_id == family_id).values(revoked=True)
    )


async def revoke_all_for_user(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked=True)
    )
