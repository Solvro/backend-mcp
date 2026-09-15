import jwt
import pytest
from auth_app.models import User
from auth_app.security import create_access_token
from auth_app.settings import get_settings
from common.auth import decode_access_token

pytestmark = pytest.mark.unit


def test_hs256_tokens_are_signed_with_the_shared_secret(monkeypatch) -> None:
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    get_settings.cache_clear()
    settings = get_settings()
    user = User(id=1, username="u", email="u@x", password_hash="h", email_verified=True)

    token = create_access_token(user)

    assert "kid" not in jwt.get_unverified_header(token)
    assert decode_access_token(token, settings)["sub"] == "1"
