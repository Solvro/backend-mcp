import base64
import hashlib
import json

import jwt
from cryptography.hazmat.primitives import serialization

from common.errors import AuthError
from common.settings import CommonSettings

ASYMMETRIC_PREFIXES = ("RS", "ES", "EdDSA")


def is_asymmetric(algorithm: str) -> bool:
    return algorithm.startswith(ASYMMETRIC_PREFIXES)


def key_id(public_key_pem: str) -> str:
    public_key = serialization.load_pem_public_key(public_key_pem.strip().encode())
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode()[:16]


def signing_kid(settings: CommonSettings) -> str:
    return key_id(settings.jwt_public_key)


def verification_keys(settings: CommonSettings) -> dict[str, str]:
    if not is_asymmetric(settings.jwt_algorithm):
        raise AuthError(f"JWT algorithm {settings.jwt_algorithm} has no public key set.")
    keys = {key_id(settings.jwt_public_key): settings.jwt_public_key}
    if settings.jwt_previous_public_key:
        keys[key_id(settings.jwt_previous_public_key)] = settings.jwt_previous_public_key
    return keys


def build_jwks(settings: CommonSettings) -> dict:
    """RFC 7517 JWK Set of the verification keys (public parts only)."""
    algorithm = jwt.get_algorithm_by_name(settings.jwt_algorithm)
    keys = []
    for kid, pem in verification_keys(settings).items():
        jwk = json.loads(algorithm.to_jwk(algorithm.prepare_key(pem)))
        keys.append({**jwk, "kid": kid, "use": "sig", "alg": settings.jwt_algorithm})
    return {"keys": keys}
