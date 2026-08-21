"""The kept-table metadata agent uses validated structured output."""

import pytest
from langchain.agents.structured_output import ToolStrategy

from agents.table_metadata import agent as metadata_agent
from agents.table_metadata.models import TableMetadata
from api.routers import chat as chat_router
from api.schemas.chat import TableMetadataRequest

pytestmark = pytest.mark.unit


def test_title_and_description_have_no_length_limits():
    properties = TableMetadata.model_json_schema()["properties"]
    for field in ("title", "description"):
        assert "minLength" not in properties[field]
        assert "maxLength" not in properties[field]


def test_agent_uses_current_langchain_structured_output(monkeypatch):
    captured = {}
    graph = object()

    monkeypatch.setattr(metadata_agent, "get_llm", lambda role, **kwargs: (role, kwargs))

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return graph

    monkeypatch.setattr(metadata_agent, "create_agent", fake_create_agent)

    assert metadata_agent.build_table_metadata_agent() is graph
    assert captured["model"] == ("chat", {"disable_streaming": True})
    assert captured["tools"] == []
    assert captured["name"] == "table_metadata"
    assert isinstance(captured["response_format"], ToolStrategy)
    assert captured["response_format"].schema is TableMetadata


def test_generation_passes_full_chat_and_exact_table(monkeypatch):
    expected = TableMetadata(
        title="Kuveyt Türk housing finance scenario",
        description=(
            "The user compared a 100,000 TRY, 12-month housing-finance scenario "
            "using a customer supplied monthly profit rate of 2.6 percent. The "
            "table records the quoted instalment and total repayment."
        ),
    )

    class Graph:
        def invoke(self, payload):
            messages = payload["messages"]
            assert messages[0] == ("user", "Use 2.6 percent")
            assert messages[1] == ("assistant", "Here is the table")
            assert '"label": "Bank"' in messages[-1][1]
            assert '"bank": "Kuveyt Türk"' in messages[-1][1]
            return {"structured_response": expected}

    monkeypatch.setattr(metadata_agent, "build_table_metadata_agent", lambda: Graph())

    result = metadata_agent.generate_table_metadata(
        [("user", "Use 2.6 percent"), ("assistant", "Here is the table")],
        {
            "columns": [{"key": "bank", "label": "Bank"}],
            "rows": [{"cells": {"bank": "Kuveyt Türk"}}],
        },
    )

    assert result == expected


def test_generation_rejects_an_unstructured_result(monkeypatch):
    class Graph:
        def invoke(self, payload):
            return {"messages": []}

    monkeypatch.setattr(metadata_agent, "build_table_metadata_agent", lambda: Graph())
    with pytest.raises(RuntimeError, match="no validated result"):
        metadata_agent.generate_table_metadata(
            [("user", "Keep it")],
            {"columns": [{"key": "bank", "label": "Bank"}], "rows": []},
        )


def test_endpoint_checks_ownership_and_passes_visible_context(monkeypatch):
    user = type("User", (), {"id": "user-1"})()
    chat = type("Chat", (), {"user_id": "user-1"})()

    class Session:
        def get(self, model, session_id):
            return chat

    body = TableMetadataRequest.model_validate(
        {
            "session_id": "90c6048b-5ce9-4d45-b166-dfe63e39aa88",
            "conversation": [
                {"role": "user", "content": "Use 2.6 percent"},
                {"role": "assistant", "content": "Here is the comparison"},
            ],
            "table": {
                "columns": [{"key": "bank", "label": "Bank"}],
                "rows": [{"cells": {"bank": "Kuveyt Türk"}}],
            },
        }
    )
    seen = {}

    def fake_generate(conversation, table):
        seen["conversation"] = conversation
        seen["table"] = table
        return TableMetadata(
            title="Kuveyt Türk housing finance scenario",
            description=(
                "This table captures the user's 100,000 TRY housing-finance "
                "scenario over 12 months with a customer supplied rate. It is "
                "ready for a later comparison with other participation banks."
            ),
        )

    monkeypatch.setattr(chat_router, "generate_table_metadata", fake_generate)
    result = chat_router.create_table_metadata(body, user, Session())

    assert seen["conversation"] == [
        ("user", "Use 2.6 percent"),
        ("assistant", "Here is the comparison"),
    ]
    assert seen["table"]["rows"][0]["cells"]["bank"] == "Kuveyt Türk"
    assert result.title == "Kuveyt Türk housing finance scenario"
