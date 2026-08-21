import pytest
from chat_app.sessionizer import (
    ContextWindowPolicy,
    MessageRole,
    build_context_window,
    select_context_messages,
)
from chat_app.sessionizer.models import Message

pytestmark = pytest.mark.unit


def _msg(content: str, role: MessageRole = MessageRole.USER) -> Message:
    return Message(session_id="s", role=role, content=content)


def _history(*contents: str) -> list[Message]:
    return [_msg(c) for i, c in enumerate(contents)]


def test_defaults_match_reference() -> None:
    policy = ContextWindowPolicy()
    assert policy.max_messages == 6
    assert policy.max_chars == 8000


def test_empty_history_yields_empty_string() -> None:
    assert build_context_window([], ContextWindowPolicy()) == ""


def test_keeps_only_last_n_messages() -> None:
    history = _history("a", "b", "c", "d", "e")
    policy = ContextWindowPolicy(max_messages=2, max_chars=10_000)

    selected = select_context_messages(history, policy)

    assert [m.content for m in selected] == ["d", "e"]


def test_fewer_messages_than_window_returns_all() -> None:
    history = _history("a", "b")
    policy = ContextWindowPolicy(max_messages=6, max_chars=10_000)

    assert [m.content for m in select_context_messages(history, policy)] == ["a", "b"]


def test_char_budget_drops_oldest_first() -> None:
    history = _history("msg-0", "msg-1", "msg-2", "msg-3")
    policy = ContextWindowPolicy(max_messages=6, max_chars=24)

    selected = select_context_messages(history, policy)

    assert [m.content for m in selected] == ["msg-2", "msg-3"]


def test_budget_keeps_most_recent_deterministically() -> None:
    history = _history("old", "mid", "new")
    policy = ContextWindowPolicy(max_messages=6, max_chars=len("user: new"))

    assert build_context_window(history, policy) == "user: new"


def test_windowing_then_budget_compose() -> None:
    history = _history("a", "b", "c", "d", "e")
    policy = ContextWindowPolicy(max_messages=3, max_chars=len("user: d\nuser: e"))

    assert build_context_window(history, policy) == "user: d\nuser: e"


def test_single_oversized_message_is_truncated_to_budget() -> None:
    history = _history("x" * 100)
    policy = ContextWindowPolicy(max_messages=6, max_chars=20)

    rendered = build_context_window(history, policy)

    assert len(rendered) <= 20
    assert rendered.startswith("user: x")
    assert rendered.endswith("…")


def test_oversized_message_never_yields_empty() -> None:
    history = _history("y" * 5000)
    policy = ContextWindowPolicy(max_messages=6, max_chars=10)

    rendered = build_context_window(history, policy)

    assert rendered != ""
    assert len(rendered) <= 10


def test_max_messages_zero_yields_empty() -> None:
    history = _history("a", "b")
    assert select_context_messages(history, ContextWindowPolicy(max_messages=0)) == []
    assert build_context_window(history, ContextWindowPolicy(max_messages=0)) == ""


def test_formats_role_and_content() -> None:
    history = [
        _msg("question", MessageRole.USER),
        _msg("answer", MessageRole.ASSISTANT),
    ]
    policy = ContextWindowPolicy(max_messages=6, max_chars=10_000)

    assert build_context_window(history, policy) == "user: question\nassistant: answer"


def test_output_is_deterministic() -> None:
    history = _history("a", "b", "c", "d")
    policy = ContextWindowPolicy(max_messages=3, max_chars=100)

    assert build_context_window(history, policy) == build_context_window(history, policy)


@pytest.mark.parametrize(
    ("max_messages", "max_chars"),
    [(-1, 100), (6, 0), (6, -5)],
)
def test_invalid_policy_rejected(max_messages: int, max_chars: int) -> None:
    with pytest.raises(ValueError):
        ContextWindowPolicy(max_messages=max_messages, max_chars=max_chars)
