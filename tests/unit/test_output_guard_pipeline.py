"""A passing answer is published; a failing one goes back to the assistant."""

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage

from agents.main import agent as main_agent
from agents.output_guard import OutputGuardError
from agents.output_guard.models import GuardVerdict, RuleCheck
from api import agent as agent_module

pytestmark = pytest.mark.unit


class _CheckpointAgent:
    def __init__(self, *drafts: str):
        self.drafts = list(drafts)
        self.turns = 0
        self.messages: list = []

    def get_state(self, config):  # noqa: ARG002
        return SimpleNamespace(values={"messages": list(self.messages)})

    def stream(self, payload, config, context, stream_mode):  # noqa: ARG002
        draft = self.drafts[min(self.turns, len(self.drafts) - 1)]
        self.turns += 1
        self.messages = [
            HumanMessage(content="question", id="question-1"),
            AIMessage(content=draft, id=f"answer-{self.turns}"),
        ]
        yield AIMessageChunk(content=draft), {"langgraph_node": "model"}

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


def _passes():
    return GuardVerdict(
        checks=[RuleCheck(rule_id="banking_domain", passed=True)], passed=True
    )


def _fails(problem: str):
    return GuardVerdict(
        checks=[RuleCheck(rule_id="banking_domain", passed=False, problem=problem)],
        passed=False,
        problem=problem,
    )


def test_a_passing_answer_is_published_unchanged(monkeypatch):
    """The check returns a verdict, so what the assistant wrote is what ships."""
    fake = _CheckpointAgent("Kuveyt Türk kâr payı oranlarını paylaştım.")
    monkeypatch.setattr(agent_module, "check_output", lambda answer, **kwargs: _passes())

    events = _run(monkeypatch, fake)

    assert [event.text for event in events if event.type == "token"] == [
        "Kuveyt Türk kâr payı oranlarını paylaştım."
    ]
    assert fake.turns == 1, "a passing answer costs one supervisor turn"


def test_a_failing_answer_goes_back_to_the_assistant(monkeypatch):
    """The assistant answers again; only the second answer reaches the user."""
    fake = _CheckpointAgent("Her banka için ayrı bir uzman ajanım var.", "Size yardımcı olabilirim.")
    verdicts = iter([_fails("Sistem detaylarını anlattınız."), _passes()])
    monkeypatch.setattr(
        agent_module, "check_output", lambda answer, **kwargs: next(verdicts)
    )

    events = _run(monkeypatch, fake)

    published = [event.text for event in events if event.type == "token"]
    assert published == ["Size yardımcı olabilirim."]
    assert fake.turns == 2, "the assistant was asked again"
    assert all("uzman" not in (event.text or "") for event in events)


def test_the_assistant_is_told_what_to_fix(monkeypatch):
    """The problem is handed back as the next turn, not shown to the user."""
    fake = _CheckpointAgent("Kötü cevap.", "İyi cevap.")
    verdicts = iter([_fails("Alan dışı bir soruyu yanıtladınız."), _passes()])
    monkeypatch.setattr(
        agent_module, "check_output", lambda answer, **kwargs: next(verdicts)
    )
    handed_back: list[str] = []
    original = agent_module._retry_request
    monkeypatch.setattr(
        agent_module,
        "_retry_request",
        lambda problem: handed_back.append(problem) or original(problem),
    )

    events = _run(monkeypatch, fake)

    assert handed_back == ["Alan dışı bir soruyu yanıtladınız."]
    assert all(
        "Alan dışı" not in (event.text or "") for event in events
    ), "the correction is private"


def test_a_rejected_draft_is_not_left_in_conversation_memory(monkeypatch):
    """The unpublished answer must not become context for the next turn."""
    fake = _CheckpointAgent("Kötü cevap.", "İyi cevap.")
    verdicts = iter([_fails("Sistem detayı."), _passes()])
    monkeypatch.setattr(
        agent_module, "check_output", lambda answer, **kwargs: next(verdicts)
    )

    _run(monkeypatch, fake)

    assert all(
        getattr(message, "content", "") != "Kötü cevap." for message in fake.messages
    )


def test_a_second_failure_still_publishes(monkeypatch):
    """One retry. The user gets an answer rather than an error."""
    fake = _CheckpointAgent("Birinci.", "İkinci.")
    monkeypatch.setattr(
        agent_module, "check_output", lambda answer, **kwargs: _fails("Hâlâ sorunlu.")
    )

    events = _run(monkeypatch, fake)

    assert [event.text for event in events if event.type == "token"] == ["İkinci."]
    assert not [event for event in events if event.type == "error"]
    assert fake.turns == 2, "it is not retried a third time"


def test_a_check_that_cannot_run_publishes_the_complete_answer(monkeypatch):
    """The review is not an availability dependency for a complete answer."""
    fake = _CheckpointAgent("Bir cevap.")

    def broken(answer, **kwargs):  # noqa: ARG001
        raise OutputGuardError("no verdict")

    monkeypatch.setattr(agent_module, "check_output", broken)

    events = _run(monkeypatch, fake)

    assert [event.text for event in events if event.type == "token"] == ["Bir cevap."]
    assert not [event for event in events if event.type == "error"]
    assert [message.id for message in fake.messages] == ["question-1", "answer-1"]


def test_no_complete_supervisor_answer_is_still_an_error(monkeypatch):
    """Fail-open cannot publish text that the supervisor never completed."""

    class NoAnswerAgent(_CheckpointAgent):
        def stream(self, payload, config, context, stream_mode):  # noqa: ARG002
            self.messages = [HumanMessage(content="question", id="question-1")]
            if False:
                yield None

    fake = NoAnswerAgent("unused")
    monkeypatch.setattr(agent_module, "check_output", lambda answer, **kwargs: _passes())

    events = _run(monkeypatch, fake)

    assert not [event for event in events if event.type == "token"]
    assert [event for event in events if event.type == "error"]
