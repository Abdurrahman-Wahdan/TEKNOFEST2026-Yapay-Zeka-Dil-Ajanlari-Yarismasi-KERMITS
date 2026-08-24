"""The overview agent, and the cache that keeps it from being asked twice.

No database and no model. The agent graph is patched to a fake that records the
turn it was handed, and the cache is exercised against a fake session, so what
these tests pin is everything except the one thing that cannot be pinned from
here: whether Gemma 4 actually reads the screenshot and ranks the banks
sensibly. The vLLM host is unreachable, so that stays unverified.
"""

import pytest

import agents.table_overview.agent as agent_mod
from agents.table_overview import TableOverview
from api import table_overviews as overviews
from config.settings import settings

pytestmark = pytest.mark.unit

TABLE = {
    "id": "konut-finansmani",
    "title": "Konut Finansmanı",
    "columns": [{"key": "Banka", "label": "Banka"}],
    "rows": [{"cells": {"Banka": "Kuveyt Türk"}}],
}
RESULT = TableOverview(summary="Bir özet.", recommended=[], not_recommended=[], caveat="")


class _Graph:
    """Stands in for the compiled agent; keeps the messages it was invoked with."""

    def __init__(self, result=RESULT):
        self.result = result
        self.calls = 0
        self.seen = None

    def invoke(self, payload):
        self.calls += 1
        self.seen = payload["messages"]
        return {"structured_response": self.result}


OUTLINE = '<page-snapshot path="/urunler">\n## On screen\n| Banka |\n| Kuveyt Türk |\n</page-snapshot>'


def _content(monkeypatch, page_text=OUTLINE, **kwargs) -> str:
    graph = _Graph()
    monkeypatch.setattr(agent_mod, "build_table_overview_agent", lambda: graph)
    agent_mod.generate_table_overview(page_text, **kwargs)
    return graph.seen[0].content


def test_the_turn_carries_the_outline_and_the_language(monkeypatch):
    text = _content(monkeypatch, locale="en")
    assert "Write the overview in English." in text
    assert "<page-snapshot" in text
    assert "Kuveyt Türk" in text


def test_the_turn_is_text_only(monkeypatch):
    # No screenshot and no table JSON. The picture cost minutes of vision
    # prefill per table and carried nothing the outline does not; the JSON was
    # the same figures a second time, in a second spelling.
    text = _content(monkeypatch)
    assert isinstance(text, str)
    assert text.count("<page-snapshot") == 1


def test_an_overview_without_the_outline_is_refused(monkeypatch):
    # The outline is the only source there is.
    monkeypatch.setattr(agent_mod, "build_table_overview_agent", lambda: _Graph())
    for empty in ("", "   "):
        with pytest.raises(ValueError):
            agent_mod.generate_table_overview(empty)


def test_an_unvalidated_answer_is_an_error_not_an_empty_overview(monkeypatch):
    class Empty:
        def invoke(self, payload):
            return {}

    monkeypatch.setattr(agent_mod, "build_table_overview_agent", lambda: Empty())
    with pytest.raises(RuntimeError):
        agent_mod.generate_table_overview(OUTLINE)


def test_the_agent_waits_for_the_model_as_long_as_the_chat_does(monkeypatch):
    # A shorter retry window was tried here and it was the wrong lever. The
    # host disconnects a slow request *during prefill*, before any chunk
    # arrives, and the answer to that is the chat's answer: retry. Measured on
    # one busy host, the chat succeeded on attempt 3 at the same moment this
    # gave up on attempt 2.
    seen = {}
    monkeypatch.setattr(agent_mod, "get_llm", lambda role, **kwargs: seen.update(role=role, **kwargs) or object())
    monkeypatch.setattr(agent_mod, "create_agent", lambda **kwargs: object())
    agent_mod.build_table_overview_agent()

    assert seen["role"] == "chat"
    assert "retry_window" not in seen


def test_the_agent_streams_so_the_tunnel_does_not_close_on_it(monkeypatch):
    # A screenshot means a minute or more of vision prefill before the model
    # emits anything, and the tunnel in front of the host closes a connection
    # that silent at about two minutes -- measured at 119s and 139s, both
    # "Server disconnected without sending a response". The chat has never hit
    # this because it has always streamed.
    seen = {}
    monkeypatch.setattr(agent_mod, "get_llm", lambda role, **kwargs: seen.update(kwargs) or object())
    monkeypatch.setattr(agent_mod, "create_agent", lambda **kwargs: object())
    agent_mod.build_table_overview_agent()

    assert seen["streaming"] is True
    assert "disable_streaming" not in seen


def test_the_hash_follows_the_content_not_the_id():
    # The id survives the producer rewriting every figure under it, which is
    # exactly the case the cache has to notice.
    changed = {**TABLE, "rows": [{"cells": {"Banka": "Albaraka"}}]}
    assert overviews.source_hash(TABLE) != overviews.source_hash(changed)
    assert overviews.source_hash(TABLE) == overviews.source_hash(dict(TABLE))


