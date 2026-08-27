"""The voice formatter is isolated, structured, and fact-preserving by contract."""

import json

import pytest
from langchain.agents.structured_output import ToolStrategy

from agents.voice_response import agent as voice_agent
from agents.voice_response.models import VoiceResponse
from api.routers import voice as voice_router
from api.schemas.voice import VoiceResponseRequest

pytestmark = pytest.mark.unit


def test_agent_uses_one_shot_structured_output(monkeypatch):
    captured = {}
    graph = object()

    monkeypatch.setattr(voice_agent, "get_llm", lambda role, **kwargs: (role, kwargs))

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return graph

    monkeypatch.setattr(voice_agent, "create_agent", fake_create_agent)

    assert voice_agent.build_voice_response_agent() is graph
    assert captured["model"] == (
        "chat",
        {"thinking": False, "disable_streaming": True},
    )
    assert captured["tools"] == []
    assert captured["name"] == "voice_response_formatter"
    assert isinstance(captured["response_format"], ToolStrategy)
    assert captured["response_format"].schema is VoiceResponse
    assert "checkpointer" not in captured


def test_generation_passes_question_answer_and_language_as_data(monkeypatch):
    expected = VoiceResponse(
        text="Kuveyt Türk için oran yüzde iki virgül altmış dokuzdur."
    )

    class Graph:
        def invoke(self, payload):
            raw = payload["messages"][0][1].split("\n", 1)[1]
            data = json.loads(raw)
            assert data == {
                "language": "Turkish",
                "user_question": "Oran nedir?",
                "assistant_answer": "| Banka | Oran |\n|---|---|\n| Kuveyt Türk | %2,69 |",
            }
            return {"structured_response": expected}

    monkeypatch.setattr(voice_agent, "build_voice_response_agent", lambda: Graph())

    assert voice_agent.format_voice_response(
        "| Banka | Oran |\n|---|---|\n| Kuveyt Türk | %2,69 |",
        question="Oran nedir?",
        locale="tr-TR",
    ) == expected


def test_endpoint_returns_the_formatter_result(monkeypatch):
    seen = {}

    def fake_format(answer, *, question, locale):
        seen.update(answer=answer, question=question, locale=locale)
        return VoiceResponse(text="Spoken answer.")

    monkeypatch.setattr(voice_router, "format_voice_response", fake_format)
    body = VoiceResponseRequest(
        answer="**Answer**", question="Question?", locale="en"
    )

    result = voice_router.create_voice_response(body, object())

    assert result.text == "Spoken answer."
    assert seen == {
        "answer": "**Answer**",
        "question": "Question?",
        "locale": "en",
    }


def test_generation_rejects_empty_answer():
    with pytest.raises(ValueError, match="completed assistant answer"):
        voice_agent.format_voice_response("  ")
