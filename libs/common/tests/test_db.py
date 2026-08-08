import asyncio

import pytest
from common.db import dispose_engine, get_engine
from common.settings import CommonSettings


@pytest.fixture
def clean_engine():
    asyncio.run(dispose_engine())
    yield
    asyncio.run(dispose_engine())


@pytest.mark.unit
def test_engine_uses_async_driver_and_configured_pool(clean_engine):
    settings = CommonSettings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        db_pool_size=7,
        db_max_overflow=3,
    )
    engine = get_engine(settings)

    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.pool.size() == 7
    assert engine.pool._max_overflow == 3


@pytest.mark.unit
def test_get_engine_is_a_singleton(clean_engine):
    settings = CommonSettings(database_url="postgresql+asyncpg://user:pass@localhost/db")
    assert get_engine(settings) is get_engine()
