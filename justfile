# Load a repo-root .env into recipe environments when present (optional).
set dotenv-load := true

default:
    @just --list

sync:
    uv sync --all-packages --dev

lint:
    uv run ruff check .

fmt:
    uv run ruff format .
    uv run ruff check . --fix

test:
    uv run pytest -m "not e2e" --cov --cov-report=term-missing

test-e2e:
    uv run pytest -m e2e

e2e:
    docker compose -f docker/compose.e2e.yml up -d --build --wait
    -uv run pytest -m e2e
    docker compose -f docker/compose.e2e.yml down -v

migrate:
    DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://postgres:postgres@localhost:5432/mcp_backend}" \
        uv run --package auth-service alembic -c services/auth-service/alembic.ini upgrade head

build:
    docker build -f services/auth-service/Dockerfile -t ml-mcp-backend/auth-service:dev .
    docker build -f services/chat-service/Dockerfile -t ml-mcp-backend/chat-service:dev .

up:
    docker compose -f docker/compose.yml up -d --build --wait

down:
    docker compose -f docker/compose.yml down

down-hard:
    docker compose -f docker/compose.yml down -v

logs *service:
    docker compose -f docker/compose.yml logs -f {{service}}
