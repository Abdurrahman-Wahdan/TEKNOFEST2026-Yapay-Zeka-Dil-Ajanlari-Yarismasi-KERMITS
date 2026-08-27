"""The accepted answer reaches the browser in pieces, and says when it is coming.

Two separate promises, and only the first one is about correctness:

- **The pieces reassemble into the answer, exactly.** `api/routers/chat.py`
  concatenates every `token` frame and stores the result as the assistant's turn,
  and the browser concatenates the same frames for the visible one. A chunker
  that normalised so much as a run of spaces would put three different texts in
  the transcript, the database and the model's next-turn history.
- **The turn narrates itself.** `status` frames say which part of the turn is
  running. They are advisory -- nothing about the answer depends on them -- but
  without them the panel shows one spinner for a wait measured at 79 seconds.

What is deliberately *not* promised: that prose reaches the user while the model
is still writing it. The output check runs on the finished answer and can hand
the turn back, so nothing is published until it has passed. `test_no_prose_..._`
below pins that, because it is the property a future "stream it live" change
would have to knowingly break rather than lose by accident.
"""

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agents.main import agent as main_agent
from agents.output_guard.models import GuardVerdict, RuleCheck
from api import agent as agent_module
from api.agent import ANSWER_CHUNK_CHARS, _agent_answer, _answer_chunks

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def pass_public_output_guard(monkeypatch):
    monkeypatch.setattr(
        agent_module,
        "check_output",
        lambda answer, **kwargs: GuardVerdict(
            checks=[RuleCheck(rule_id="banking_domain", passed=True)], passed=True
        ),
    )


# --- the chunker -----------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "a",
        "Merhaba! İyiyim, teşekkür ederim.",
        # A comparison table: the shape most answers actually take, and the one
        # where a mid-token split shows a stray pipe or asterisk for a paint.
        "| Banka | Oran |\n| :--- | ---: |\n| **Dünya Katılım** | %1,65 |\n",
        # Whitespace that a split-and-rejoin would quietly normalise.
        "   leading and trailing   \n\n\n   ",
        "tek" + " " * 200 + "kelime",
        # Longer than one chunk with no break in it at all.
        "https://www.kuveytturk.com.tr/" + "a" * 400,
        "\n".join(f"satır {i} kâr payı oranı %1,{i:02d}" for i in range(60)),
    ],
)
def test_chunks_reassemble_into_exactly_the_answer(answer):
    assert "".join(_answer_chunks(answer)) == answer


def test_no_chunk_is_empty():
    """An empty `token` frame renders nothing and costs a re-render."""
    assert all(_answer_chunks("kâr payı oranı %1,65 olarak açıklandı" * 20))


def test_a_run_longer_than_the_chunk_size_is_emitted_whole():
    """Better one oversized frame than a URL cut in half mid-render."""
    url = "https://www.vakifkatilim.com.tr/" + "x" * (ANSWER_CHUNK_CHARS * 2)
    pieces = list(_answer_chunks(f"Kaynak: {url} son."))
    # In its own frame, whole. The trailing space rides along with it: a run of
    # whitespace never forces a split, so it joins whatever frame precedes it.
    assert any(piece.strip() == url for piece in pieces)


def test_words_are_not_split_across_frames():
    """Every intermediate state stays plausible prose while markdown re-parses."""
    answer = " ".join(f"kelime{i}" for i in range(200))
    # Every prefix the renderer is handed must end on a boundary, never inside a
    # word -- that is what lets it paint an intermediate state without a torn
    # token.
    pieces = list(_answer_chunks(answer))
    for index in range(len(pieces) - 1):
        prefix = "".join(pieces[: index + 1])
        assert prefix == answer[: len(prefix)]
        assert prefix.endswith(" ") or answer[len(prefix)].isspace()


# --- the stream ------------------------------------------------------------


ANSWER = " ".join(f"Kâr payı oranı %1,{i:02d} olarak açıklanmıştır." for i in range(12))


class _TwoTurnAgent:
    """Asks one bank, then writes. The shape every real tool-using turn has."""

    def __init__(self, answer: str = ANSWER):
        self.answer = answer
        self.final: AIMessage | None = None

    def get_state(self, config):
        return SimpleNamespace(values={"messages": [self.final] if self.final else []})

    def stream(self, payload, config, context, stream_mode):
        # Turn one: a tool call and no prose. Measured against the running model
        # across five query shapes -- a supervisor turn is never both.
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"name": "ask_vakif", "args": "{}", "id": "c1", "index": 0}
            ],
        ), {"langgraph_node": "model"}
        yield ToolMessage(
            name="ask_vakif", tool_call_id="c1", content="Specialist answer"
        ), {"langgraph_node": "tools"}
        # Turn two: the answer, arriving as the model writes it.
        self.final = AIMessage(content=self.answer, id="final")
        for start in range(0, len(self.answer), 7):
            yield AIMessageChunk(content=self.answer[start : start + 7]), {
                "langgraph_node": "model"
            }


def _run(agent, monkeypatch):
    monkeypatch.setattr(main_agent, "build_main_agent", lambda **kwargs: agent)
    return list(
        _agent_answer(
            "Vakıf Katılım'ın altın kâr payı oranı nedir?",
            None,
            None,
            None,
            None,
            session_id=uuid.uuid4(),
        )
    )


def test_the_answer_arrives_in_several_frames_that_rebuild_it(monkeypatch):
    events = _run(_TwoTurnAgent(), monkeypatch)
    tokens = [event for event in events if event.type == "token"]
    assert len(tokens) > 1, "a long answer arriving in one frame is the old behaviour"
    assert "".join(event.text or "" for event in tokens) == ANSWER


def test_the_turn_reports_pricing_then_writing_then_reviewing(monkeypatch):
    events = _run(_TwoTurnAgent(), monkeypatch)
    stages = [event.stage for event in events if event.type == "status"]
    assert stages == ["pricing", "writing", "reviewing"]


def test_writing_is_announced_before_the_first_answer_frame(monkeypatch):
    """The label has to lead the text, or it never gets shown at all."""
    events = _run(_TwoTurnAgent(), monkeypatch)
    kinds = [
        event.type if event.type != "status" else f"status:{event.stage}"
        for event in events
    ]
    assert kinds.index("status:writing") < kinds.index("token")


def test_no_prose_crosses_the_boundary_before_the_output_check(monkeypatch):
    """The check stays in front of the reader.

    The agent below streams its prose and the guard is asked afterwards, so a
    `token` frame appearing before `status:reviewing` would mean the user saw
    text the check had not yet judged. Deliberate behaviour, pinned here because
    it is exactly what an optimistic-streaming change would have to change on
    purpose.
    """
    events = _run(_TwoTurnAgent(), monkeypatch)
    first_token = next(i for i, e in enumerate(events) if e.type == "token")
    reviewing = next(
        i for i, e in enumerate(events) if e.type == "status" and e.stage == "reviewing"
    )
    assert reviewing < first_token


def test_a_short_answer_still_arrives(monkeypatch):
    """One frame is correct for an answer shorter than one chunk."""
    events = _run(_TwoTurnAgent("Merhaba!"), monkeypatch)
    assert [e.text for e in events if e.type == "token"] == ["Merhaba!"]
