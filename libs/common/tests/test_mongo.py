import asyncio

import pytest
from common.mongo import close_mongo_client, get_database, get_mongo_client
from common.settings import CommonSettings

SETTINGS = CommonSettings(mongo_uri="mongodb://localhost:27017", mongo_db_name="testdb")


@pytest.fixture
def clean_client():
    asyncio.run(close_mongo_client())
    yield
    asyncio.run(close_mongo_client())


@pytest.mark.unit
def test_client_is_singleton(clean_client):
    async def _check() -> bool:
        return get_mongo_client(SETTINGS) is get_mongo_client()

    assert asyncio.run(_check())


@pytest.mark.unit
def test_get_database_uses_configured_name(clean_client):
    async def _name() -> str:
        return get_database(SETTINGS).name

    assert asyncio.run(_name()) == "testdb"
