import logging
from enum import StrEnum

from pydantic_ai.models import Model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider

from chat_app.settings import ChatSettings

logger = logging.getLogger(__name__)


class AnswerProvider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"


# Auto-selection priority
_AUTO_ORDER: tuple[AnswerProvider, ...] = (
    AnswerProvider.OPENAI,
    AnswerProvider.GEMINI,
)


def _build_model(provider: AnswerProvider, settings: ChatSettings) -> Model | None:
    if provider is AnswerProvider.OPENAI and settings.openai_api_key:
        return OpenAIChatModel(
            settings.answer_openai_model,
            provider=OpenAIProvider(api_key=settings.openai_api_key),
        )
    if provider is AnswerProvider.GEMINI and settings.google_api_key:
        return GoogleModel(
            settings.answer_gemini_model,
            provider=GoogleProvider(api_key=settings.google_api_key),
        )
    return None


def select_answer_model(settings: ChatSettings) -> Model | None:
    configured = settings.answer_model_provider.strip().lower()
    if configured:
        try:
            provider = AnswerProvider(configured)
        except ValueError:
            logger.warning(
                "Unknown answer_model_provider %r; falling back to auto-selection", configured
            )
        else:
            model = _build_model(provider, settings)
            if model is not None:
                logger.info("Answer model provider selected: %s", provider.value)
                return model
            logger.warning(
                "Configured answer provider %r has no API key; trying auto-selection", configured
            )

    for provider in _AUTO_ORDER:
        model = _build_model(provider, settings)
        if model is not None:
            logger.info("Answer model provider selected (auto): %s", provider.value)
            return model

    logger.warning(
        "No LLM API key configured (OpenAI/Gemini): "
        "answer generation will use the raw knowledge-graph fallback"
    )
    return None
