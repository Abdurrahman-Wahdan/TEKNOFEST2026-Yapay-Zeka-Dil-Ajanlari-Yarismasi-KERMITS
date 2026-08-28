"""The spoken-answer writer restates one answer. It never answers, and never edits facts."""

import pytest
from fastapi import HTTPException
from langchain.agents.structured_output import ToolStrategy

from agents.voice_response import agent as voice
from agents.voice_response.models import VoiceResponse
from api.routers import voice as route
from api.schemas.voice import VoiceResponseRequest
from config.settings import settings

pytestmark = pytest.mark.unit

ANSWER = (
    "## Kuveyt Türk\n\n"
    "| Vade | Kâr payı |\n| --- | --- |\n| 32 gün | %2,69 |\n\n"
    "Detay icin [urun sayfasi](https://www.kuveytturk.com.tr/x)."
)
SPOKEN = "Kuveyt Türk'te 32 gün vadede kâr payı yüzde 2,69."


class _FakeWriter:
    """Stands in for the built agent, and records the prompt it was given."""

    def __init__(self, result, seen: list[str]):
        self._result = result
        self._seen = seen

    def invoke(self, payload):
        self._seen.append(payload["messages"][0][1])
        return self._result


def _use(monkeypatch, result) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(
        voice, "build_voice_response_agent", lambda: _FakeWriter(result, seen)
    )
    return seen


def test_agent_is_built_stateless_on_the_configured_model(monkeypatch):
    captured: dict = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(voice, "get_llm", lambda role, **kwargs: (role, kwargs))
    monkeypatch.setattr(voice, "create_agent", fake_create_agent)

    voice.build_voice_response_agent()

    assert captured["model"] == (
        settings.VOICE_RESPONSE_MODEL,
        {
            "thinking": False,
            "disable_streaming": True,
            "max_tokens": settings.VOICE_RESPONSE_MAX_TOKENS,
        },
    )
    assert captured["tools"] == []
    assert captured["name"] == "voice_response"
    assert isinstance(captured["response_format"], ToolStrategy)
    assert captured["response_format"].schema is VoiceResponse
    # No thread, so it cannot start answering instead of restating.
    assert "checkpointer" not in captured


def test_the_answer_and_the_question_both_reach_the_writer(monkeypatch):
    seen = _use(monkeypatch, {"structured_response": VoiceResponse(speech=SPOKEN)})

    result = voice.shape_for_speech(ANSWER, question="Kuveyt Türk kâr payı ne kadar?")

    assert result.speech == SPOKEN
    assert len(seen) == 1
    assert "%2,69" in seen[0]
    assert "Kuveyt Türk kâr payı ne kadar?" in seen[0]


def test_an_unvalidated_result_is_refused_rather_than_spoken(monkeypatch):
    _use(monkeypatch, {"structured_response": None})

    with pytest.raises(RuntimeError, match="no validated result"):
        voice.shape_for_speech(ANSWER)


def test_an_empty_answer_never_reaches_the_model(monkeypatch):
    seen = _use(monkeypatch, {"structured_response": VoiceResponse(speech=SPOKEN)})

    with pytest.raises(ValueError, match="no answer"):
        voice.shape_for_speech("   \n ")

    assert seen == []


def test_surrounding_whitespace_is_stripped_from_the_passage():
    assert VoiceResponse(speech=f"  {SPOKEN}\n").speech == SPOKEN


def test_the_route_returns_the_rewritten_passage(monkeypatch):
    _use(monkeypatch, {"structured_response": VoiceResponse(speech=SPOKEN)})

    out = route.create_voice_response(
        VoiceResponseRequest(text=ANSWER, question="ne kadar?"), object()
    )

    assert out.speech == SPOKEN


def test_the_route_refuses_an_answer_that_is_only_whitespace():
    with pytest.raises(HTTPException) as caught:
        route.create_voice_response(VoiceResponseRequest(text="   "), object())

    assert caught.value.status_code == 422


def test_an_over_long_answer_is_refused_and_not_truncated(monkeypatch):
    seen = _use(monkeypatch, {"structured_response": VoiceResponse(speech=SPOKEN)})
    oversized = "a" * (settings.VOICE_RESPONSE_MAX_INPUT_CHARS + 1)

    with pytest.raises(HTTPException) as caught:
        route.create_voice_response(VoiceResponseRequest(text=oversized), object())

    assert caught.value.status_code == 422
    assert seen == []


def test_a_writer_that_fails_becomes_the_503_that_means_shape_it_yourself(monkeypatch):
    def explode():
        raise RuntimeError("tunnel gone")

    monkeypatch.setattr(voice, "build_voice_response_agent", explode)

    with pytest.raises(HTTPException) as caught:
        route.create_voice_response(VoiceResponseRequest(text=ANSWER), object())

    assert caught.value.status_code == 503
