import pytest
from chat_app.answer import (
    FALLBACK_WARNING,
    AnswerProvider,
    AnswerResult,
    build_answer_agent,
    fallback_answer,
    render_answer_prompt,
    select_answer_model,
)
from chat_app.settings import ChatSettings
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

pytestmark = pytest.mark.unit


def _settings(**keys: str) -> ChatSettings:
    defaults = {"openai_api_key": "", "clarin_api_key": "", "google_api_key": ""}
    defaults.update(keys)
    return ChatSettings(**defaults)


def test_openai_is_preferred_when_multiple_keys_present() -> None:
    model = select_answer_model(
        _settings(openai_api_key="ok", clarin_api_key="ck", google_api_key="gk")
    )
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-5.4-mini"


def test_gemini_selected_when_only_google_key() -> None:
    model = select_answer_model(_settings(google_api_key="gk"))
    assert isinstance(model, GoogleModel)
    assert model.model_name == "gemini-2.5-flash-lite"


def test_explicit_provider_overrides_auto_priority() -> None:
    model = select_answer_model(
        _settings(
            answer_model_provider="gemini",
            openai_api_key="ok",
            google_api_key="gk",
        )
    )
    assert isinstance(model, GoogleModel)


def test_unknown_configured_provider_falls_back_to_auto() -> None:
    model = select_answer_model(
        _settings(answer_model_provider="bogus", openai_api_key="ok")
    )
    assert isinstance(model, OpenAIChatModel)


def test_no_keys_returns_none() -> None:
    assert select_answer_model(_settings()) is None


def test_answer_provider_enum_values() -> None:
    assert {p.value for p in AnswerProvider} == {"openai", "gemini"}


def test_build_agent_returns_none_without_keys() -> None:
    assert build_answer_agent(_settings()) is None


def test_fallback_returns_raw_kg_summary_and_warning() -> None:
    result = fallback_answer("  Kolokwium odbędzie się w sali 301.  ")
    assert isinstance(result, AnswerResult)
    assert result.answer == "Kolokwium odbędzie się w sali 301."
    assert result.warning == FALLBACK_WARNING


def test_agent_produces_typed_output_with_test_model() -> None:
    agent = build_answer_agent(_settings(openai_api_key="test"))
    assert agent is not None

    with agent.override(model=TestModel()):
        result = agent.run_sync(
            render_answer_prompt(question="Gdzie jest sala 301?", kg_context="Sala 301: C-16.")
        )

    assert isinstance(result.output, AnswerResult)
    assert isinstance(result.output.answer, str)


def test_agent_deterministic_output_via_custom_args() -> None:
    agent = build_answer_agent(_settings(openai_api_key="test"))
    assert agent is not None

    canned = AnswerResult(answer="Sala 301 znajduje się w budynku C-16.", warning=None)
    with agent.override(model=TestModel(custom_output_args=canned.model_dump())):
        result = agent.run_sync(
            render_answer_prompt(question="Gdzie jest sala 301?", kg_context="Sala 301: C-16.")
        )

    assert result.output.answer == "Sala 301 znajduje się w budynku C-16."
    assert result.output.warning is None


def test_agent_with_function_model_receives_prompt() -> None:
    captured: dict[str, str] = {}

    def respond(messages, info: AgentInfo) -> ModelResponse:
        user_text = messages[-1].parts[-1].content
        captured["prompt"] = user_text
        output_tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=output_tool.name,
                    args={"answer": "grounded", "warning": None},
                )
            ]
        )

    agent = build_answer_agent(_settings(openai_api_key="test"))
    assert agent is not None

    with agent.override(model=FunctionModel(respond)):
        result = agent.run_sync(
            render_answer_prompt(question="Kto prowadzi wykład?", kg_context="Wykład: dr Kowalski.")
        )

    assert result.output.answer == "grounded"
    assert "dr Kowalski" in captured["prompt"]
    assert "Kto prowadzi wykład?" in captured["prompt"]


def test_prompt_includes_context_and_question() -> None:
    prompt = render_answer_prompt(question="Pytanie?", kg_context="Kontekst KG.")
    assert "Kontekst KG." in prompt
    assert "Pytanie?" in prompt
    assert "Conversation so far:" not in prompt


def test_prompt_injects_history_when_present() -> None:
    prompt = render_answer_prompt(
        question="A teraz?",
        kg_context="Kontekst.",
        history="user: czesc\nassistant: hej",
    )
    assert "Conversation so far:" in prompt
    assert "user: czesc" in prompt


def test_prompt_placeholder_when_context_empty() -> None:
    prompt = render_answer_prompt(question="Pytanie?", kg_context="   ")
    assert "no knowledge-graph context" in prompt
