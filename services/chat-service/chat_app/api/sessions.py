from datetime import timedelta

from common.auth import require_auth
from common.errors import NotFoundError
from common.mongo import get_database
from fastapi import APIRouter, Depends, Query, Response

from chat_app.sessionizer import Conversation, ConversationRepository, Message
from chat_app.settings import ChatSettings, get_settings

_SESSION_NOT_FOUND = "Session not found."


def get_repository(settings: ChatSettings = Depends(get_settings)) -> ConversationRepository:
    ttl = None
    if settings.anonymous_retention_days > 0:
        ttl = timedelta(days=settings.anonymous_retention_days)
    return ConversationRepository(get_database(settings), anonymous_ttl=ttl)


def build_sessions_router(settings: ChatSettings) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["sessions"])
    authenticated = require_auth(settings=settings)

    @router.get("/sessions/{session_id}", response_model=Conversation)
    async def get_session(
        session_id: str,
        user_id: str = Depends(authenticated),
        repo: ConversationRepository = Depends(get_repository),
    ) -> Conversation:
        conversation = await repo.get_conversation(session_id, user_id=user_id)
        if conversation is None:
            raise NotFoundError(_SESSION_NOT_FOUND)
        return conversation

    @router.get("/sessions/{session_id}/history", response_model=list[Message])
    async def get_session_history(
        session_id: str,
        limit: int | None = Query(default=None, ge=1, le=500),
        user_id: str = Depends(authenticated),
        repo: ConversationRepository = Depends(get_repository),
    ) -> list[Message]:
        conversation = await repo.get_conversation(session_id, user_id=user_id)
        if conversation is None:
            raise NotFoundError(_SESSION_NOT_FOUND)
        return await repo.get_history(session_id, limit=limit)

    @router.get("/users/me/sessions", response_model=list[Conversation])
    async def list_my_sessions(
        active_only: bool = Query(default=False),
        limit: int | None = Query(default=None, ge=1, le=100),
        skip: int = Query(default=0, ge=0),
        user_id: str = Depends(authenticated),
        repo: ConversationRepository = Depends(get_repository),
    ) -> list[Conversation]:
        return await repo.list_by_user(
            user_id, active_only=active_only, limit=limit, skip=skip
        )

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(
        session_id: str,
        user_id: str = Depends(authenticated),
        repo: ConversationRepository = Depends(get_repository),
    ) -> Response:
        if not await repo.delete(session_id, user_id=user_id):
            raise NotFoundError(_SESSION_NOT_FOUND)
        return Response(status_code=204)

    @router.post("/sessions/{session_id}/deactivate", response_model=Conversation)
    async def deactivate_session(
        session_id: str,
        user_id: str = Depends(authenticated),
        repo: ConversationRepository = Depends(get_repository),
    ) -> Conversation:
        if not await repo.deactivate(session_id, user_id=user_id):
            raise NotFoundError(_SESSION_NOT_FOUND)
        conversation = await repo.get_conversation(session_id, user_id=user_id)
        assert conversation is not None
        return conversation

    return router
