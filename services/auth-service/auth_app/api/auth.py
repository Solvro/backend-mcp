from datetime import datetime, timezone

from auth_app.email import send_verification_email
from auth_app.models import Role, User
from auth_app.schemas import (
    LoginSchema,
    RegisterSchema,
    ResendVerificationEmailSchema,
    UserResponseSchema,
)
from auth_app.security import create_access_token, get_password_manager
from auth_app.settings import get_settings
from auth_app.verification import consume_token, create_and_store_token, is_on_cooldown
from common.db import get_session
from common.redis import get_redis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/auth", tags=["auth"])


async def send_verification_email_safe(email: str, link: str) -> None:
    try:
        await send_verification_email(email, link)
    except Exception:
        pass


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserResponseSchema,
)
async def register(
    data: RegisterSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    stmt = select(User).where((User.email == data.email) | (User.username == data.username))
    existing_user = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email_or_username_already_registered",
        )

    pm = get_password_manager()
    try:
        hashed_password = pm.hash_password(data.password)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))

    stmt_role = select(Role).where(Role.name == "user")
    role_result = await db.execute(stmt_role)
    user_role = role_result.scalar_one_or_none()

    if not user_role:
        user_role = Role(name="user", description="Default user role")
        db.add(user_role)
        await db.flush()

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hashed_password,
        email_verified=False,
    )
    user.roles.append(user_role)

    db.add(user)
    await db.commit()
    await db.refresh(user, attribute_names=["roles"])

    raw_token = await create_and_store_token(redis, user.id)
    settings = get_settings()
    verify_link = f"{settings.frontend_url.rstrip('/')}/auth/verify?token={raw_token}"
    background_tasks.add_task(send_verification_email_safe, user.email, verify_link)

    return UserResponseSchema(
        id=user.id,
        username=user.username,
        email=user.email,
        email_verified=user.email_verified,
        is_active=user.is_active,
        roles=[r.name for r in user.roles],
    )


@router.post("/login")
async def login(data: LoginSchema, db: AsyncSession = Depends(get_session)):
    stmt = select(User).where(User.email == data.email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    pm = get_password_manager()
    if not user or not pm.verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="invalid_credentials")

    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="email_unverified",
        )

    if pm.needs_rehash(user.password_hash):
        user.password_hash = pm.hash_password(data.password)
        await db.commit()

    access_token = create_access_token(user.id)
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/verify")
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    user_id = await consume_token(redis, token)
    if not user_id:
        raise HTTPException(status_code=400, detail="invalid_or_expired_token")

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="invalid_or_expired_token")

    user.email_verified = True
    user.verified_at = datetime.now(timezone.utc)
    await db.commit()

    return {"message": "Email verified successfully."}


@router.post("/resend-verification")
async def resend_verification(
    data: ResendVerificationEmailSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    generic_response = {
        "message": "If the email is registered and unverified, a verification link has been sent."
    }

    if await is_on_cooldown(redis, data.email):
        return generic_response

    stmt = select(User).where(User.email == data.email.lower().strip())
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user and not user.email_verified:
        raw_token = await create_and_store_token(redis, user.id)
        settings = get_settings()
        verify_link = f"{settings.frontend_url.rstrip('/')}/auth/verify?token={raw_token}"
        background_tasks.add_task(send_verification_email, user.email, verify_link)

    return generic_response
