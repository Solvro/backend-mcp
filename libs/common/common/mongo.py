from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from common.settings import CommonSettings

_client: AsyncIOMotorClient | None = None


def get_mongo_client(settings: CommonSettings | None = None) -> AsyncIOMotorClient:
    global _client
    if _client is None:
        s = settings or CommonSettings()
        _client = AsyncIOMotorClient(
            s.mongo_uri,
            maxPoolSize=s.mongo_max_pool_size,
            minPoolSize=s.mongo_min_pool_size,
            serverSelectionTimeoutMS=s.mongo_server_selection_timeout_ms,
            uuidRepresentation="standard",
        )
    return _client


def get_database(settings: CommonSettings | None = None) -> AsyncIOMotorDatabase:
    s = settings or CommonSettings()
    return get_mongo_client(s)[s.mongo_db_name]


async def check_mongo() -> bool:
    await get_mongo_client().admin.command("ping")
    return True


async def create_indexes(settings: CommonSettings | None = None) -> None:
    db = get_database(settings)

    await db["conversations"].create_index(
        [("session_id", ASCENDING)], unique=True, name="uq_session_id"
    )
    await db["conversations"].create_index(
        [("user_id", ASCENDING), ("updated_at", DESCENDING)], name="ix_user_updated"
    )

    await db["messages"].create_index([("id", ASCENDING)], unique=True, name="uq_message_id")
    await db["messages"].create_index(
        [("session_id", ASCENDING), ("timestamp", ASCENDING)], name="ix_session_ts"
    )


async def close_mongo_client() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None
