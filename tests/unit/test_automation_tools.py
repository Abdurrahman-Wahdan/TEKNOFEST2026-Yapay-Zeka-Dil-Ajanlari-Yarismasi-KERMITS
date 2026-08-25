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

from agents.shared.automation_tools import (
    MAX_PER_USER,
    TITLE_CHARS,
    build_automation_tools,
)

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
    """A stored automation, mutable, so an update can be asserted on it."""

    def __init__(self, title, hour=9, minute=0, weekdays=None, enabled=True):
        self.id = uuid.uuid4()
        self.title = title
        self.prompt = f"{title} sorusu"
        self.hour = hour
        self.minute = minute
        self.weekdays = weekdays or []
        self.enabled = enabled
        self.web_search = True
        self.next_run_at = None


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
    """`(create, listing)`, the pair the older tests were written against."""
    create, _update, listing = build_automation_tools()
    return create, listing


@pytest.fixture
def update():
    _create, updater, _listing = build_automation_tools()
    return updater


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

    def test_says_it_cannot_delete_but_can_change(self, tools, store):
        """The list used to tell the model it had no power to change anything.

        It said "saatini değiştirmek Profil sayfasından yapılır" -- true when
        written, and afterwards the exact reason the model kept sending users to
        the profile page to fix an hour it could have fixed itself.
        """
        _, listing = tools
        store.existing = [_Existing("x")]
        answer = listing.func(runtime=_Runtime(USER))
        assert "SİLME yetkin yok" in answer
        assert "update_automation" in answer


class TestUpdate:
    """Changing one that exists.

    The tool the assistant was missing while it was already inviting the
    correction: `create_automation` tells the model to say it may have misread
    the hour, and for a while the only thing it could do when the user said "no,
    19:00" was point at the profile page.
    """

    def test_no_user_id_refuses_without_writing(self, update, store):
        store.existing = [_Existing("Sabah altın raporu")]
        answer = update.func(title="Sabah altın raporu", hour=19, runtime=_Runtime({}))
        assert "hesap bilgisi yok" in answer
        assert store.existing[0].hour == 9

    def test_changing_the_hour_moves_the_next_run(self, update, store):
        row = _Existing("Sabah altın raporu", hour=9)
        store.existing = [row]
        answer = update.func(
            title="Sabah altın raporu", hour=19, minute=30, runtime=_Runtime(USER)
        )
        assert row.hour == 19 and row.minute == 30
        # Recomputed from now, exactly as PATCH /me/automations/{id} does.
        # Keeping the old value would fire it once more at the time the user was
        # correcting away from.
        assert row.next_run_at is not None
        assert "19:30" in answer
        assert "güncellendi" in answer

    def test_a_partial_title_is_enough(self, update, store):
        row = _Existing("Günlük Aidatsız Kart Avantaj Raporu")
        store.existing = [row]
        update.func(title="aidatsız", hour=18, minute=30, runtime=_Runtime(USER))
        assert (row.hour, row.minute) == (18, 30)

    def test_matching_ignores_case(self, update, store):
        row = _Existing("Sabah Altın Raporu")
        store.existing = [row]
        update.func(title="sabah altın raporu", hour=7, runtime=_Runtime(USER))
        assert row.hour == 7

    def test_a_longer_phrase_than_the_title_still_matches(self, update, store):
        """The model often names the thing rather than quoting the row."""
        row = _Existing("altın")
        store.existing = [row]
        update.func(title="altın raporu otomasyonu", hour=8, runtime=_Runtime(USER))
        assert row.hour == 8

    def test_an_exact_title_wins_over_a_substring(self, update, store):
        """Two rows where one title contains the other must not be ambiguous."""
        exact = _Existing("Altın", hour=9)
        longer = _Existing("Altın ve döviz raporu", hour=9)
        store.existing = [exact, longer]
        update.func(title="Altın", hour=11, runtime=_Runtime(USER))
        assert exact.hour == 11
        assert longer.hour == 9

    def test_an_ambiguous_title_refuses_and_names_the_candidates(self, update, store):
        """Two automations about gold differ only in their wording. Editing the
        wrong one is a silent change to something the user never mentioned."""
        first = _Existing("Sabah altın raporu", hour=9)
        second = _Existing("Akşam altın raporu", hour=20)
        store.existing = [first, second]
        answer = update.func(title="altın raporu", hour=11, runtime=_Runtime(USER))
        assert "birden fazla" in answer
        assert "Sabah altın raporu" in answer and "Akşam altın raporu" in answer
        assert (first.hour, second.hour) == (9, 20)

    def test_an_unknown_title_refuses_and_lists_what_exists(self, update, store):
        row = _Existing("Sabah altın raporu")
        store.existing = [row]
        answer = update.func(title="dolar", hour=11, runtime=_Runtime(USER))
        assert "bulamadım" in answer
        assert "Sabah altın raporu" in answer

    def test_with_nothing_stored_it_says_so(self, update, store):
        answer = update.func(title="herhangi", hour=11, runtime=_Runtime(USER))
        assert "hiç otomasyonu yok" in answer

    def test_no_fields_given_is_a_question_not_a_write(self, update, store):
        row = _Existing("Sabah altın raporu", hour=9)
        store.existing = [row]
        answer = update.func(title="Sabah altın raporu", runtime=_Runtime(USER))
        assert "Değiştirilecek bir alan verilmedi" in answer
        assert row.hour == 9

    def test_pausing_says_paused_not_deleted(self, update, store):
        """The model has no delete tool, so "iptal et" lands here. It must not
        tell the user their automation is gone -- it is not, and the past reports
        are not either."""
        row = _Existing("Sabah altın raporu")
        store.existing = [row]
        answer = update.func(
            title="Sabah altın raporu", enabled=False, runtime=_Runtime(USER)
        )
        assert row.enabled is False
        assert "durduruldu" in answer
        assert "Silinmedi" in answer

    def test_resuming_reports_the_next_run(self, update, store):
        row = _Existing("Sabah altın raporu", enabled=False)
        store.existing = [row]
        answer = update.func(
            title="Sabah altın raporu", enabled=True, runtime=_Runtime(USER)
        )
        assert row.enabled is True
        assert "güncellendi" in answer

    def test_an_empty_weekday_list_means_every_day(self, update, store):
        """Absent and empty differ on this field alone: `[]` is the user asking
        for every day, and omitting it means leave the days alone."""
        row = _Existing("x", weekdays=[0, 4])
        store.existing = [row]
        update.func(title="x", weekdays=[], runtime=_Runtime(USER))
        assert row.weekdays == []

    def test_omitting_weekdays_leaves_them_alone(self, update, store):
        row = _Existing("x", weekdays=[0, 4])
        store.existing = [row]
        update.func(title="x", hour=7, runtime=_Runtime(USER))
        assert row.weekdays == [0, 4]

    def test_weekdays_are_cleaned_before_storage(self, update, store):
        row = _Existing("x")
        store.existing = [row]
        update.func(title="x", weekdays=[4, 0, 7, 4, -1], runtime=_Runtime(USER))
        assert row.weekdays == [0, 4]

    def test_renaming_reports_the_new_name(self, update, store):
        row = _Existing("Eski ad")
        store.existing = [row]
        answer = update.func(
            title="Eski ad", new_title="Yeni ad", runtime=_Runtime(USER)
        )
        assert row.title == "Yeni ad"
        assert "Yeni ad" in answer

    def test_a_new_title_is_clipped_to_the_column(self, update, store):
        row = _Existing("x")
        store.existing = [row]
        update.func(title="x", new_title="ç" * 400, runtime=_Runtime(USER))
        assert len(row.title) == TITLE_CHARS

    def test_the_prompt_can_be_replaced(self, update, store):
        row = _Existing("x")
        store.existing = [row]
        update.func(
            title="x", prompt="Dolar ve altın fiyatlarını karşılaştır",
            runtime=_Runtime(USER),
        )
        assert row.prompt == "Dolar ve altın fiyatlarını karşılaştır"

    def test_a_database_failure_comes_back_as_prose(self, update, monkeypatch):
        """Never an exception: the router discards the whole assembled answer on
        an `error` frame, so a raise here would delete a good answer."""
        import api.db.session as session_module

        def broken():
            raise RuntimeError("connection reset")

        monkeypatch.setattr(session_module, "session_scope", broken)
        answer = update.func(title="x", hour=9, runtime=_Runtime(USER))
        assert "güncellenemedi" in answer
        assert "RuntimeError" in answer
        assert "tekrar DENEME" in answer

    def test_the_runtime_annotation_is_a_resolved_type(self):
        """Same trap as `create_automation`. See the note in the module."""
        import inspect

        _create, updater, _listing = build_automation_tools()
        annotation = inspect.signature(updater.func).parameters["runtime"].annotation
        assert not isinstance(annotation, str), annotation