def test_unknown_locales_become_turkish():
    assert overviews.normalise_locale("de") == "tr"
    assert overviews.normalise_locale(None) == "tr"
    assert overviews.normalise_locale("en") == "en"


def test_a_second_request_for_the_same_table_does_not_start_a_second_call(monkeypatch):
    # Ten tabs opening one table on a cold cache is ten screenshots arriving at
    # once; only the first is worth a model call.
    started = []
    monkeypatch.setattr(
        overviews.threading,
        "Thread",
        lambda **kwargs: type("T", (), {"start": lambda self: started.append(kwargs["name"])})(),
    )
    try:
        assert overviews.start(
            table=TABLE, table_id="t", locale="tr", page_text=OUTLINE
        ) is True
        assert overviews.running("t", "tr") is True
        assert overviews.start(
            table=TABLE, table_id="t", locale="tr", page_text=OUTLINE
        ) is False
        # A different language is a different overview, so it is not blocked.
        assert overviews.start(
            table=TABLE, table_id="t", locale="en", page_text=OUTLINE
        ) is True
        assert started == ["overview:t:tr", "overview:t:en"]
    finally:
        overviews._running.clear()


class _Row:
    def __init__(self, **kwargs):
        self.table_id = kwargs.get("table_id", "")
        self.locale = kwargs.get("locale", "")
        self.source_hash = kwargs.get("source_hash", "")
        self.model = kwargs.get("model", "")
        self.body = kwargs.get("body", {})


class _Session:
    """Enough of a Session for the cache: one row, or none."""

    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.commits = 0

    def execute(self, _statement):
        session = self

        class Result:
            def scalar_one_or_none(self):
                return session.row

        return Result()

    def add(self, row):
        self.added.append(row)
        self.row = row

    def commit(self):
        self.commits += 1

    def refresh(self, _row):
        pass


def _stored(**kwargs) -> _Row:
    return _Row(
        table_id="konut-finansmani",
        locale="tr",
        source_hash=overviews.source_hash(TABLE),
        model=overviews.model_name(),
        body=RESULT.model_dump(mode="json"),
        **kwargs,
    )


def test_a_row_written_from_a_different_table_is_not_a_hit():
    session = _Session(_stored())
    session.row.source_hash = "something else"
    assert overviews.cached(session, "konut-finansmani", "tr", overviews.source_hash(TABLE)) is None


def test_generations_are_capped_process_wide():
    # The per-table lock only stops two readers racing on one table. Opening
    # six tables in a minute is six vision calls at a host that is also serving
    # the chat -- measured at 14 in flight, after which it stopped answering a
    # ten-token prompt inside 90 seconds.
    assert overviews._slots._initial_value == settings.TABLE_OVERVIEW_CONCURRENCY
    assert settings.TABLE_OVERVIEW_CONCURRENCY >= 1


def test_a_reworded_prompt_invalidates_what_the_old_one_wrote():
    # The table hash cannot see a change that happened in the prompt file, so
    # the prompt version rides along in the model key. Without it, rewording
    # the prompt leaves every cached row answering the previous question.
    from agents.table_overview import PROMPT_VERSION

    assert overviews.model_name().endswith(f"@v{PROMPT_VERSION}")
    session = _Session(_stored())
    session.row.model = f"{settings.CHAT_MODEL}@v{PROMPT_VERSION - 1}"
    assert overviews.cached(
        session, "konut-finansmani", "tr", overviews.source_hash(TABLE)
    ) is None


def test_a_row_written_by_a_different_model_is_not_a_hit():
    session = _Session(_stored())
    session.row.model = "some-older-model"
    assert overviews.cached(session, "konut-finansmani", "tr", overviews.source_hash(TABLE)) is None


def test_a_matching_row_is_served_without_calling_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(
        overviews, "generate_table_overview", lambda *a, **k: called.append(1) or RESULT
    )
    session = _Session(_stored())
    row = overviews.generate(
        session, table=TABLE, table_id="konut-finansmani", locale="tr",
        page_text=OUTLINE,
    )
    assert row is session.row
    assert called == []


def test_a_miss_calls_the_model_once_and_stores_what_it_said(monkeypatch):
    calls = []

    def fake(page_text, *, locale):
        calls.append((page_text, locale))
        return RESULT

    monkeypatch.setattr(overviews, "generate_table_overview", fake)

    # The real ORM class, unpatched: constructing one needs no database, and
    # patching it would break the `select()` that decides insert vs update.
    session = _Session(None)
    row = overviews.generate(
        session, table=TABLE, table_id="konut-finansmani", locale="en",
        page_text=OUTLINE,
    )
    assert calls == [(OUTLINE, "en")]
    assert session.commits == 1
    assert row.body == RESULT.model_dump(mode="json")
    assert row.source_hash == overviews.source_hash(TABLE)
