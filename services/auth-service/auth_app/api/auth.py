import time
from datetime import datetime, timezone

from auth_app.models import USER_ROLE, RefreshToken, Role, User
from auth_app.refresh_tokens import revoke_all_for_user, revoke_family, store_refresh_token
from auth_app.schemas import (
    ForgotPasswordSchema,
    LoginSchema,
    LogoutSchema,
    RefreshSchema,
    RegisterSchema,
    ResendVerificationEmailSchema,
    ResetPasswordSchema,
    UserResponseSchema,
)
from auth_app.security import create_access_token, create_refresh_token, get_password_manager
from auth_app.settings import get_settings
from auth_app.verification import (
    TokenPurpose,
    consume_token,
    create_and_store_token,
    hash_token,
    is_on_cooldown,
)
from common.auth import REFRESH_TOKEN_TYP, decode_access_token, decode_token
from common.db import get_session
from common.email import get_email_sender, send_template_email
from common.errors import AuthError, EmailSendError, NonRetriableError
from common.rate_limit import rate_limit
from common.redis import redis_dependency, revoke_token, revoke_user_tokens
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/auth", tags=["auth"])

settings = get_settings()

register_limiter = rate_limit(
    "auth:register",
    settings=settings,
    limit=settings.rate_limit_register,
    window_seconds=settings.rate_limit_window_seconds,
)

login_limiter = rate_limit(
    "auth:login",
    settings=settings,
    limit=settings.rate_limit_login,
    window_seconds=settings.rate_limit_window_seconds,
)

resend_limiter = rate_limit(
    "auth:resend",
    settings=settings,
    limit=settings.rate_limit_resend,
    window_seconds=settings.rate_limit_window_seconds,
)

refresh_limiter = rate_limit(
    "auth:refresh",
    settings=settings,
    limit=settings.rate_limit_refresh,
    window_seconds=settings.rate_limit_window_seconds,
)

forgot_password_limiter = rate_limit(
    "auth:forgot-password",
    settings=settings,
    limit=settings.rate_limit_forgot_password,
    window_seconds=settings.rate_limit_window_seconds,
)

reset_password_limiter = rate_limit(
    "auth:reset-password",
    settings=settings,
    limit=settings.rate_limit_reset_password,
    window_seconds=settings.rate_limit_window_seconds,
)


def bearer_claims(request: Request) -> dict:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="authentication_required")
    try:
        return decode_access_token(token.strip(), get_settings())
    except AuthError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_access_token")


async def send_password_reset_email(email: str, link: str, ttl_minutes: int) -> None:
    try:
        await send_template_email(
            sender=get_email_sender(),
            to=[email],
            subject="Password reset",
            template_name="password_reset",
            context={"reset_url": link, "ttl_minutes": ttl_minutes},
        )
    except Exception:  # noqa: BLE001 - never surface delivery problems to the requester
        pass


async def send_verification_email(email: str, username: str, link: str) -> None:
    try:
        sender = get_email_sender()
        await send_template_email(
            sender=sender,
            to=[email],
            subject="Account verification",
            template_name="verification",
            context={"username": username, "verify_url": link},
        )
    except (EmailSendError, NonRetriableError):
        pass
    except Exception:
        pass


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserResponseSchema,
    dependencies=[Depends(register_limiter)],
)
async def register(
    data: RegisterSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))

    user_role = (await db.execute(select(Role).where(Role.name == USER_ROLE))).scalar_one_or_none()
    if user_role is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="roles_not_seeded"
        )

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
    background_tasks.add_task(send_verification_email, user.email, user.username, verify_link)

    return UserResponseSchema(
        id=user.id,
        username=user.username,
        email=user.email,
        email_verified=user.email_verified,
        is_active=user.is_active,
        roles=[r.name for r in user.roles],
    )


@router.post("/login", dependencies=[Depends(login_limiter)])
async def login(data: LoginSchema, db: AsyncSession = Depends(get_session)):
    pm = get_password_manager()

    stmt = select(User).options(selectinload(User.roles)).where(User.email == data.email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    target_hash = user.password_hash if user else pm.DUMMY_HASH
    is_password_correct = pm.verify_password(data.password, target_hash)

    if not user or not is_password_correct:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="email_unverified",
        )

    if pm.needs_rehash(user.password_hash):
        user.password_hash = pm.hash_password(data.password)

    access_token = create_access_token(user)
    refresh_token, refresh_jti, expires_at = create_refresh_token(user)
    store_refresh_token(
        db, raw_token=refresh_token, jti=refresh_jti, user_id=user.id, expires_at=expires_at
    )
    await db.commit()

    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@router.post("/refresh", dependencies=[Depends(refresh_limiter)])
