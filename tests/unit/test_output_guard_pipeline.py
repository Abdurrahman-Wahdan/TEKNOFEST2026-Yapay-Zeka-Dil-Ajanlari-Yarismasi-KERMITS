"""The guarded answer is the one published and retained by LangGraph."""

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage

from agents.main import agent as main_agent
from agents.output_guard import OutputGuardError
from agents.output_guard.models import GuardedOutput
from api import agent as agent_module

pytestmark = pytest.mark.unit


class _CheckpointAgent:
    def __init__(self, draft: str):
        self.draft = draft
        self.messages: list = []

    def get_state(self, config):  # noqa: ARG002
        return SimpleNamespace(values={"messages": list(self.messages)})

    def stream(self, payload, config, context, stream_mode):  # noqa: ARG002
        self.messages = [
            HumanMessage(content="question", id="question-1"),
            AIMessage(content=self.draft, id="answer-1"),
        ]
        yield AIMessageChunk(content=self.draft), {"langgraph_node": "model"}

    def update_state(self, config, update):  # noqa: ARG002
        for incoming in update.get("messages", []):
            if isinstance(incoming, RemoveMessage):
                self.messages = [
                    message for message in self.messages if message.id != incoming.id
                ]
                continue
            self.messages = [
                incoming if message.id == incoming.id else message
                for message in self.messages
            ]


def _run(monkeypatch, fake: _CheckpointAgent):
    monkeypatch.setattr(main_agent, "build_main_agent", lambda **kwargs: fake)
    return list(agent_module._agent_answer(
        "question",
        history=None,
        context=None,
        captures=None,
        tool_results=None,
        session_id=uuid.uuid4(),
    ))


def test_guarded_text_is_public_and_replaces_the_checkpoint(monkeypatch):
    fake = _CheckpointAgent("I asked the internal tool for the answer.")
    monkeypatch.setattr(
        agent_module,
        "guard_output",
        lambda text, **kwargs: GuardedOutput(
            text="I checked the available sources for the answer.",
            changed=True,
            checks=[],
            patches=[],
            passes=2,
        ),
    )

    events = _run(monkeypatch, fake)

    assert [event.text for event in events if event.type == "token"] == [
        "I checked the available sources for the answer."
    ]
    assert fake.messages[-1].content == "I checked the available sources for the answer."
    assert all("internal tool" not in (event.text or "") for event in events)


def test_rejected_draft_is_neither_published_nor_left_in_memory(monkeypatch):
    fake = _CheckpointAgent("Private implementation detail.")

    def reject(text, **kwargs):  # noqa: ARG001
        raise OutputGuardError("invalid patch")

    monkeypatch.setattr(agent_module, "guard_output", reject)

    events = _run(monkeypatch, fake)

    assert not [event for event in events if event.type == "token"]
    assert [event for event in events if event.type == "error"]
    assert [message.id for message in fake.messages] == ["question-1"]
