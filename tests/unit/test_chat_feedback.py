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
