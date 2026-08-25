"""The supervisor's create_automation / list_automations tools.

Two properties matter more than the happy path.

**Neither tool raises.** `api/routers/chat.py` discards the whole assembled
answer when it sees an `error` frame, so an exception in a tool would delete a
good answer along with the failed write. Every failure here has to come back as
a sentence the model can read out.

**Neither writes without an owner.** The standalone answer path has no signed-in
user, and an automation with no owner could never run -- so absence of
`user_id` is a refusal, not a default.

The database is a fake: the tools import `session_scope` inside the callable
precisely so it can be replaced here.
"""

import uuid
from contextlib import contextmanager

import pytest

from agents.shared.automation_tools import MAX_PER_USER, build_automation_tools

pytestmark = pytest.mark.unit


class _Runtime:
    """Stands in for `ToolRuntime[AgentContext]`; only `.context` is read."""

    def __init__(self, context=None):
        self.context = context if context is not None else {}
        self.config = {}


class _Store:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.added = []

    def scalars(self, _statement):
        return self

    def all(self):
        return self.existing

    def add(self, obj):
        obj.id = uuid.uuid4()
        self.added.append(obj)

    def flush(self):
        pass


class _Existing:
    def __init__(self, title, hour=9, minute=0, weekdays=None, enabled=True):
        self.title = title
        self.hour = hour
        self.minute = minute
        self.weekdays = weekdays or []
        self.enabled = enabled


@pytest.fixture
def store(monkeypatch):
    """A fake session, installed where the tools look for one."""
    made = _Store()

    @contextmanager
    def scope():
        yield made

    import api.db.session as session_module

    monkeypatch.setattr(session_module, "session_scope", scope)
    return made


@pytest.fixture
def tools():
    create, listing = build_automation_tools()
    return create, listing


USER = {"session_id": "s", "user_id": str(uuid.uuid4())}


class TestRefusals:
    def test_no_user_id_refuses_without_writing(self, tools, store):
        create, _ = tools
        answer = create.func(
            title="t", prompt="p", hour=9, runtime=_Runtime({"session_id": "s"})
        )
        assert "otomasyon kuramıyorum" in answer.lower()
        assert store.added == []

    def test_an_unparseable_user_id_refuses(self, tools, store):
        create, _ = tools
        answer = create.func(
            title="t", prompt="p", hour=9,
            runtime=_Runtime({"session_id": "s", "user_id": "not-a-uuid"}),
        )
        assert "otomasyon kuramıyorum" in answer.lower()
        assert store.added == []

    def test_an_empty_title_refuses(self, tools, store):
        create, _ = tools
        answer = create.func(
            title="   ", prompt="p", hour=9, runtime=_Runtime(USER)
        )
        assert "boş" in answer
        assert store.added == []

    def test_an_empty_prompt_refuses(self, tools, store):
        create, _ = tools
        answer = create.func(title="t", prompt="", hour=9, runtime=_Runtime(USER))
        assert "boş" in answer
        assert store.added == []

    def test_the_per_user_ceiling_is_a_sentence_not_an_error(self, tools, store):
        create, _ = tools
        store.existing = [_Existing(f"a{i}") for i in range(MAX_PER_USER)]
        answer = create.func(title="t", prompt="p", hour=9, runtime=_Runtime(USER))
        assert str(MAX_PER_USER) in answer
        assert store.added == []


class TestNeverRaises:
    def test_a_database_failure_comes_back_as_prose(self, tools, monkeypatch):
        create, _ = tools

        @contextmanager
        def broken():
            raise RuntimeError("connection refused")
            yield  # pragma: no cover

        import api.db.session as session_module

        monkeypatch.setattr(session_module, "session_scope", broken)
        answer = create.func(title="t", prompt="p", hour=9, runtime=_Runtime(USER))
        assert "RuntimeError" in answer
        assert "DENEME" in answer  # do not retry with the same arguments

    def test_listing_survives_a_database_failure(self, tools, monkeypatch):
        _, listing = tools

        @contextmanager
        def broken():
            raise RuntimeError("gone")
            yield  # pragma: no cover

        import api.db.session as session_module

        monkeypatch.setattr(session_module, "session_scope", broken)
        answer = listing.func(runtime=_Runtime(USER))
        assert "okunamadı" in answer


