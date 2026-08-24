from datetime import timedelta

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
    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        *,
        anonymous_ttl: timedelta | None = None,
    ) -> None:
        self._conversations = db["conversations"]
        self._messages = db["messages"]
        self._anonymous_ttl = anonymous_ttl

    async def create_conversation(
        self,
        user_id: str | None = None,
        metadata: dict | None = None,
    ) -> Conversation:
        conversation = Conversation(user_id=user_id, metadata=metadata or {})
        if user_id is None and self._anonymous_ttl is not None:
            conversation.expires_at = conversation.created_at + self._anonymous_ttl
        await self._conversations.insert_one(conversation.model_dump())
        return conversation

    @staticmethod
    def _scoped(session_id: str, user_id: str | None) -> dict:
        query: dict = {"session_id": session_id}
        if user_id is not None:
            query["user_id"] = user_id
        return query

    async def get_conversation(
        self, session_id: str, *, user_id: str | None = None
    ) -> Conversation | None:
        doc = await self._conversations.find_one(self._scoped(session_id, user_id), _NO_ID)
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
        update: dict = {
            "$set": {"updated_at": message.timestamp},
            "$inc": {"message_count": 1},
        }
        if self._anonymous_ttl is not None and await self._is_anonymous(session_id):
            message.expires_at = message.timestamp + self._anonymous_ttl
            update["$set"]["expires_at"] = message.expires_at
        await self._messages.insert_one(message.model_dump())
        await self._conversations.update_one({"session_id": session_id}, update)
        return message

    async def _is_anonymous(self, session_id: str) -> bool:
        doc = await self._conversations.find_one(
            {"session_id": session_id}, {"_id": 0, "user_id": 1}
        )
        return doc is not None and doc.get("user_id") is None

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

    async def deactivate(self, session_id: str, *, user_id: str | None = None) -> bool:
        result = await self._conversations.update_one(
            self._scoped(session_id, user_id),
            {"$set": {"is_active": False, "updated_at": utcnow()}},
        )
        return result.matched_count > 0

    async def delete(self, session_id: str, *, user_id: str | None = None) -> bool:
        result = await self._conversations.delete_one(self._scoped(session_id, user_id))
        if result.deleted_count == 0:
            return False
        await self._messages.delete_many({"session_id": session_id})
        return True
