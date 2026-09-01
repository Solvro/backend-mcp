import logging
from datetime import datetime

from common.auth import optional_auth
from common.context import user_id_var
from common.errors import NotFoundError, ValidationError
from common.observability import new_trace_id, start_turn_trace
from common.rate_limit import daily_quota, rate_limit
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent

from chat_app.answer import (
    AnswerDeps,
    AnswerResult,
    generate_answer,
)
from chat_app.api.sessions import get_repository
from chat_app.mcp_gateway import KnowledgeGraphGateway
from chat_app.sessionizer import ConversationRepository, MessageRole
from chat_app.settings import ChatSettings
from chat_app.validators import validate_chat_message

logger = logging.getLogger(__name__)

_SESSION_NOT_FOUND = "Session not found."

SOURCE_KNOWLEDGE_GRAPH = "mcp_knowledge_graph"
SOURCE_ERROR = "error"

DEGRADED_ANSWER = (
    "Przepraszam, w tej chwili nie mogę uzyskać odpowiedzi z bazy wiedzy. "
    "Spróbuj ponownie za chwilę."
)


class ChatRequest(BaseModel):
    message: str = Field(description="User chat message (validated by the input guardrail).")
    session_id: str | None = Field(
        default=None,
        description="Continue an existing conversation; omit to start a new one.",
    )
    metadata: dict | None = Field(default=None, description="Optional client metadata.")

    @field_validator("message")
    @classmethod
    def _guard(cls, value: str) -> str:
        try:
            return validate_chat_message(value)
        except ValidationError as exc:
            raise ValueError(str(exc.detail)) from exc


class ChatResponse(BaseModel):
    session_id: str
    message: str
    timestamp: datetime
    metadata: dict


def get_gateway(request: Request) -> KnowledgeGraphGateway:
    return request.app.state.mcp_gateway


def get_answer_agent(request: Request) -> Agent[AnswerDeps, AnswerResult] | None:
    return request.app.state.answer_agent


def build_chat_router(settings: ChatSettings) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["chat"])

    @router.post(
        "/chat",
        response_model=ChatResponse,
        dependencies=[
            Depends(optional_auth(settings=settings)),
            Depends(rate_limit("chat:message", settings=settings)),
            Depends(
                daily_quota(
                    "chat:message",
                    settings=settings,
                    anonymous_limit=settings.chat_daily_quota_anonymous,
                    authenticated_limit=settings.chat_daily_quota_authenticated,
                )
            ),
        ],
    )
    async def chat(
        request: ChatRequest,
        repo: ConversationRepository = Depends(get_repository),
        gateway: KnowledgeGraphGateway = Depends(get_gateway),
        agent: Agent[AnswerDeps, AnswerResult] | None = Depends(get_answer_agent),
    ) -> ChatResponse:
        user_id = user_id_var.get()

        if request.session_id:
            conversation = await repo.get_for_turn(request.session_id, user_id=user_id)
            if conversation is None:
                raise NotFoundError(_SESSION_NOT_FOUND)
        else:
            conversation = await repo.create_conversation(
                user_id=user_id, metadata=request.metadata
            )
        session_id = conversation.session_id

        history = await repo.get_context_window(
            session_id,
            max_messages=settings.context_max_messages,
            max_chars=settings.context_max_chars,
        )

        await repo.append_message(
            session_id, MessageRole.USER, request.message, metadata=request.metadata
        )

        trace_id = new_trace_id()
        source = SOURCE_KNOWLEDGE_GRAPH
        try:
            with start_turn_trace(
                trace_id,
                session_id=session_id,
                input=request.message,
                settings=settings,
            ):
                result = await generate_answer(
                    agent,
                    question=request.message,
                    history=history,
                    gateway=gateway,
                    trace_id=trace_id,
                )
                answer = result.answer
        except Exception:
            logger.exception("Chat orchestration failed for session %s", session_id)
            answer = DEGRADED_ANSWER
            source = SOURCE_ERROR

        assistant = await repo.append_message(
            session_id,
            MessageRole.ASSISTANT,
            answer,
            metadata={"source": source, "trace_id": trace_id},
        )

        refreshed = await repo.get_for_turn(session_id, user_id=user_id)
        message_count = refreshed.message_count if refreshed else conversation.message_count + 2

        return ChatResponse(
            session_id=session_id,
            message=answer,
            timestamp=assistant.timestamp,
            metadata={
                "message_count": message_count,
                "source": source,
                "trace_id": trace_id,
            },
        )

    return router
