import re
import unicodedata

from common.errors import ValidationError
from pydantic import BaseModel, Field, field_validator

from chat_app.settings import get_settings

DEFAULT_MIN_LENGTH: int = 1
DEFAULT_MAX_LENGTH: int = 2000

_PATTERN_SOURCES: list[tuple[str, str]] = [
    ("prompt_injection", r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions"),
    ("prompt_injection", r"disregard\s+(?:the\s+)?(?:system|previous)\s+(?:prompt|instructions)"),
    (
        "prompt_injection",
        r"zignoruj\s+(?:wszystkie\s+)?"
        r"(?:poprzednie|powyzsze|wczesniejsze|dotychczasowe)\s+"
        r"(?:instrukcje|polecenia|wytyczne|wiadomosci)",
    ),
    (
        "prompt_injection",
        r"pomin\s+(?:poprzednie|powyzsze|wczesniejsze)\s+(?:instrukcje|polecenia)",
    ),
    (
        "prompt_injection",
        r"zapomnij\s+(?:o\s+)?(?:poprzednich|wczesniejszych|wszystkich)\s+"
        r"(?:instrukcjach|poleceniach|wiadomosciach)",
    ),
    (
        "prompt_injection",
        r"nie\s+(?:zwazaj|bierz\s+pod\s+uwage)\s+(?:na\s+)?"
        r"(?:powyzsze|poprzednie|wczesniejsze)",
    ),
    ("prompt_injection", r"(?:udawaj|zachowuj\s+sie)\s+(?:ze\s+jestes|jak|jakbys)"),
    ("prompt_injection", r"od\s+(?:teraz|tej\s+pory)\s+(?:jestes|bedziesz)"),
    ("prompt_injection", r"wciel\s+sie\s+w\s+role"),
    ("prompt_injection", r"tryb\s+(?:dewelopera|deweloperski|programisty)"),
    ("system_prompt_probe", r"system\s*(?:prompt|message)\s*[:=]"),
    ("system_prompt_probe", r"(?:prompt|wiadomosc|komunikat)\s+systemow"),
    (
        "system_prompt_probe",
        r"(?:ujawnij|pokaz|wyswietl|podaj)\s+(?:mi\s+)?(?:swoj|swoje|caly)\s+"
        r"(?:prompt|instrukcj|polecen|wytyczn)",
    ),
    ("special_token", r"<\|.*?\|>"),
    ("code_block", r"```"),
    ("encoded_payload", r"(?:\\x[0-9a-fA-F]{2}){4,}"),
    ("encoded_payload", r"(?:\\u[0-9a-fA-F]{4}){3,}"),
    ("encoded_payload", r"(?:%[0-9a-fA-F]{2}){6,}"),
    ("encoded_payload", r"[0-9a-fA-F]{64,}"),
    ("encoded_payload", r"[A-Za-z0-9+/]{40,}={0,2}"),
]

PROHIBITED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (reason, re.compile(src, re.IGNORECASE | re.DOTALL)) for reason, src in _PATTERN_SOURCES
]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(
        ch
        for ch in text
        if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in "\t\n\r"
    )


def _fold_for_matching(text: str) -> str:
    text = text.replace("ł", "l").replace("Ł", "L")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def run_guardrail(
    user_input: str,
    *,
    min_length: int = DEFAULT_MIN_LENGTH,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> str:
    normalized = normalize_text(user_input).strip()

    if len(normalized) < min_length:
        raise ValidationError("Message must not be empty.")
    if len(normalized) > max_length:
        raise ValidationError(f"Message exceeds the maximum length of {max_length} characters.")

    folded = _fold_for_matching(normalized)
    for reason, pattern in PROHIBITED_PATTERNS:
        if pattern.search(folded):
            raise ValidationError(f"Message rejected by input guardrail: {reason}.")

    return normalized


def validate_chat_message(user_input: str) -> str:
    settings = get_settings()
    return run_guardrail(
        user_input,
        min_length=settings.chat_input_min_length,
        max_length=settings.chat_input_max_length,
    )


class ChatMessageIn(BaseModel):
    message: str = Field(
        description=(
            "User chat message. 1–2000 characters after Unicode normalization also "
            "rejected with 422 if empty, oversized, or matching an input-guardrail "
            "category (prompt_injection, system_prompt_probe, special_token, "
            "code_block, encoded_payload)."
        ),
    )

    @field_validator("message")
    @classmethod
    def _guard(cls, value: str) -> str:
        try:
            return validate_chat_message(value)
        except ValidationError as exc:
            raise ValueError(str(exc.detail)) from exc
