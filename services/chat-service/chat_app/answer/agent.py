import logging

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from chat_app.answer.models import select_answer_model
from chat_app.answer.output import AnswerResult
from chat_app.answer.prompts import SYSTEM_PROMPT
from chat_app.settings import ChatSettings

logger = logging.getLogger(__name__)

FALLBACK_WARNING = (
    "Odpowiedź wygenerowana bez modelu LLM: nie skonfigurowano żadnego klucza API "
    "(OpenAI/Gemini), więc zwrócono surowy kontekst z grafu wiedzy."
)


def build_answer_agent(settings: ChatSettings) -> Agent[None, AnswerResult] | None:
    model = select_answer_model(settings)
    if model is None:
        return None

    return Agent(
        model,
        output_type=AnswerResult,
        system_prompt=SYSTEM_PROMPT,
        model_settings=ModelSettings(temperature=settings.answer_temperature),
        name="answer-agent",
    )


def fallback_answer(kg_context: str) -> AnswerResult:
    return AnswerResult(answer=kg_context.strip(), warning=FALLBACK_WARNING)