async def refresh(data: RefreshSchema, db: AsyncSession = Depends(get_session)):
    """Rotate a refresh token: revoke the presented one, issue a new access + refresh pair."""
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_refresh_token")

    try:
        claims = decode_token(data.refresh_token, get_settings())
    except AuthError:
        raise invalid
    if claims.get("typ") != REFRESH_TOKEN_TYP:
        raise invalid

    stmt = (
        select(RefreshToken)
        .options(selectinload(RefreshToken.user).selectinload(User.roles))
        .where(RefreshToken.jti == claims.get("jti"))
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or row.token_hash != hash_token(data.refresh_token):
        raise invalid

    if row.revoked:
        await revoke_family(db, row.family_id)
        await db.commit()
        raise invalid

    user = row.user
    if not user.is_active:
        raise invalid

    row.revoked = True
    access_token = create_access_token(user)
    refresh_token, refresh_jti, expires_at = create_refresh_token(user)
    store_refresh_token(
        db,
        raw_token=refresh_token,
        jti=refresh_jti,
        user_id=user.id,
        expires_at=expires_at,
        family_id=row.family_id,
    )
    await db.commit()

    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@router.get("/verify")
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    user_id = await consume_token(redis, token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired_token"
        )

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired_token"
        )

    user.email_verified = True
    user.verified_at = datetime.now(timezone.utc)
    await db.commit()

    return {"message": "Email verified successfully."}


@router.post("/resend-verification", dependencies=[Depends(resend_limiter)])
async def resend_verification(
    data: ResendVerificationEmailSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    generic_response = {
        "message": "If the email is registered and unverified, a verification link has been sent."
    }

    clean_email = data.email.lower().strip()

    stmt = select(User).where(User.email == clean_email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user and not user.email_verified:
        if not await is_on_cooldown(redis, user.email):
            raw_token = await create_and_store_token(redis, user.id)
            settings = get_settings()
            verify_link = f"{settings.frontend_url.rstrip('/')}/auth/verify?token={raw_token}"
            background_tasks.add_task(
                send_verification_email, user.email, user.username, verify_link
            )

    return generic_response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: LogoutSchema,
    claims: dict = Depends(bearer_claims),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Denylist the access token until it would expire anyway; revoke the refresh family."""
    settings = get_settings()

    ttl = int(claims["exp"]) + settings.jwt_leeway_seconds - int(time.time())
    if claims.get("jti") and ttl > 0:
        await revoke_token(str(claims["jti"]), ttl_seconds=ttl, settings=settings)

    if data.refresh_token:
        await _revoke_own_refresh_family(db, data.refresh_token, owner_id=claims["sub"])
        await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _revoke_own_refresh_family(db: AsyncSession, raw_token: str, *, owner_id: str) -> None:
    try:
        claims = decode_token(raw_token, get_settings())
    except AuthError:
        return
    if claims.get("typ") != REFRESH_TOKEN_TYP:
        return

    stmt = select(RefreshToken).where(RefreshToken.jti == claims.get("jti"))
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or row.token_hash != hash_token(raw_token) or str(row.user_id) != owner_id:
        return

    await revoke_family(db, row.family_id)


@router.post("/forgot-password", dependencies=[Depends(forgot_password_limiter)])
async def forgot_password(
    data: ForgotPasswordSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    generic_response = {"message": "If the email is registered, a reset link has been sent."}
    clean_email = data.email.lower().strip()

    user = (await db.execute(select(User).where(User.email == clean_email))).scalar_one_or_none()
    if user is None or not user.is_active:
        return generic_response
    if await is_on_cooldown(redis, clean_email, purpose=TokenPurpose.PASSWORD_RESET):
        return generic_response

    settings = get_settings()
    raw_token = await create_and_store_token(redis, user.id, purpose=TokenPurpose.PASSWORD_RESET)
    reset_url = f"{settings.frontend_url.rstrip('/')}/auth/reset-password?token={raw_token}"
    background_tasks.add_task(
        send_password_reset_email,
        user.email,
        reset_url,
        settings.password_reset_token_ttl_minutes,
    )
    return generic_response


@router.post("/reset-password", dependencies=[Depends(reset_password_limiter)])
async def reset_password(
    data: ResetPasswordSchema,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(redis_dependency),
):
    settings = get_settings()
    pm = get_password_manager()
    try:
        new_hash = pm.hash_password(data.new_password)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))

    user_id = await consume_token(redis, data.token, purpose=TokenPurpose.PASSWORD_RESET)
    user = (
        (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user_id is not None
        else None
    )
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired_token"
        )

    user.password_hash = new_hash
    if not user.email_verified:  # completing the reset proves control of the inbox
        user.email_verified = True
        user.verified_at = datetime.now(timezone.utc)
    await revoke_all_for_user(db, user.id)
    await db.commit()

    await revoke_user_tokens(
        str(user.id),
        ttl_seconds=settings.access_token_expire_minutes * 60 + settings.jwt_leeway_seconds,
        settings=settings,
    )

    return {"message": "Password has been reset."}
