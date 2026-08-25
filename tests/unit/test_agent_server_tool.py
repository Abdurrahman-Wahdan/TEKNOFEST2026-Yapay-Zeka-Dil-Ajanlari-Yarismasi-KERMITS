"""The agent saving a table itself, and continuing the answer afterwards.

Two kinds of tool now exist and they behave differently, which is the whole point
of this file. `look_at_page` runs in the *browser*, so its call ends the stream and
the client asks again. `save_table` runs *here*, so the write happens in-process,
the result goes back to the model as a `ToolMessage`, and the answer continues in
the same response.

The loop deliberately has **no pass limit**. A count would break real work -- "make
me five tables" is five passes -- so termination comes from progress instead: a call
whose fingerprint has already run is not run again. Both halves of that are pinned
below.

No database and no model. `save_table_view` is patched, and `_FakeLLM` serves
scripted chunks -- which also means these tests say nothing about whether Gemma 4
actually chooses the tool or fills it correctly. That is unknowable until the vLLM
host is reachable.
"""

import pathlib
import uuid

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

import api.agent as agent_mod
from api.saved_tables import SavedTable

pytestmark = pytest.mark.unit

USER = uuid.uuid4()


class _FakeLLM:
    """A chat model that serves one scripted chunk list per `stream()` call.

    The extension over the harness in `test_agent_multimodal.py` is exactly this:
    a server-side tool means the model is streamed **more than once** in a turn, so
    a single fixed chunk list cannot express the scenario.
    """

    def __init__(self, passes):
        self._passes = list(passes)
        self.bound = None
        self.calls = 0
        self.seen: list[list] = []

    def bind_tools(self, tools):
        self.bound = tools
        return self

    def stream(self, messages):
        self.calls += 1
        # Copied: `answer` keeps appending to the same list, and a test asserting
        # what pass 2 was sent must not see pass 3's additions.
        self.seen.append(list(messages))
        chunks = self._passes.pop(0) if self._passes else [_chunk("")]
        return iter(chunks)


def _chunk(content="", tool_calls=None):
    if tool_calls is None:
        return AIMessageChunk(content=content)
    return AIMessageChunk(content=content, tool_calls=tool_calls)


def _save_call(title="Konut", id="s1"):
    return [
        {
            "name": "save_table",
            "args": {"title": title, "columns": ["Banka"], "rows": [["Kuveyt Türk"]]},
            "id": id,
            "type": "tool_call",
        }
    ]


def _look_call(mode="both", id="c1"):
    return [{"name": "look_at_page", "args": {"mode": mode}, "id": id, "type": "tool_call"}]


@pytest.fixture
def harness(monkeypatch):
    """No retrieval, no database. Returns a runner over scripted passes."""
    monkeypatch.setattr(agent_mod, "search", lambda *a, **k: [])

    saves: list[dict] = []

    def fake_save(args, user_id, **kw):
        saves.append({"args": args, "user_id": user_id})
        return (f"Saved {args.get('title')!r}.", SavedTable(slug="konut", title="Konut"))

    monkeypatch.setattr(agent_mod, "save_table_view", fake_save)

    def run(passes, **kw):
        llm = _FakeLLM(passes)
        monkeypatch.setattr(agent_mod, "get_llm", lambda *a, **k: llm)
        kw.setdefault("user_id", USER)
        events = list(agent_mod.answer("panele ekle", **kw))
        return events, llm, saves

    return run


def _types(events):
    return [e.type for e in events]


# --- what is offered --------------------------------------------------------


def test_save_table_is_offered_when_there_is_a_user(harness):
    _, llm, _ = harness([[_chunk("cevap")]])
    assert [t["function"]["name"] for t in llm.bound] == ["save_table"]


def test_save_table_is_not_offered_without_a_user(harness):
    # A caller with no user has no dashboard to write to.
    _, llm, _ = harness([[_chunk("cevap")]], user_id=None)
    assert llm.bound is None


