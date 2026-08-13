import asyncio

from alembic import context
from auth_app.models import RefreshToken, Role, User, UserRole  # noqa: F401
from common.db import Base
from common.settings import CommonSettings
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = CommonSettings().database_url
if not DATABASE_URL:
    msg: str = "DATABASE_URL is not set"
    raise RuntimeError(msg)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