class TestCreate:
    def test_writes_the_row_and_says_when_it_runs(self, tools, store):
        create, _ = tools
        answer = create.func(
            title="Sabah altın raporu",
            prompt="Gram altın fiyatlarını karşılaştır.",
            hour=9,
            minute=0,
            runtime=_Runtime(USER),
        )
        assert len(store.added) == 1
        row = store.added[0]
        assert row.title == "Sabah altın raporu"
        assert row.hour == 9
        assert row.weekdays == []
        assert row.enabled is True
        assert row.next_run_at is not None
        assert "her gün 09:00" in answer
        # The model is told where the report lands, because it has to tell the user.
        assert "Raporlar" in answer

    def test_weekdays_are_cleaned_before_storage(self, tools, store):
        create, _ = tools
        create.func(
            title="t", prompt="p", hour=9,
            weekdays=[4, 0, 7, 4, -1],
            runtime=_Runtime(USER),
        )
        assert store.added[0].weekdays == [0, 4]

    def test_the_title_is_clipped_to_the_column(self, tools, store):
        create, _ = tools
        create.func(
            title="ü" * 400, prompt="p", hour=9, runtime=_Runtime(USER)
        )
        assert len(store.added[0].title) == 160

    def test_named_days_are_read_back_in_the_answer(self, tools, store):
        create, _ = tools
        answer = create.func(
            title="t", prompt="p", hour=21, minute=30, weekdays=[0, 4],
            runtime=_Runtime(USER),
        )
        assert "Pazartesi, Cuma" in answer
        assert "21:30" in answer


class TestList:
    def test_empty_says_so(self, tools, store):
        _, listing = tools
        assert "otomasyonu yok" in listing.func(runtime=_Runtime(USER))

    def test_names_each_with_its_schedule(self, tools, store):
        _, listing = tools
        store.existing = [
            _Existing("Sabah altın", hour=9),
            _Existing("Haftalık kampanya", hour=21, minute=30, weekdays=[0]),
        ]
        answer = listing.func(runtime=_Runtime(USER))
        assert "1. Sabah altın -- her gün 09:00" in answer
        assert "2. Haftalık kampanya -- Pazartesi günleri 21:30" in answer

    def test_a_stopped_automation_is_marked(self, tools, store):
        _, listing = tools
        store.existing = [_Existing("Duran", enabled=False)]
        assert "DURDURULMUŞ" in listing.func(runtime=_Runtime(USER))

    def test_says_it_cannot_delete(self, tools, store):
        """There is no delete tool, and the model must not imply there is."""
        _, listing = tools
        store.existing = [_Existing("x")]
        assert "silme yetkin yok" in listing.func(runtime=_Runtime(USER))


class TestSchemas:
    def test_create_takes_no_cron_string(self):
        """The schedule is three integers. A wrong cron fails by never firing."""
        create, _ = build_automation_tools()
        props = create.args_schema.model_json_schema()["properties"]
        assert set(props) == {
            "title", "prompt", "hour", "minute", "weekdays", "web_search"
        }
        assert props["hour"]["maximum"] == 23
        assert props["minute"]["maximum"] == 59

    def test_list_declares_an_empty_schema(self):
        """Inference would hit `runtime: ToolRuntime`, which has no JSON schema."""
        _, listing = build_automation_tools()
        assert listing.args_schema is not None
        assert listing.args_schema.model_json_schema().get("properties", {}) == {}

    def test_there_is_no_delete_tool(self):
        names = {t.name for t in build_automation_tools()}
        assert names == {"create_automation", "list_automations"}

    def test_the_runtime_annotation_is_a_resolved_type(self):
        """`from __future__ import annotations` in this module breaks injection.

        LangChain decides whether to inject `ToolRuntime` by inspecting the
        annotation *object*. The future import makes every annotation a string,
        so the parameter is never recognised, never injected, and stays a
        required positional argument the model was never told about.

        Nothing fails at import: the tool builds, binds, and is offered normally.
        It blows up the first time the model actually calls it, and
        `_agent_answer` turns that into "The live banking assistant is
        unavailable" -- a whole chat turn lost, with the real cause three frames
        inside LangGraph. Measured on 2026-08-25; this is what stops it coming
        back.
        """
        import inspect

        from langgraph.prebuilt.tool_node import ToolRuntime

        for tool in build_automation_tools():
            annotated = [
                parameter.annotation
                for parameter in inspect.signature(tool.func).parameters.values()
                if parameter.name == "runtime"
            ]
            assert annotated, f"{tool.name} has no runtime parameter"
            assert not isinstance(annotated[0], str), (
                f"{tool.name}'s runtime annotation is a string -- this module "
                "must not use `from __future__ import annotations`"
            )
            assert typing_origin(annotated[0]) is ToolRuntime


def typing_origin(annotation):
    import typing

    return typing.get_origin(annotation) or annotation
