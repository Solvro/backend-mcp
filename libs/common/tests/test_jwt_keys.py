import jwt
import pytest
from common.errors import AuthError
from common.jwt_keys import build_jwks, key_id, signing_kid, verification_keys
from common.settings import CommonSettings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

pytestmark = pytest.mark.unit


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


CUR_PRIV, CUR_PUB = _keypair()
OLD_PRIV, OLD_PUB = _keypair()


def _settings(**overrides) -> CommonSettings:
    base = dict(jwt_algorithm="RS256", jwt_private_key=CUR_PRIV, jwt_public_key=CUR_PUB)
    base.update(overrides)
    return CommonSettings(**base)


def test_key_id_is_deterministic_and_differs_per_key() -> None:
    assert key_id(CUR_PUB) == key_id(CUR_PUB)
    assert key_id(CUR_PUB) != key_id(OLD_PUB)
    assert len(key_id(CUR_PUB)) == 16


def test_key_id_ignores_pem_whitespace_differences() -> None:
    assert key_id(CUR_PUB) == key_id(CUR_PUB.strip() + "\r\n")


def test_signing_kid_is_the_current_public_keys_id() -> None:
    assert signing_kid(_settings()) == key_id(CUR_PUB)


def test_verification_keys_hold_current_only_by_default() -> None:
    keys = verification_keys(_settings())
    assert keys == {key_id(CUR_PUB): CUR_PUB}


def test_verification_keys_include_previous_key_when_configured() -> None:
    keys = verification_keys(_settings(jwt_previous_public_key=OLD_PUB))
    assert set(keys) == {key_id(CUR_PUB), key_id(OLD_PUB)}
    assert keys[key_id(OLD_PUB)] == OLD_PUB


def test_verification_keys_reject_symmetric_algorithms() -> None:
    with pytest.raises(AuthError):
        verification_keys(CommonSettings(jwt_algorithm="HS256", jwt_secret_key="s" * 32))


def test_jwks_lists_every_verification_key_as_public_rsa_jwk() -> None:
    jwks = build_jwks(_settings(jwt_previous_public_key=OLD_PUB))

    assert set(jwks) == {"keys"}
    by_kid = {k["kid"]: k for k in jwks["keys"]}
    assert set(by_kid) == {key_id(CUR_PUB), key_id(OLD_PUB)}
    for k in by_kid.values():
        assert k["kty"] == "RSA"
        assert k["use"] == "sig"
        assert k["alg"] == "RS256"
        assert {"n", "e"} <= set(k)
        assert "d" not in k  # never the private exponent

    token = jwt.encode({"sub": "u1"}, CUR_PRIV, algorithm="RS256", headers={"kid": key_id(CUR_PUB)})
    verifier = jwt.PyJWK.from_dict(by_kid[key_id(CUR_PUB)])
    assert jwt.decode(token, verifier.key, algorithms=["RS256"])["sub"] == "u1"
