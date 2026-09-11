from datetime import datetime, timedelta, timezone

import pytest
from auth_app.models import RefreshToken, Role, User, UserRole
from common.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.mark.unit
def test_auth_orm_tables_are_registered() -> None:
    registered = {table.name for table in Base.metadata.tables.values()}

    assert {"users", "roles", "user_roles", "refresh_tokens"}.issubset(registered)
    assert User.__tablename__ == "users"
    assert Role.__tablename__ == "roles"
    assert UserRole.__tablename__ == "user_roles"
    assert RefreshToken.__tablename__ == "refresh_tokens"


@pytest.mark.unit
def test_auth_models_can_be_created_and_persisted() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        role = Role(name="admin", description="Administrator")
        user = User(
            username="alice",
            email="alice@example.com",
            password_hash="hash-123",
            is_active=True,
            email_verified=False,
            verified_at=None,
            roles=[role],
        )
        refresh_token = RefreshToken(
            user=user,
            token_hash="token-hash",
            jti="jti-123",
            expires_at=now + timedelta(hours=1),
            revoked=False,
        )

        session.add_all([user, role, refresh_token])
        session.commit()
        session.refresh(user)
        session.refresh(role)
        session.refresh(refresh_token)

        assert user.id is not None
        assert role.id is not None
        assert refresh_token.id is not None
        assert refresh_token.user_id == user.id
        assert refresh_token.user.username == "alice"
        assert [item.name for item in user.roles] == ["admin"]

        saved_user = session.get(User, user.id)
        assert saved_user is not None
        assert saved_user.email == "alice@example.com"
        assert saved_user.email_verified is False
        assert saved_user.verified_at is None

        saved_user.email_verified = True
        saved_user.verified_at = now
        session.commit()
        session.refresh(saved_user)

        assert saved_user.email_verified is True
        assert saved_user.verified_at is not None
        verified_at_utc = saved_user.verified_at.replace(tzinfo=timezone.utc)
        assert verified_at_utc.timestamp() == pytest.approx(now.timestamp(), abs=1)
