from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from chat_app.sessionizer.models import (
    Conversation,
    Message,
    MessageRole,
    utcnow,
)
from chat_app.sessionizer.windowing import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_MESSAGES,
    ContextWindowPolicy,
    build_context_window,
)

DEFAULT_CONTEXT_WINDOW: int = DEFAULT_MAX_MESSAGES

_NO_ID = {"_id": 0}


class ConversationRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._conversations = db["conversations"]
        self._messages = db["messages"]

    async def create_conversation(
        self,
        user_id: str,
        metadata: dict | None = None,
    ) -> Conversation:
        conversation = Conversation(user_id=user_id, metadata=metadata or {})
        await self._conversations.insert_one(conversation.model_dump())
        return conversation

    async def get_conversation(self, session_id: str) -> Conversation | None:
        doc = await self._conversations.find_one({"session_id": session_id}, _NO_ID)
        return Conversation(**doc) if doc else None

    async def append_message(
        self,
        session_id: str,
        role: MessageRole | str,
        content: str,
        metadata: dict | None = None,
    ) -> Message:
        message = Message(
            session_id=session_id,
            role=role,
            content=content,
            metadata=metadata or {},
        )
        await self._messages.insert_one(message.model_dump())
        await self._conversations.update_one(
            {"session_id": session_id},
            {
                "$set": {"updated_at": message.timestamp},
                "$inc": {"message_count": 1},
            },
        )
        return message

    async def get_history(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[Message]:
        query = self._messages.find({"session_id": session_id}, _NO_ID)
        if limit is None:
            docs = await query.sort(
                [("timestamp", ASCENDING), ("_id", ASCENDING)]
            ).to_list(length=None)
        else:
            docs = (
                await query.sort([("timestamp", DESCENDING), ("_id", DESCENDING)])
                .limit(limit)
                .to_list(length=limit)
            )
            docs.reverse()
        return [Message(**doc) for doc in docs]

    async def get_context_window(
        self,
        session_id: str,
        max_messages: int = DEFAULT_CONTEXT_WINDOW,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> str:
        policy = ContextWindowPolicy(max_messages=max_messages, max_chars=max_chars)
        if policy.max_messages == 0:
            return ""
        messages = await self.get_history(session_id, limit=policy.max_messages)
        return build_context_window(messages, policy)

    async def list_by_user(
        self,
        user_id: str,
        *,
        active_only: bool = False,
        limit: int | None = None,
        skip: int = 0,
    ) -> list[Conversation]:
        query: dict = {"user_id": user_id}
        if active_only:
            query["is_active"] = True
        cursor = (
            self._conversations.find(query, _NO_ID)
            .sort([("updated_at", DESCENDING), ("_id", DESCENDING)])
            .skip(skip)
        )
        if limit is not None:
            cursor = cursor.limit(limit)
        docs = await cursor.to_list(length=limit)
        return [Conversation(**doc) for doc in docs]

    async def deactivate(self, session_id: str) -> bool:
        result = await self._conversations.update_one(
            {"session_id": session_id},
            {"$set": {"is_active": False, "updated_at": utcnow()}},
        )
        return result.modified_count > 0

    async def delete(self, session_id: str) -> bool:
        await self._messages.delete_many({"session_id": session_id})
        result = await self._conversations.delete_one({"session_id": session_id})
        return result.deleted_count > 0
