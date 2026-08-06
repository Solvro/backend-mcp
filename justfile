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
    uv run pytest -m "not e2e"

test-e2e:
    uv run pytest -m e2e

e2e:
    docker compose -f docker/compose.e2e.yml up -d --build --wait
    -uv run pytest -m e2e
    docker compose -f docker/compose.e2e.yml down -v

build:
    docker build -f services/auth-service/Dockerfile -t ml-mcp-backend/auth-service:dev .
    docker build -f services/chat-service/Dockerfile -t ml-mcp-backend/chat-service:dev .

up:
    docker compose -f docker/compose.yml up -d --build

down:
    docker compose -f docker/compose.yml down