def test_both_tools_are_offered_together(harness):
    _, llm, _ = harness([[_chunk("cevap")]], client_tools=["look_at_page"])
    assert [t["function"]["name"] for t in llm.bound] == ["look_at_page", "save_table"]


def test_the_tool_description_says_only_when_asked(harness):
    # The string the model reads at the decision point, which is attended to more
    # reliably than a distant system rule.
    _, llm, _ = harness([[_chunk("cevap")]])
    description = llm.bound[0]["function"]["description"]
    assert "ONLY when the user has" in description


def test_the_system_prompt_carries_the_same_rule():
    assert "save_table" in agent_mod.SYSTEM_PROMPT
    assert "YALNIZCA" in agent_mod.SYSTEM_PROMPT


def test_the_tool_takes_a_flat_matrix_not_nested_cells():
    # A nested object is the likeliest thing for the model to get wrong, and
    # arguments arrive split across chunks -- a malformed object loses the whole
    # table rather than one cell.
    params = agent_mod.SAVE_TABLE["function"]["parameters"]["properties"]
    assert params["rows"]["items"]["items"]["type"] == "string"
    required = agent_mod.SAVE_TABLE["function"]["parameters"]["required"]
    assert sorted(required) == [
        "columns",
        "rows",
        "title",
    ]


# --- the server-side round trip ---------------------------------------------


def test_a_save_runs_in_process_and_the_model_is_streamed_again(harness):
    events, llm, saves = harness(
        [[_chunk("kaydediyorum", tool_calls=_save_call())], [_chunk(" tamam")]]
    )
    assert llm.calls == 2
    assert len(saves) == 1
    assert saves[0]["user_id"] == USER
    assert saves[0]["args"]["title"] == "Konut"


def test_the_answer_continues_after_the_save(harness):
    events, _, _ = harness(
        [[_chunk("bir saniye", tool_calls=_save_call())], [_chunk("tablo hazır")]]
    )
    text = "".join(e.text for e in events if e.type == "token")
    assert text == "bir saniyetablo hazır"


def test_a_saved_view_frame_carries_the_slug_and_title(harness):
    events, _, _ = harness([[_chunk(tool_calls=_save_call())], [_chunk("ok")]])
    saved = [e for e in events if e.type == "saved_view"]
    assert len(saved) == 1
    assert saved[0].view_slug == "konut"
    assert saved[0].view_title == "Konut"


def test_the_assistant_turn_then_the_tool_message_are_appended_in_that_order(harness):
    _, llm, _ = harness([[_chunk("ara söz", tool_calls=_save_call())], [_chunk("ok")]])
    second = llm.seen[1]
    assert isinstance(second[-2], AIMessage)
    assert not isinstance(second[-2], AIMessageChunk)  # a plain message, not a chunk
    assert second[-2].tool_calls[0]["name"] == "save_table"
    assert isinstance(second[-1], ToolMessage)
    assert second[-1].tool_call_id == "s1"
    assert "Saved" in second[-1].content


def test_the_assistant_turn_is_appended_once_for_several_saves(harness):
    calls = _save_call("A", id="s1") + _save_call("B", id="s2")
    _, llm, saves = harness([[_chunk(tool_calls=calls)], [_chunk("ok")]])
    assert len(saves) == 2
    second = llm.seen[1]
    assert sum(isinstance(m, AIMessage) and not isinstance(m, ToolMessage)
               for m in second if not isinstance(m, tuple)) == 1
    assert [m.tool_call_id for m in second if isinstance(m, ToolMessage)] == ["s1", "s2"]


