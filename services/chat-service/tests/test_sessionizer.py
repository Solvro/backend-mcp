import asyncio
from datetime import timedelta

import pytest
from chat_app.sessionizer import (
    Conversation,
    ConversationRepository,
    Message,
    MessageRole,
)
from mongomock_motor import AsyncMongoMockClient

pytestmark = pytest.mark.unit

_TTL = timedelta(days=30)


@pytest.fixture
def repo() -> ConversationRepository:
    db = AsyncMongoMockClient()["mcp_backend_test"]
    return ConversationRepository(db)


@pytest.fixture
def repo_ttl() -> ConversationRepository:
    db = AsyncMongoMockClient()["mcp_backend_test"]
    return ConversationRepository(db, anonymous_ttl=_TTL)


async def _seed(repo: ConversationRepository, session_id: str, count: int) -> None:
    for i in range(count):
        role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
        await repo.append_message(session_id, role, f"msg-{i}")


@pytest.mark.unit
async def test_create_conversation_persists_and_is_retrievable(repo):
    created = await repo.create_conversation("user-1", metadata={"src": "web"})

    fetched = await repo.get_conversation(created.session_id)

    assert isinstance(fetched, Conversation)
    assert fetched.user_id == "user-1"
    assert fetched.metadata == {"src": "web"}
    assert fetched.is_active is True
    assert fetched.message_count == 0


@pytest.mark.unit
async def test_get_conversation_missing_returns_none(repo):
    assert await repo.get_conversation("nope") is None


@pytest.mark.unit
async def test_anonymous_conversation_has_null_user_and_is_not_listed(repo):
    anon = await repo.create_conversation()

    fetched = await repo.get_conversation(anon.session_id)
    assert fetched.user_id is None

    await repo.create_conversation("user-1")
    assert anon.session_id not in {c.session_id for c in await repo.list_by_user("user-1")}


@pytest.mark.unit
async def test_append_message_stores_message_and_bumps_conversation(repo):
    conv = await repo.create_conversation("user-1")

    message = await repo.append_message(
        conv.session_id,
        MessageRole.USER,
        "gdzie jest sala 101?",
        metadata={"trace_id": "abc123"},
    )

    assert isinstance(message, Message)
    assert message.session_id == conv.session_id
    assert message.role == "user"
    assert message.metadata["trace_id"] == "abc123"

    refreshed = await repo.get_conversation(conv.session_id)
    assert refreshed.message_count == 1
    assert refreshed.updated_at >= refreshed.created_at


@pytest.mark.unit
async def test_get_history_returns_chronological_order(repo):
    conv = await repo.create_conversation("user-1")
    await _seed(repo, conv.session_id, 4)

    history = await repo.get_history(conv.session_id)

    assert [m.content for m in history] == ["msg-0", "msg-1", "msg-2", "msg-3"]


@pytest.mark.unit
async def test_get_history_limit_keeps_most_recent_in_order(repo):
    conv = await repo.create_conversation("user-1")
    await _seed(repo, conv.session_id, 5)

    history = await repo.get_history(conv.session_id, limit=2)

    assert [m.content for m in history] == ["msg-3", "msg-4"]


@pytest.mark.unit
async def test_get_context_window_formats_last_n(repo):
    conv = await repo.create_conversation("user-1")
    await _seed(repo, conv.session_id, 3)

    window = await repo.get_context_window(conv.session_id, max_messages=2)

    assert window == "assistant: msg-1\nuser: msg-2"


@pytest.mark.unit
async def test_get_context_window_applies_char_budget(repo):
    conv = await repo.create_conversation("user-1")
    await _seed(repo, conv.session_id, 3)

    window = await repo.get_context_window(
        conv.session_id, max_messages=6, max_chars=len("user: msg-2")
    )

    assert window == "user: msg-2"


@pytest.mark.unit
async def test_get_context_window_zero_messages_is_empty(repo):
    conv = await repo.create_conversation("user-1")
    await _seed(repo, conv.session_id, 3)

    assert await repo.get_context_window(conv.session_id, max_messages=0) == ""


