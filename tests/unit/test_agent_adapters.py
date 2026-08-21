"""Main-agent adapters expose summaries, not specialist internals."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.shared import agent_tools
from agents.shared import checkpoints
from agents.shared import specialists
from agents.main import agent as main_agent
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


def test_specialists_are_rebuilt_with_fresh_non_streaming_models(monkeypatch):
    """A graph must not pin a tunnel-bound model client across delegations."""
    model_calls: list[tuple[str, dict]] = []
    graphs: list[object] = []

    def fake_get_llm(role, **kwargs):
        model_calls.append((role, kwargs))
        return object()

    def fake_create_agent(**kwargs):
        graph = object()
        graphs.append(graph)
        return graph

    monkeypatch.setattr(specialists, "get_llm", fake_get_llm)
    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)
    monkeypatch.setattr(specialists, "build_bank_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(specialists, "get_checkpointer", lambda: object())
    monkeypatch.setattr(specialists, "prompt_for", lambda bank: bank)

    first = specialists.build_specialist("kuveytturk")
    second = specialists.build_specialist("kuveytturk")

    assert first is graphs[0]
    assert second is graphs[1]
    assert first is not second
    assert model_calls == [
        ("chat", {"disable_streaming": True}),
        ("chat", {"disable_streaming": True}),
    ]


def test_main_agent_is_rebuilt_without_losing_checkpoint_backed_memory(monkeypatch):
    """The graph is disposable; its checkpointer remains the state authority."""
    checkpointer = object()
    graphs: list[object] = []

    monkeypatch.setattr(main_agent, "get_llm", lambda role: object())
    monkeypatch.setattr(main_agent, "build_specialist_tools", lambda: [])
    monkeypatch.setattr(main_agent, "get_checkpointer", lambda: checkpointer)

    def fake_create_agent(**kwargs):
        assert kwargs["checkpointer"] is checkpointer
        graph = object()
        graphs.append(graph)
        return graph

    monkeypatch.setattr(main_agent, "create_agent", fake_create_agent)

    assert main_agent.build_main_agent() is graphs[0]
    assert main_agent.build_main_agent() is graphs[1]
    assert graphs[0] is not graphs[1]


def test_specialist_adapter_waits_for_the_final_retry_result(monkeypatch):
    """The supervisor tool cannot complete while its specialist is still retrying."""
    started = Event()
    release = Event()

    class _WaitingSpecialist:
        def invoke(self, payload, config, context):
            started.set()
            assert release.wait(timeout=1)
            return {"messages": [type("Message", (), {"content": "Final retry result"})()]}

    monkeypatch.setattr(
        agent_tools,
        "build_specialist",
        lambda bank, monthly_profit_rate=None: _WaitingSpecialist(),
    )
    spec = next(spec for spec in SPECS if spec.bank == "kuveytturk")
    tool = agent_tools.build_specialist_tool(spec)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(tool.func, "Get a live quote", _Runtime())
        assert started.wait(timeout=1)
        assert pending.done() is False
        release.set()
        assert pending.result(timeout=1) == "Final retry result"


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