class TestSchemas:
    def test_create_takes_no_cron_string(self):
        """The schedule is three integers. A wrong cron fails by never firing."""
        create, _update, _listing = build_automation_tools()
        props = create.args_schema.model_json_schema()["properties"]
        assert set(props) == {
            "title", "prompt", "hour", "minute", "weekdays", "web_search"
        }
        assert props["hour"]["maximum"] == 23
        assert props["minute"]["maximum"] == 59

    def test_list_declares_an_empty_schema(self):
        """Inference would hit `runtime: ToolRuntime`, which has no JSON schema."""
        *_, listing = build_automation_tools()
        assert listing.args_schema is not None
        assert listing.args_schema.model_json_schema().get("properties", {}) == {}

    def test_there_is_no_delete_tool(self):
        """Pausing is the reversible half of "cancel"; deleting is not offered."""
        names = {t.name for t in build_automation_tools()}
        assert names == {
            "create_automation", "update_automation", "list_automations",
        }

    def test_update_selects_by_title_not_by_id(self):
        """A model cannot verify a UUID, and one wrong character edits the wrong
        row -- or nothing at all. A title is something it already knows: it
        either just wrote it or read it out of `list_automations`."""
        _create, updater, _listing = build_automation_tools()
        props = updater.args_schema.model_json_schema()["properties"]
        assert "title" in props
        assert not any("id" == name for name in props)
        assert props["hour"]["anyOf"][0]["maximum"] == 23
        assert set(props) == {
            "title", "new_title", "prompt", "hour", "minute", "weekdays",
            "enabled", "web_search",
        }

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