@pytest.mark.unit
async def test_ttl_set_on_anonymous_conversation(repo_ttl):
    conv = await repo_ttl.create_conversation()  # anonymous

    assert conv.expires_at == conv.created_at + _TTL
    assert (await repo_ttl.get_conversation(conv.session_id)).expires_at is not None


@pytest.mark.unit
async def test_no_ttl_on_logged_in_conversation(repo_ttl):
    conv = await repo_ttl.create_conversation("user-1")

    assert conv.expires_at is None
    assert (await repo_ttl.get_conversation(conv.session_id)).expires_at is None


@pytest.mark.unit
async def test_anonymous_messages_expire_and_conversation_ttl_slides(repo_ttl):
    conv = await repo_ttl.create_conversation()

    msg = await repo_ttl.append_message(conv.session_id, MessageRole.USER, "hi")

    assert msg.expires_at == msg.timestamp + _TTL
    stored_msg = (await repo_ttl.get_history(conv.session_id))[0]
    refreshed = await repo_ttl.get_conversation(conv.session_id)
    assert stored_msg.expires_at == stored_msg.timestamp + _TTL
    assert refreshed.expires_at == stored_msg.expires_at  # conversation TTL slid forward


@pytest.mark.unit
async def test_logged_in_messages_have_no_ttl(repo_ttl):
    conv = await repo_ttl.create_conversation("user-1")

    msg = await repo_ttl.append_message(conv.session_id, MessageRole.USER, "hi")

    assert msg.expires_at is None
    assert (await repo_ttl.get_conversation(conv.session_id)).expires_at is None


@pytest.mark.unit
async def test_no_ttl_configured_leaves_expiry_unset(repo):
    conv = await repo.create_conversation()  # anonymous, but repo has no TTL
    msg = await repo.append_message(conv.session_id, MessageRole.USER, "hi")

    assert conv.expires_at is None
    assert msg.expires_at is None


@pytest.mark.unit
async def test_history_is_scoped_by_session(repo):
    a = await repo.create_conversation("user-1")
    b = await repo.create_conversation("user-1")
    await repo.append_message(a.session_id, MessageRole.USER, "in-a")
    await repo.append_message(b.session_id, MessageRole.USER, "in-b")

    history_a = await repo.get_history(a.session_id)

    assert [m.content for m in history_a] == ["in-a"]


@pytest.mark.unit
async def test_list_by_user_orders_by_updated_desc(repo):
    first = await repo.create_conversation("user-1")
    second = await repo.create_conversation("user-1")
    await asyncio.sleep(0.002)
    await repo.append_message(first.session_id, MessageRole.USER, "hi")
    await repo.create_conversation("other-user")

    sessions = await repo.list_by_user("user-1")

    assert [c.session_id for c in sessions] == [first.session_id, second.session_id]


@pytest.mark.unit
async def test_list_by_user_active_only_and_pagination(repo):
    keep = await repo.create_conversation("user-1")
    gone = await repo.create_conversation("user-1")
    await repo.deactivate(gone.session_id)

    active = await repo.list_by_user("user-1", active_only=True)
    assert [c.session_id for c in active] == [keep.session_id]

    page = await repo.list_by_user("user-1", limit=1, skip=1)
    assert len(page) == 1


@pytest.mark.unit
async def test_deactivate_flags_conversation(repo):
    conv = await repo.create_conversation("user-1")

    changed = await repo.deactivate(conv.session_id)

    assert changed is True
    assert (await repo.get_conversation(conv.session_id)).is_active is False
    assert await repo.deactivate("missing") is False


@pytest.mark.unit
async def test_delete_removes_conversation_and_messages(repo):
    conv = await repo.create_conversation("user-1")
    await _seed(repo, conv.session_id, 3)

    deleted = await repo.delete(conv.session_id)

    assert deleted is True
    assert await repo.get_conversation(conv.session_id) is None
    assert await repo.get_history(conv.session_id) == []
    assert await repo.delete(conv.session_id) is False
