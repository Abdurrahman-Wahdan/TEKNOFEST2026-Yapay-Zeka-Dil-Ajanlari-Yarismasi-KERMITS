"""Main-agent adapters expose summaries, not specialist internals."""

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.shared import agent_tools
from agents.shared import checkpoints
from agents.shared.registry import SPECS
from agents.shared.runtime import AgentContext

pytestmark = pytest.mark.unit


class _Specialist:
    def invoke(self, payload, config, context):
        assert payload == {"messages": [("user", "Get a live quote")]}
        assert config["configurable"]["thread_id"] == "chat-1:bank:kuveytturk"
        assert context == {"session_id": "chat-1"}
        return {"messages": [type("Message", (), {"content": "Live result"})()]}


class _Runtime:
    context = {"session_id": "chat-1"}


class _ToolCallingFake(FakeMessagesListChatModel):
    """The stock fake deliberately omits tool binding; agents require it."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def test_main_adapter_uses_its_bank_private_thread(monkeypatch):
    monkeypatch.setattr(
        agent_tools,
        "build_specialist",
        lambda bank, monthly_profit_rate=None: _Specialist(),
    )
    spec = next(spec for spec in SPECS if spec.bank == "kuveytturk")
    tool = agent_tools.build_specialist_tool(spec)

    # ToolRuntime is injected by LangChain's agent ToolNode; invoke the stored
    # callable directly here so this remains a no-model, no-graph unit test.
    result = tool.func("Get a live quote", _Runtime())

    assert result == "Live result"
    assert tool.name == "ask_kuveytturk"
    assert "request" in tool.args_schema.model_json_schema()["properties"]
    assert "bank" not in tool.args_schema.model_json_schema()["properties"]


def test_main_has_exactly_one_adapter_per_bank():
    tools = agent_tools.build_specialist_tools()
    assert [tool.name for tool in tools] == [spec.tool_name for spec in SPECS]


def test_langchain_v1_runtime_context_reaches_specialist_adapter(monkeypatch):
    monkeypatch.setattr(agent_tools, "build_specialist", lambda bank: _Specialist())
    spec = next(spec for spec in SPECS if spec.bank == "kuveytturk")
    tool = agent_tools.build_specialist_tool(spec)
    model = _ToolCallingFake(responses=[
        AIMessage(content="", tool_calls=[{
            "name": tool.name, "args": {"request": "Get a live quote"}, "id": "call-1"
        }]),
        AIMessage(content="The specialist answered."),
    ])
    agent = create_agent(
        model=model,
        tools=[tool],
        context_schema=AgentContext,
        checkpointer=InMemorySaver(),
    )

    result = agent.invoke(
        {"messages": [("user", "Ask Kuveyt Türk")]},
        config={"configurable": {"thread_id": "main-thread"}},
        context={"session_id": "chat-1"},
    )

    assert result["messages"][-1].content == "The specialist answered."


def test_deleting_a_chat_removes_main_and_every_private_specialist_memory(monkeypatch):
    deleted: list[str] = []

    class _Checkpointer:
        def delete_thread(self, thread_id):
            deleted.append(thread_id)

    monkeypatch.setattr(checkpoints, "get_checkpointer", lambda: _Checkpointer())
    checkpoints.delete_session_checkpoints("chat-1")

    assert deleted == [
        "chat-1:main", *[f"chat-1:bank:{spec.bank}" for spec in SPECS]
    ]
