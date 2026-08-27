"""What "try again" removes before it asks again.

Both halves of a regenerate are here, because they are two halves of one rule --
*the last turn goes, whatever shape it took* -- applied to two stores that would
otherwise disagree. `_drop_last_turn` clears the transcript rows the API replays
as history; `rewind_last_turn` clears the supervisor's own checkpoint, which is
what the agent actually reads once a thread is live. Getting one right and the
other wrong is the failure worth catching: the model would be asked the same
question twice and answer the second one as a follow-up.
"""

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from agents.main import agent as main_agent
from api.agent import rewind_last_turn
from api.routers.chat import _drop_last_turn


# --- the stored transcript ---------------------------------------------------


class _Row:
    """A `ChatMessage` as this function uses one: a role and an identity."""

    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return f"{self.role}:{self.content}"


class _FakeSession:
    """Enough SQLAlchemy session to run the deletion, and a record of it."""

    def __init__(self, rows):
        self.rows = rows
        self.deleted: list[_Row] = []
        self.commits = 0

    def scalars(self, _statement):
        return SimpleNamespace(all=lambda: list(self.rows))

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        self.commits += 1


def test_a_complete_exchange_goes_as_a_pair():
    session = _FakeSession(
        [_Row("user", "q1"), _Row("assistant", "a1"), _Row("user", "q2"), _Row("assistant", "a2")]
    )
    _drop_last_turn(session, uuid.uuid4())
    assert [r.content for r in session.deleted] == ["q2", "a2"]


def test_a_failed_turn_leaves_no_orphan_question():
    """The answer never persisted, so the last row is the question itself.

    This is the case the retry button exists for, and "delete the last two rows"
    would take the previous turn's answer with it.
    """
    session = _FakeSession([_Row("user", "q1"), _Row("assistant", "a1"), _Row("user", "q2")])
    _drop_last_turn(session, uuid.uuid4())
    assert [r.content for r in session.deleted] == ["q2"]


def test_everything_after_the_last_question_goes():
    """A turn that produced more than one assistant row is still one turn."""
    session = _FakeSession(
        [_Row("user", "q1"), _Row("user", "q2"), _Row("assistant", "a1"), _Row("assistant", "a2")]
    )
    _drop_last_turn(session, uuid.uuid4())
    assert [r.content for r in session.deleted] == ["q2", "a1", "a2"]


def test_an_earlier_turn_is_untouched():
    session = _FakeSession([_Row("user", "q1"), _Row("assistant", "a1"), _Row("user", "q2")])
    _drop_last_turn(session, uuid.uuid4())
    assert [r.content for r in session.rows if r not in session.deleted] == ["q1", "a1"]


def test_an_empty_conversation_deletes_nothing_and_commits_nothing():
    session = _FakeSession([])
    _drop_last_turn(session, uuid.uuid4())
    assert session.deleted == []
    assert session.commits == 0


def test_the_deletion_is_committed_before_the_history_is_read():
    """The caller reads history straight after; an open transaction would still
    contain the turn being replaced."""
    session = _FakeSession([_Row("user", "q1"), _Row("assistant", "a1")])
    _drop_last_turn(session, uuid.uuid4())
    assert session.commits == 1


# --- the supervisor's checkpoint ---------------------------------------------


class _FakeAgent:
    def __init__(self, messages):
        self.messages = messages
        self.updates: list = []

    def get_state(self, _config):
        return SimpleNamespace(values={"messages": self.messages})

    def update_state(self, _config, update):
        self.updates.append(update)


@pytest.fixture
def checkpoint(monkeypatch):
    def install(messages):
        agent = _FakeAgent(messages)
        monkeypatch.setattr(main_agent, "build_main_agent", lambda **kwargs: agent)
        return agent

    return install


def _removed(agent) -> list[str]:
    return [
        message.id
        for update in agent.updates
        for message in update["messages"]
        if isinstance(message, RemoveMessage)
    ]


def test_the_whole_last_exchange_is_rewound(checkpoint):
    """From the last question onward -- the tool messages in the middle belong to
    the call that requested them, and outliving it would strand them."""
    agent = checkpoint(
        [
            HumanMessage(content="q1", id="h1"),
            AIMessage(content="a1", id="a1"),
            HumanMessage(content="q2", id="h2"),
            AIMessage(content="", id="a2", tool_calls=[]),
            ToolMessage(content="bank said", tool_call_id="c1", id="t1"),
            AIMessage(content="a2", id="a3"),
        ]
    )
    rewind_last_turn(uuid.uuid4())
    assert _removed(agent) == ["h2", "a2", "t1", "a3"]


def test_an_earlier_exchange_survives(checkpoint):
    agent = checkpoint(
        [
            HumanMessage(content="q1", id="h1"),
            AIMessage(content="a1", id="a1"),
            HumanMessage(content="q2", id="h2"),
            AIMessage(content="a2", id="a2"),
        ]
    )
    rewind_last_turn(uuid.uuid4())
    assert "h1" not in _removed(agent) and "a1" not in _removed(agent)


def test_a_question_whose_answer_never_arrived_is_still_rewound(checkpoint):
    agent = checkpoint([HumanMessage(content="q1", id="h1")])
    rewind_last_turn(uuid.uuid4())
    assert _removed(agent) == ["h1"]


def test_an_empty_checkpoint_is_left_alone(checkpoint):
    """A first turn, or a thread whose checkpoints were cleared. The stored rows
    have already gone, and `history` reseeds it."""
    agent = checkpoint([])
    rewind_last_turn(uuid.uuid4())
    assert agent.updates == []


def test_a_checkpoint_that_cannot_be_read_does_not_fail_the_retry(monkeypatch):
    """Best effort on purpose: the worst case is the model seeing the question
    twice, which is strictly better than the user being unable to ask again."""

    def explode(**_kwargs):
        raise RuntimeError("no checkpointer")

    monkeypatch.setattr(main_agent, "build_main_agent", explode)
    rewind_last_turn(uuid.uuid4())  # does not raise
