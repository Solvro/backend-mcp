import pytest
from auth_app.main import check_jwt_key_config
from auth_app.settings import AuthSettings

pytestmark = pytest.mark.unit


def test_asymmetric_algorithm_requires_keypair() -> None:
    with pytest.raises(RuntimeError, match="RS256"):
        check_jwt_key_config(AuthSettings(jwt_algorithm="RS256", jwt_private_key=""))

    with pytest.raises(RuntimeError, match="RS256"):
        check_jwt_key_config(AuthSettings(jwt_algorithm="RS256", jwt_public_key=""))


def test_symmetric_algorithm_requires_secret() -> None:
    with pytest.raises(RuntimeError, match="HS256"):
        check_jwt_key_config(AuthSettings(jwt_algorithm="HS256", jwt_secret_key=""))


def test_valid_config_passes() -> None:
    check_jwt_key_config(AuthSettings(jwt_algorithm="RS256"))
    check_jwt_key_config(AuthSettings(jwt_algorithm="HS256"))


def test_unknown_algorithm_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="none"):
        check_jwt_key_config(AuthSettings(jwt_algorithm="none"))


def test_malformed_previous_public_key_is_rejected_at_startup() -> None:
    with pytest.raises(RuntimeError, match="JWT_PREVIOUS_PUBLIC_KEY"):
        check_jwt_key_config(
            AuthSettings(jwt_algorithm="RS256", jwt_previous_public_key="-----not a key-----")
        )


@pytest.mark.asyncio
async def test_jwks_endpoint_publishes_current_verification_key(async_client) -> None:
    from common.jwt_keys import key_id

    resp = await async_client.get("/.well-known/jwks.json")

    assert resp.status_code == 200
    kids = [k["kid"] for k in resp.json()["keys"]]
    assert kids == [key_id(AuthSettings().jwt_public_key)]
