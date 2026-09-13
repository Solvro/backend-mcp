import asyncio
from contextlib import asynccontextmanager

from common.exceptions_handlers import register_exception_handlers
from common.health import build_health_router
from common.logging import setup_logging
from common.metrics import setup_metrics
from common.middleware import setup_middleware
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from auth_app.api.auth import router as auth_router
from auth_app.health import build_dependencies
from auth_app.settings import get_settings

settings = get_settings()


def generate_pem_rsa_keys():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    pem_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")

    pem_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")

    return pem_private, pem_public


@asynccontextmanager
async def lifespan(app: FastAPI):
    alg = settings.jwt_algorithm

    if alg.startswith(("RS", "ES", "EdDSA")):
        if not settings.jwt_private_key or not settings.jwt_public_key:
            settings.jwt_private_key, settings.jwt_public_key = await asyncio.to_thread(
                generate_pem_rsa_keys
                )
    elif alg.startswith("HS"):
        if not settings.jwt_secret_key:
            raise RuntimeError(
                "CRITICAL: Symmetric JWT algorithm specified, "
                "but secret key is missing in environment settings!"
            )

    yield


setup_logging(service_name="auth-service", log_level=settings.log_level)

app = FastAPI(title="ml-mcp-backend · auth-service", version="0.1.0", lifespan=lifespan)

setup_middleware(app, settings)

register_exception_handlers(app)

setup_metrics(app, settings)

app.include_router(
    build_health_router(
        service_name="auth-service",
        dependencies_provider=lambda: build_dependencies(settings),
    )
)

app.include_router(auth_router)
