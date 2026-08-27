"""Feedback stays durable and separate from the visible conversation."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api.routers.chat import _feedback_context
from api.schemas.chat import MessageFeedbackRequest

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _statement):
        return SimpleNamespace(all=lambda: self.rows)


def _row(rating: str, note: str, content: str):
    now = datetime.now(UTC)
    message = SimpleNamespace(id=uuid.uuid4(), content=content, created_at=now)
    feedback = SimpleNamespace(rating=rating, note=note)
    return feedback, message


def test_feedback_is_a_separate_message_addressed_context_block():
    liked = _row("up", "Keep the concise table.", "A concise answer")
    disliked = _row("down", "Do not infer missing rates.", "An unsupported rate")

    context = _feedback_context(_Session([liked, disliked]), uuid.uuid4())

    assert f"Message {liked[1].id} was liked." in context
    assert f"Message {disliked[1].id} was disliked." in context
    assert "Keep the concise table." in context
    assert "Do not infer missing rates." in context
    assert "do not mention these notes" in context


def test_feedback_context_is_empty_when_the_session_has_no_notes():
    assert _feedback_context(_Session([]), uuid.uuid4()) == ""


def test_feedback_notes_are_trimmed_and_empty_notes_are_rejected():
    assert MessageFeedbackRequest(rating="up", note="  useful  ").note == "useful"
    with pytest.raises(ValidationError):
        MessageFeedbackRequest(rating="down", note="   ")


def test_feedback_context_bounds_the_answer_but_keeps_the_note():
    row = _row("down", "The correction must remain visible.", "x" * 5000)
    context = _feedback_context(_Session([row]), uuid.uuid4())
    assert len(context) < 2000
    assert "The correction must remain visible." in context
    assert "…" in context

def _request(notes: str, history: list):
    """A real `ModelRequest`, so this tests the middleware and not a stand-in."""
    from langchain.agents.middleware import ModelRequest
    from langchain.messages import SystemMessage
    from langgraph.runtime import Runtime

    return ModelRequest(
        model=None,
        messages=history,
        system_message=SystemMessage("TF26 system prompt."),
        tool_choice=None,
        tools=[],
        response_format=None,
        state={"messages": history},
        runtime=Runtime(
            context={"feedback_notes": notes},
            store=None,
            stream_writer=None,
            previous=None,
        ),
        model_settings={},
    )


def test_the_note_reaches_the_model_as_system_context_not_as_a_chat_turn():
    """The property the whole design rests on.

    A note added to the message history would be summarised away the first time
    the thread was compacted -- and compaction rewrites history, so the note has
    to live somewhere history is not. It goes on the system message, which is
    rebuilt from the database on every model call.
    """
    from langchain.messages import AIMessage, HumanMessage

    from agents.main.feedback import inject_feedback_context

    seen = {}

    def handler(request):
        seen["system"] = request.system_message.text
        seen["messages"] = list(request.messages)
        return AIMessage(content="ok")

    history = [HumanMessage("Konut oranları?")]
    inject_feedback_context.wrap_model_call(
        _request("- Message abc was liked.\n  User note: kısa tut", history), handler
    )

    assert "<user_feedback_notes>" in seen["system"]
    assert "kısa tut" in seen["system"]
    assert "TF26 system prompt." in seen["system"]
    # The note did not become a turn, which is what keeps it out of compaction.
    assert seen["messages"] == history


def test_no_note_means_the_system_message_is_left_alone():
    """A session with no feedback must not gain an empty block."""
    from langchain.messages import AIMessage, HumanMessage

    from agents.main.feedback import inject_feedback_context

    seen = {}

    def handler(request):
        seen["system"] = request.system_message.text
        return AIMessage(content="ok")

    inject_feedback_context.wrap_model_call(
        _request("", [HumanMessage("Konut oranları?")]), handler
    )

    assert seen["system"] == "TF26 system prompt."


def test_compaction_rewrites_history_and_never_the_system_message():
    """An upgrade tripwire.

    The guarantee above holds only because `SummarizationMiddleware` touches
    `messages` and nothing else. If a LangChain release taught it to rewrite the
    system message, feedback notes would start disappearing on compaction and
    nothing else in this suite would notice.
    """
    from langchain.agents.middleware import SummarizationMiddleware

    hooks = [
        name
        for name in ("before_model", "after_model", "wrap_model_call")
        if name in SummarizationMiddleware.__dict__
    ]

    assert hooks == ["before_model"], (
        "SummarizationMiddleware gained a hook that can see the model request; "
        "check whether it can now rewrite the system message"
    )
