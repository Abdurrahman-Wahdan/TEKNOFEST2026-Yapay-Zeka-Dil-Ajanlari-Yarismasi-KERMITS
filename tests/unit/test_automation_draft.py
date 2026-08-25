"""Turning a sentence into a storable automation.

The drafting agent's output is written straight into a row that fires unattended
for months, so the validation is the feature. A model saying "9 AM" has to become
`hour=9` or fail -- never a default that quietly runs at midnight.

No model host: `create_agent` and the built graph are both replaced.
"""

import pytest
from langchain.agents.structured_output import ToolStrategy
from pydantic import ValidationError

from agents.automation import agent as draft_agent
from agents.automation.models import AutomationDraft

pytestmark = pytest.mark.unit


class TestModel:
    def test_hour_is_bounded_to_a_real_hour(self):
        for bad in (-1, 24, 99):
            with pytest.raises(ValidationError):
                AutomationDraft(title="t", prompt="p", hour=bad)

    def test_minute_is_bounded(self):
        for bad in (-1, 60):
            with pytest.raises(ValidationError):
                AutomationDraft(title="t", prompt="p", hour=9, minute=bad)

    def test_title_is_bounded_to_the_column(self):
        with pytest.raises(ValidationError):
            AutomationDraft(title="x" * 161, prompt="p", hour=9)

    def test_prompt_has_no_length_limit(self):
        """Nothing sent to the model is truncated anywhere in this app."""
        properties = AutomationDraft.model_json_schema()["properties"]
        assert "maxLength" not in properties["prompt"]

    def test_defaults_are_daily_and_online(self):
        draft = AutomationDraft(title="t", prompt="p", hour=9)
        assert draft.minute == 0
        assert draft.weekdays == []
        assert draft.web_search is True

    @pytest.mark.parametrize(
        "given, expected",
        [
            ([4, 0, 4], [0, 4]),
            ([7, 2], [2]),
            ([-1, 3], [3]),
            ([True, False], []),
            (["1", 1], [1]),
            ("hafta içi", []),
            (None, []),
        ],
    )
    def test_weekdays_are_cleaned_rather_than_rejected(self, given, expected):
        """A stray 7 must not cost the six days the model got right."""
        assert AutomationDraft(
            title="t", prompt="p", hour=9, weekdays=given
        ).weekdays == expected

    def test_text_is_stripped(self):
        draft = AutomationDraft(title="  Sabah  ", prompt="  soru  ", hour=9)
        assert draft.title == "Sabah"
        assert draft.prompt == "soru"


class TestAgent:
    def test_uses_validated_structured_output(self, monkeypatch):
        captured = {}
        graph = object()

        monkeypatch.setattr(draft_agent, "get_llm", lambda role, **kwargs: (role, kwargs))
        monkeypatch.setattr(
            draft_agent, "create_agent", lambda **kwargs: captured.update(kwargs) or graph
        )

        assert draft_agent.build_automation_agent() is graph
        assert captured["model"] == ("chat", {"disable_streaming": True})
        assert captured["tools"] == []
        assert captured["name"] == "automation_draft"
        assert isinstance(captured["response_format"], ToolStrategy)
        assert captured["response_format"].schema is AutomationDraft

    def test_passes_the_description_through(self, monkeypatch):
        expected = AutomationDraft(
            title="Sabah altın raporu",
            prompt="Her katılım bankasında gram altın alış/satış fiyatlarını karşılaştır.",
            hour=9,
        )

        class Graph:
            def invoke(self, payload):
                assert payload["messages"] == [
                    ("user", "her sabah 9'da altın fiyatlarına bak")
                ]
                return {"structured_response": expected}

        monkeypatch.setattr(draft_agent, "build_automation_agent", lambda: Graph())
        assert draft_agent.draft_automation(
            "  her sabah 9'da altın fiyatlarına bak  "
        ) is expected

    def test_empty_text_is_refused_before_the_model(self, monkeypatch):
        def never(*_args, **_kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("the model must not be called for empty input")

        monkeypatch.setattr(draft_agent, "build_automation_agent", never)
        for blank in ("", "   ", None):
            with pytest.raises(ValueError):
                draft_agent.draft_automation(blank)

    def test_an_unvalidated_result_raises_rather_than_defaulting(self, monkeypatch):
        """A standing order nobody meant, firing every morning, is worse than a
        failed save the user can retry."""

        class Graph:
            def invoke(self, _payload):
                return {"structured_response": None}

        monkeypatch.setattr(draft_agent, "build_automation_agent", lambda: Graph())
        with pytest.raises(RuntimeError):
            draft_agent.draft_automation("her sabah altın")
