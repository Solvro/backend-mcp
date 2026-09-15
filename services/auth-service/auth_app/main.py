from contextlib import asynccontextmanager

from common.exceptions_handlers import register_exception_handlers
from common.health import build_health_router
from common.jwt_keys import build_jwks, is_asymmetric, verification_keys
from common.logging import setup_logging
from common.metrics import setup_metrics
from common.middleware import setup_middleware
from fastapi import FastAPI

from auth_app.api.auth import router as auth_router
from auth_app.health import build_dependencies
from auth_app.settings import AuthSettings, get_settings

settings = get_settings()


def check_jwt_key_config(settings: AuthSettings) -> None:
    alg = settings.jwt_algorithm

    if is_asymmetric(alg):
        if not settings.jwt_private_key or not settings.jwt_public_key:
            raise RuntimeError(
                f"JWT_ALGORITHM={alg} requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY "
                "(or the *_FILE variants) to be set."
            )
        try:
            verification_keys(settings)
        except ValueError as exc:
            raise RuntimeError(
                "JWT_PUBLIC_KEY / JWT_PREVIOUS_PUBLIC_KEY must be PEM-encoded public keys."
            ) from exc
    elif alg.startswith("HS"):
        if not settings.jwt_secret_key:
            raise RuntimeError(f"JWT_ALGORITHM={alg} requires JWT_SECRET_KEY to be set.")
    else:
        raise RuntimeError(f"Unsupported JWT_ALGORITHM={alg!r}.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_jwt_key_config(settings)
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


@app.get("/.well-known/jwks.json", include_in_schema=False)
def jwks() -> dict:
    return build_jwks(get_settings())
