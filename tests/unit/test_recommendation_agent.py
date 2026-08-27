"""The private next-message agent's context and checkpoint contract."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import agents.recommendation.agent as recommendation
from agents.recommendation import ConversationRecommendation

pytestmark = pytest.mark.unit


class _Graph:
    def __init__(self, messages=None, structured=None):
        self.state = SimpleNamespace(
            values={"messages": list(messages or []), "structured_response": structured}
        )
        self.seen = None
        self.config = None

    def get_state(self, config):
        self.config = config
        return self.state

    def invoke(self, payload, *, config):
        self.seen = payload["messages"]
        self.config = config
        return {
            "structured_response": ConversationRecommendation(
                text="Oranları tabloya döker misin?"
            )
        }


def test_missing_checkpoint_is_seeded_with_the_full_visible_conversation(monkeypatch):
    graph = _Graph()
    monkeypatch.setattr(recommendation, "build_recommendation_agent", lambda: graph)

    result = recommendation.generate_recommendation(
        [
            ("u1", "user", "Güncel oranları bul."),
            ("a1", "assistant", "Üç bankanın oranlarını buldum."),
        ],
        session_id="session-1",
        locale="tr",
    )

    assert result.text == "Oranları tabloya döker misin?"
    assert [type(message) for message in graph.seen] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]
    assert graph.seen[0].content == "Güncel oranları bul."
    assert graph.seen[-1].additional_kwargs[recommendation._MARKER] == "a1"
    assert "Turkish" in graph.seen[-1].content
    assert graph.config["configurable"]["thread_id"] == "session-1:recommendation"


def test_existing_checkpoint_receives_only_turns_after_its_marker(monkeypatch):
    marker = HumanMessage(
        "recommend",
        additional_kwargs={
            recommendation._MARKER: "a1",
            recommendation._LOCALE: "en",
        },
    )
    graph = _Graph(messages=[marker, AIMessage("old recommendation")])
    monkeypatch.setattr(recommendation, "build_recommendation_agent", lambda: graph)

    recommendation.generate_recommendation(
        [
            ("u1", "user", "First"),
            ("a1", "assistant", "First answer"),
            ("u2", "user", "Second"),
            ("a2", "assistant", "Second answer"),
        ],
        session_id="session-1",
        locale="en",
    )

    assert [message.content for message in graph.seen[:-1]] == [
        "Second",
        "Second answer",
    ]
    assert "English" in graph.seen[-1].content


def test_same_completed_turn_reuses_the_checkpointed_recommendation(monkeypatch):
    expected = ConversationRecommendation(text="Kaynakları karşılaştırabilir misin?")
    marker = HumanMessage(
        "recommend",
        additional_kwargs={
            recommendation._MARKER: "a1",
            recommendation._LOCALE: "tr",
        },
    )
    graph = _Graph(messages=[marker], structured=expected)
    monkeypatch.setattr(recommendation, "build_recommendation_agent", lambda: graph)

    result = recommendation.generate_recommendation(
        [("u1", "user", "First"), ("a1", "assistant", "Answer")],
        session_id="session-1",
        locale="tr",
    )

    assert result is expected
    assert graph.seen is None


def test_locale_change_regenerates_the_same_turn_in_the_new_language(monkeypatch):
    marker = HumanMessage(
        "recommend",
        additional_kwargs={
            recommendation._MARKER: "a1",
            recommendation._LOCALE: "tr",
        },
    )
    graph = _Graph(
        messages=[marker],
        structured=ConversationRecommendation(text="Türkçe öneri"),
    )
    monkeypatch.setattr(recommendation, "build_recommendation_agent", lambda: graph)

    recommendation.generate_recommendation(
        [("u1", "user", "First"), ("a1", "assistant", "Answer")],
        session_id="session-1",
        locale="en",
    )

    assert graph.seen is not None
    assert "English" in graph.seen[-1].content


@pytest.mark.parametrize(
    "conversation",
    [[], [("u1", "user", "Still waiting for an answer")]],
)
def test_recommendation_requires_a_completed_assistant_turn(monkeypatch, conversation):
    monkeypatch.setattr(
        recommendation, "build_recommendation_agent", lambda: pytest.fail("not built")
    )
    with pytest.raises(ValueError, match="completed assistant"):
        recommendation.generate_recommendation(
            conversation, session_id="session-1", locale="en"
        )