def test_a_failing_save_is_prose_and_never_an_error_frame(harness, monkeypatch):
    # An `error` frame makes the router discard the whole assembled answer, so a
    # failed save must not be able to delete a good answer.
    monkeypatch.setattr(
        agent_mod, "save_table_view", lambda *a, **k: ("Could not save it.", None)
    )
    events, llm, _ = harness(
        [[_chunk("deniyorum", tool_calls=_save_call())], [_chunk(" olmadı")]]
    )
    assert "error" not in _types(events)
    assert "saved_view" not in _types(events)
    assert llm.calls == 2
    assert isinstance(llm.seen[1][-1], ToolMessage)
    assert llm.seen[1][-1].content == "Could not save it."


# --- no pass cap, but a progress guard --------------------------------------


def test_five_different_saves_all_run(harness):
    # The case a pass cap would break: the model believing it saved five tables
    # while only three were written.
    passes = [[_chunk(tool_calls=_save_call(f"T{i}", id=f"s{i}"))] for i in range(5)]
    passes.append([_chunk("hepsi hazır")])
    events, llm, saves = harness(passes)
    assert len(saves) == 5
    assert [s["args"]["title"] for s in saves] == ["T0", "T1", "T2", "T3", "T4"]
    assert len([e for e in events if e.type == "saved_view"]) == 5
    assert llm.calls == 6


def test_a_model_repeating_one_save_writes_once_and_the_loop_ends(harness):
    # Identical arguments every pass. Without the fingerprint guard this is an
    # infinite loop with a token bill attached.
    passes = [[_chunk(tool_calls=_save_call("Aynı", id=f"s{i}"))] for i in range(20)]
    events, llm, saves = harness(passes)
    assert len(saves) == 1
    assert llm.calls == 2       # the save, then the pass that repeated it
    assert len([e for e in events if e.type == "saved_view"]) == 1


def test_the_id_changing_does_not_defeat_the_guard(harness):
    # The fingerprint is over the name and arguments; the call id is assigned by
    # the model and varies between passes for the same intent.
    passes = [
        [_chunk(tool_calls=_save_call("Aynı", id="a"))],
        [_chunk(tool_calls=_save_call("Aynı", id="b"))],
    ]
    _, _, saves = harness(passes)
    assert len(saves) == 1


# --- the client tool still behaves as it did --------------------------------


def test_look_at_page_still_ends_the_stream_on_the_first_pass(harness):
    events, llm, _ = harness(
        [[_chunk(tool_calls=_look_call("text"))], [_chunk("should not run")]],
        client_tools=["look_at_page"],
    )
    assert llm.calls == 1
    assert _types(events)[-1] == "tool_call"
    assert events[-1].tool_name == "look_at_page"
    assert events[-1].mode == "text"


def test_an_invented_mode_falls_back_to_both(harness):
    events, _, _ = harness(
        [[_chunk(tool_calls=_look_call("hologram"))]], client_tools=["look_at_page"]
    )
    assert events[-1].mode == "both"


def test_a_save_and_a_look_in_one_turn_saves_then_suspends(harness):
    # The save runs before the stream ends, so the write is not lost if the client
    # never comes back with the page.
    calls = _save_call() + _look_call()
    events, llm, saves = harness([[_chunk(tool_calls=calls)]], client_tools=["look_at_page"])
    assert len(saves) == 1
    assert _types(events) == ["status", "status", "saved_view", "tool_call"]
    assert llm.calls == 1


def test_a_plain_answer_streams_once_and_stops(harness):
    events, llm, saves = harness([[_chunk("sadece cevap")]])
    assert llm.calls == 1
    assert saves == []
    assert _types(events) == ["status", "status", "token"]


def test_an_unknown_tool_name_is_ignored(harness):
    calls = [{"name": "drop_database", "args": {}, "id": "x", "type": "tool_call"}]
    events, llm, saves = harness([[_chunk("cevap", tool_calls=calls)]])
    assert saves == []
    assert llm.calls == 1
    assert "error" not in _types(events)


