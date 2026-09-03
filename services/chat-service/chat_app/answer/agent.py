import logging

from common.observability import start_span
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from chat_app.answer.deps import AnswerDeps
from chat_app.answer.models import select_answer_model
from chat_app.answer.output import AnswerResult
from chat_app.answer.prompts import (
    NO_KNOWLEDGE_REPLY,
    SYSTEM_PROMPT,
    render_answer_prompt,
)
from chat_app.mcp_gateway import NO_KNOWLEDGE_SENTINEL, KnowledgeGraphGateway, is_no_knowledge
from chat_app.settings import ChatSettings
from chat_app.validators import validate_chat_message

logger = logging.getLogger(__name__)

FALLBACK_WARNING = (
    "Odpowiedź wygenerowana bez modelu LLM: nie skonfigurowano żadnego klucza API "
    "(OpenAI/Gemini), więc zwrócono surowy kontekst z grafu wiedzy."
)


def build_answer_agent(
    settings: ChatSettings, *, model: Model | None = None
) -> Agent[AnswerDeps, AnswerResult] | None:
    model = model or select_answer_model(settings)
    if model is None:
        return None

    retrieval_attempts = max(1, settings.answer_kg_retrieval_attempts)

    agent: Agent[AnswerDeps, AnswerResult] = Agent(
        model,
        deps_type=AnswerDeps,
        output_type=AnswerResult,
        system_prompt=SYSTEM_PROMPT,
        model_settings=ModelSettings(temperature=settings.answer_temperature),
        name="answer-agent",
    )

    @agent.tool
    async def knowledge_graph(ctx: RunContext[AnswerDeps], query: str) -> str:
        """Retrieve grounding facts about Wrocław University of Science and Technology.

        Args:
            query: A focused natural-language question to look up in the knowledge graph.

        Returns the retrieved context, or a sentinel string when nothing is found.
        """
        ctx.deps.tool_called = True
        for _ in range(retrieval_attempts):
            with start_span("mcp.knowledge_graph", as_type="tool", input=query):
                result = await ctx.deps.gateway.query(query, trace_id=ctx.deps.trace_id)
            if not is_no_knowledge(result):
                ctx.deps.knowledge_retrieved = True
                return result
        return NO_KNOWLEDGE_SENTINEL

    @agent.output_validator
    def require_grounding(ctx: RunContext[AnswerDeps], output: AnswerResult) -> AnswerResult:
        if not ctx.deps.tool_called:
            raise ModelRetry(
                "You must call the knowledge_graph tool to retrieve context before "
                "answering. Do not answer from prior knowledge."
            )
        return output

    return agent


def fallback_answer(kg_context: str) -> AnswerResult:
    return AnswerResult(answer=kg_context.strip(), warning=FALLBACK_WARNING)


async def generate_answer(
    agent: Agent[AnswerDeps, AnswerResult] | None,
    *,
    question: str,
    history: str,
    gateway: KnowledgeGraphGateway,
    trace_id: str | None = None,
) -> AnswerResult:
    question = validate_chat_message(question)

    if agent is None:
        with start_span("mcp.knowledge_graph", as_type="tool", input=question):
            raw = await gateway.query(question, trace_id=trace_id)
        if is_no_knowledge(raw):
            return AnswerResult(answer=NO_KNOWLEDGE_REPLY)
        return fallback_answer(raw)

    deps = AnswerDeps(gateway=gateway, trace_id=trace_id)
    prompt = render_answer_prompt(question=question, history=history)
    with start_span("answer-llm", as_type="generation", input=question):
        result = await agent.run(prompt, deps=deps)
    if not deps.knowledge_retrieved:
        return AnswerResult(answer=NO_KNOWLEDGE_REPLY)
    return result.output
