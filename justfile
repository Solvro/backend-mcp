# Load a repo-root .env into recipe environments when present (optional).
set dotenv-load := true

default:
    @just --list

sync:
    uv sync --all-packages --dev

lint:
    uv run ruff check .
    uv run python scripts/check_env_example.py
    uv run python scripts/check_prod_secrets.py

fmt:
    uv run ruff format .
    uv run ruff check . --fix

test:
    uv run pytest -m "not e2e" --cov --cov-report=term-missing

test-e2e:
    uv run pytest -m e2e

e2e: e2e-keys
    docker compose -f docker/compose.e2e.yml up -d --build --wait
    -uv run pytest -m e2e
    docker compose -f docker/compose.e2e.yml down -v

e2e-keys:
    @test -f docker/.e2e-keys/jwt_private.pem || ( \
        mkdir -p docker/.e2e-keys && \
        openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out docker/.e2e-keys/jwt_private.pem 2>/dev/null && \
        openssl pkey -in docker/.e2e-keys/jwt_private.pem -pubout -out docker/.e2e-keys/jwt_public.pem && \
        chmod 644 docker/.e2e-keys/*.pem && \
        echo "wrote docker/.e2e-keys/jwt_{private,public}.pem" )

migrate:
    DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://postgres:postgres@localhost:5432/mcp_backend}" \
        uv run --package auth-service alembic -c services/auth-service/alembic.ini upgrade head

build:
    docker build -f services/auth-service/Dockerfile -t ml-mcp-backend/auth-service:dev .
    docker build -f services/chat-service/Dockerfile -t ml-mcp-backend/chat-service:dev .

up: network tls-selfsigned e2e-keys
    docker compose --profile dev -f docker/compose.yml up -d --build --wait

# Production overlay: docker secrets, restart policies, resource limits, no dev ports.
# Expects the secret files under $SECRETS_DIR (default /etc/ml-mcp/secrets) and .env.prod.
up-prod: network
    docker compose --env-file .env.prod -f docker/compose.yml -f docker/compose.prod.yml up -d --build --wait

down-prod:
    docker compose --env-file .env.prod -f docker/compose.yml -f docker/compose.prod.yml down

# Self-signed cert for the local edge (gitignored). Idempotent: keeps an existing pair.
tls-selfsigned:
    @test -f docker/.tls/cert.pem || ( \
        mkdir -p docker/.tls && \
        openssl req -x509 -newkey rsa:2048 -nodes -days 825 -sha256 \
            -keyout docker/.tls/key.pem -out docker/.tls/cert.pem \
            -subj "/CN=localhost" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" 2>/dev/null && \
        chmod 644 docker/.tls/*.pem && \
        echo "wrote docker/.tls/{cert,key}.pem (self-signed, localhost)" )

# Create the network shared with ml-mcp's mcp-server (idempotent; either stack may run it first)
network:
    docker network inspect solvro-mcp-internal >/dev/null 2>&1 || docker network create --internal solvro-mcp-internal

down:
    docker compose --profile dev -f docker/compose.yml down

down-hard:
    docker compose --profile dev -f docker/compose.yml down -v

logs *service:
    docker compose -f docker/compose.yml logs -f {{service}}
