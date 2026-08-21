from dataclasses import dataclass

from chat_app.sessionizer.models import Message

DEFAULT_MAX_MESSAGES: int = 6
DEFAULT_MAX_CHARS: int = 8000

_ELLIPSIS = "…"


@dataclass(frozen=True)
class ContextWindowPolicy:
    max_messages: int = DEFAULT_MAX_MESSAGES
    max_chars: int = DEFAULT_MAX_CHARS

    def __post_init__(self) -> None:
        if self.max_messages < 0:
            raise ValueError("max_messages must be >= 0")
        if self.max_chars < 1:
            raise ValueError("max_chars must be >= 1")


def render_line(message: Message) -> str:
    return f"{message.role}: {message.content}"


def select_context_messages(
    messages: list[Message],
    policy: ContextWindowPolicy,
) -> list[Message]:
    if policy.max_messages == 0 or not messages:
        return []

    windowed = messages[-policy.max_messages :]

    selected: list[Message] = []
    used = 0
    for message in reversed(windowed):
        line_len = len(render_line(message))
        extra = line_len + (1 if selected else 0)
        if selected and used + extra > policy.max_chars:
            break
        selected.append(message)
        used += extra

    selected.reverse()
    return selected


def build_context_window(
    messages: list[Message],
    policy: ContextWindowPolicy | None = None,
) -> str:
    policy = policy or ContextWindowPolicy()
    selected = select_context_messages(messages, policy)
    rendered = "\n".join(render_line(message) for message in selected)

    if len(rendered) <= policy.max_chars:
        return rendered

    only = selected[-1]
    prefix = f"{only.role}: "
    keep = max(0, policy.max_chars - len(prefix) - len(_ELLIPSIS))
    return f"{prefix}{only.content[:keep]}{_ELLIPSIS}"