def test_tool_call_arguments_split_across_chunks_still_parse(harness):
    # The reason `gathered` accumulates: the name arrives in one chunk and the
    # arguments a few characters at a time after it.
    from langchain_core.messages.tool import ToolCallChunk

    first = AIMessageChunk(
        content="",
        tool_call_chunks=[
            ToolCallChunk(name="save_table", args='{"title": "Kon', id="s1", index=0)
        ],
    )
    second = AIMessageChunk(
        content="",
        tool_call_chunks=[
            ToolCallChunk(
                name=None,
                args='ut", "columns": ["a"], "rows": [["1"]]}',
                id=None,
                index=0,
            )
        ],
    )
    _, _, saves = harness([[first, second], [_chunk("ok")]])
    assert len(saves) == 1
    assert saves[0]["args"] == {"title": "Konut", "columns": ["a"], "rows": [["1"]]}


class TestAutomationAnnouncement:
    """Telling the browser that a standing order changed.

    `create_automation` runs inside the supervisor's graph, so nothing on the
    wire used to say an automation had appeared. The profile page went on showing
    its cached list, and the user reasonably concluded the assistant had only
    *claimed* to set one up -- it had actually written the row. This is the frame
    that closes that gap, and it must fire on a write and never on a refusal.
    """

    def message(self, name, content):
        return ToolMessage(content=content, name=name, tool_call_id="c1")

    def test_a_created_automation_is_announced(self):
        assert agent_mod._automation_changed(
            self.message(
                "create_automation",
                "Otomasyon kuruldu: 'Sabah altın raporu', her gün 09:00.",
            )
        ) == "created"

    def test_an_updated_automation_is_announced(self):
        assert agent_mod._automation_changed(
            self.message("update_automation", "Otomasyon güncellendi: 'x', artık 19:30.")
        ) == "updated"

    def test_pausing_counts_as_an_update(self):
        """The list has to be refetched either way -- a paused row renders
        differently."""
        assert agent_mod._automation_changed(
            self.message("update_automation", "Otomasyon durduruldu: 'x'. Silinmedi")
        ) == "updated"

    def test_a_refusal_announces_nothing(self):
        """The tools return prose and never raise, so "I could not" and "I did"
        arrive through the same channel. Announcing a refusal would tell the UI
        to refetch after a write that never happened -- and, worse, would make
        the frame evidence of something that did not occur."""
        for refusal in (
            "Bu oturumda otomasyon kuramıyorum çünkü hesap bilgisi yok.",
            "Otomasyon kaydedilemedi (RuntimeError).",
            "Otomasyon güncellenemedi (OperationalError).",
            "'altın' birden fazla otomasyona uyuyor: 'a', 'b'.",
            "Kullanıcının hiç otomasyonu yok, dolayısıyla değiştirecek bir şey de yok.",
            "Değiştirilecek bir alan verilmedi.",
        ):
            assert agent_mod._automation_changed(
                self.message("create_automation", refusal)
            ) is None, refusal

    def test_the_ceiling_refusal_announces_nothing(self):
        assert agent_mod._automation_changed(
            self.message(
                "create_automation",
                "Kullanıcının zaten 20 otomasyonu var, üst sınır 20.",
            )
        ) is None

    def test_another_tool_saying_the_same_words_announces_nothing(self):
        """Gated on the tool name first, so a bank specialist quoting a page
        cannot forge this frame."""
        assert agent_mod._automation_changed(
            self.message("ask_vakif", "Otomasyon kuruldu")
        ) is None

    def test_listing_is_not_a_change(self):
        assert agent_mod._automation_changed(
            self.message("list_automations", "1. Sabah altın raporu -- her gün 09:00")
        ) is None

    def test_every_success_string_the_tools_return_is_recognised(self):
        """Pins the two modules together. The tools' wording is the protocol
        here, so a reworded success sentence must fail loudly rather than
        silently stop announcing writes."""
        from agents.shared import automation_tools

        source = pathlib.Path(automation_tools.__file__).read_text(encoding="utf-8")
        for phrase in agent_mod._AUTOMATION_WROTE:
            assert f'f"{phrase}' in source or f'"{phrase}' in source, phrase
