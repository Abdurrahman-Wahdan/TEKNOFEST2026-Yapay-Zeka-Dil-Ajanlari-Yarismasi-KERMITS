"""Main-agent adapters expose summaries, not specialist internals."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
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
        # The private thread wins over the supervisor's, so the specialist's
        # memory never lands on the conversation's thread.
        assert config["configurable"]["thread_id"] == "chat-1:bank:kuveytturk"
        # ...but everything else on the supervisor's config is carried across.
        # Without this the parent's callbacks never reach the specialist and its
        # token spend is invisible to anything watching the supervisor.
        assert config["callbacks"] == ["the supervisor's handlers"]
        assert context == {"session_id": "chat-1"}
        return {"messages": [type("Message", (), {"content": "Live result"})()]}


class _Runtime:
    """What LangChain's ToolNode injects, as far as the adapter uses it.

    `config` carries the supervisor's run: its callbacks are what let anything
    observe a specialist's token spend, and its `thread_id` is what the adapter
    must override so a specialist's memory stays private.
    """

    context = {"session_id": "chat-1"}
    config = {
        "configurable": {"thread_id": "main-thread"},
        "callbacks": ["the supervisor's handlers"],
    }


class _WebRuntime(_Runtime):
    context = {"session_id": "chat-1", "web_search_enabled": True}


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
    assert "web_research_required" in tool.args_schema.model_json_schema()["properties"]
    assert "bank" not in tool.args_schema.model_json_schema()["properties"]


def test_explicit_web_requirement_reaches_the_specialist_prompt(monkeypatch):
    built: dict = {}

    class _WebSpecialist:
        def invoke(self, payload, config, context):
            return {"messages": [
                ToolMessage(
                    name="search_bank_web",
                    tool_call_id="search-1",
                    content=json.dumps({
                        "bank": "kuveytturk",
                        "source_type": "web_search",
                        "status": "no_results",
                        "results": [],
                    }),
                ),
                AIMessage(content="The web search returned no results."),
            ]}

    def build(bank, monthly_profit_rate=None, **kwargs):
        built.update(bank=bank, monthly_profit_rate=monthly_profit_rate, **kwargs)
        return _WebSpecialist()

    monkeypatch.setattr(agent_tools, "build_specialist", build)
    spec = next(spec for spec in SPECS if spec.bank == "kuveytturk")
    tool = agent_tools.build_specialist_tool(spec)

    result = tool.func(
        "Search the internet and find everything available.",
        _WebRuntime(),
        web_research_required=True,
    )

    assert built == {
        "bank": "kuveytturk",
        "monthly_profit_rate": None,
        "web_research_enabled": True,
        "web_research_required": True,
    }
    assert "search_bank_web" in result


def test_required_web_research_refuses_to_fall_back_when_toggle_is_off(monkeypatch):
    monkeypatch.setattr(
        agent_tools,
        "build_specialist",
        lambda *args, **kwargs: pytest.fail("specialist must not run"),
    )
    spec = next(spec for spec in SPECS if spec.bank == "kuveytturk")
    tool = agent_tools.build_specialist_tool(spec)

    result = tool.func(
        "Search the internet.", _Runtime(), web_research_required=True
    )

    assert "Web search is disabled" in result
    assert "indexed retrieval does not satisfy" in result


def test_all_bank_coverage_does_not_require_web_when_toggle_is_off(monkeypatch):
    class _AvailableSourcesSpecialist:
        def invoke(self, payload, config, context):
            assert "her banka" in payload["messages"][0][1]
            return {"messages": [
                ToolMessage(
                    name="profit_share_quote",
                    tool_call_id="quote-1",
                    content=json.dumps({
                        "bank": "kuveytturk",
                        "source_type": "live_endpoint",
                        "retrieved_at": "2026-08-25T10:00:00+00:00",
                        "status": "ok",
                    }),
                ),
                AIMessage(content="The live endpoint supplied the current result."),
            ]}

    monkeypatch.setattr(
        agent_tools,
        "build_specialist",
        lambda bank, monthly_profit_rate=None: _AvailableSourcesSpecialist(),
    )
    spec = next(spec for spec in SPECS if spec.bank == "kuveytturk")
    tool = agent_tools.build_specialist_tool(spec)

    result = tool.func(
        "Güncel kâr oranlarını her banka için bulabilir misin?",
        _Runtime(),
        web_research_required=False,
    )

    assert "current result" in result
    assert "profit_share_quote" in result
    assert "enable Web search" not in result


def test_web_requirement_schema_distinguishes_sources_from_bank_coverage():
    description = agent_tools.DelegateInput.model_json_schema()["properties"][
        "web_research_required"
    ]["description"]

    assert "only when" in description
    assert "all or every bank" in description
    assert "her banka" in description


def test_required_web_research_gets_one_corrective_retry(monkeypatch):
    calls = 0

    class _RetryingSpecialist:
        def invoke(self, payload, config, context):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"messages": [AIMessage(content="Indexed answer only.")]}
            return {"messages": [
                AIMessage(content="Indexed answer only."),
                ToolMessage(
                    name="search_bank_web",
                    tool_call_id="search-1",
                    content=json.dumps({
                        "bank": "kuveytturk",
                        "source_type": "web_search",
                        "status": "no_results",
                        "results": [],
                    }),
                ),
                AIMessage(content="Corrected web answer."),
            ]}

    monkeypatch.setattr(
        agent_tools,
        "build_specialist",
        lambda *args, **kwargs: _RetryingSpecialist(),
    )
    spec = next(spec for spec in SPECS if spec.bank == "kuveytturk")
    tool = agent_tools.build_specialist_tool(spec)

    result = tool.func(
        "Find everything online.", _WebRuntime(), web_research_required=True
    )

    assert calls == 2
    assert "Corrected web answer." in result
    assert "search_bank_web" in result


def test_adapter_machine_preserves_tool_evidence_the_specialist_omitted():
    result = {"messages": [
        ToolMessage(
            name="read_bank_source",
            tool_call_id="call-1",
            content=json.dumps({
                "bank": "vakif",
                "source_type": "live_web_page",
                "retrieved_at": "2026-08-24T19:00:00+00:00",
                "status": "ok",
                "url": "https://www.vakifkatilim.com.tr/tr/musteri-ol",
                "text": "large source body that must stay private",
            }),
        ),
        AIMessage(content=(
            "The page supports the claim "
            "[Müşteri Ol](https://www.vakifkatilim.com.tr/tr/musteri-ol)."
        )),
    ]}
    handoff = agent_tools._final_text(result)
    assert "TF26_TOOL_EVIDENCE" in handoff
    assert "live_web_page" in handoff
    assert "2026-08-24T19:00:00+00:00" in handoff
    assert "https://www.vakifkatilim.com.tr/tr/musteri-ol" in handoff
    assert "large source body" not in handoff


def test_web_citations_are_derived_only_from_actual_web_tool_evidence():
    messages = [
        ToolMessage(
            name="search_bank_web",
            tool_call_id="search-1",
            content=json.dumps({
                "bank": "vakif",
                "source_type": "web_search",
                "status": "ok",
                "results": [{
                    "title": "Konut Finansmanı",
                    "url": "https://www.vakifkatilim.com.tr/tr/konut-finansmani",
                    "snippet": "A search hint that stays private.",
                }],
            }),
        ),
        ToolMessage(
            name="read_bank_source",
            tool_call_id="read-1",
            content=json.dumps({
                "bank": "vakif",
                "source_type": "live_web_page",
                "status": "ok",
                "title": "Müşteri Ol",
                "url": "https://www.vakifkatilim.com.tr/tr/musteri-ol",
                "text": "The private page body.",
            }),
        ),
        ToolMessage(
            name="finance_quote",
            tool_call_id="quote-1",
            content=json.dumps({"bank": "vakif", "status": "ok"}),
        ),
    ]
    handoff = agent_tools._final_text({
        "messages": [*messages, AIMessage(content=(
            "Use [Konut Finansmanı]"
            "(https://www.vakifkatilim.com.tr/tr/konut-finansmani) and "
            "[Müşteri Ol](https://www.vakifkatilim.com.tr/tr/musteri-ol)."
        ))]
    })
    public_message = ToolMessage(
        name="ask_vakif", tool_call_id="delegate-1", content=handoff
    )

    sources = agent_tools.used_sources_from_tool_message(public_message)

    assert sources == [
        {
            "url": "https://www.vakifkatilim.com.tr/tr/konut-finansmani",
            "title": "Konut Finansmanı",
            "bank": "vakif",
            "source_type": "web_search",
            "provenance": "live_web",
        },
        {
            "url": "https://www.vakifkatilim.com.tr/tr/musteri-ol",
            "title": "Müşteri Ol",
            "bank": "vakif",
            "source_type": "live_web_page",
            "provenance": "live_web",
        },
    ]
    assert "A search hint that stays private." not in handoff
    assert "The private page body." not in handoff


def test_unused_web_results_never_cross_the_specialist_handoff():
    used_url = "https://www.vakifkatilim.com.tr/tr/konut-finansmani"
    unused_url = "https://www.vakifkatilim.com.tr/tr/kampanyalar"
    message = ToolMessage(
        name="search_bank_web",
        tool_call_id="search-1",
        content=json.dumps({
            "bank": "vakif",
            "source_type": "web_search",
            "status": "ok",
            "results": [
                {"title": "Konut", "url": used_url, "snippet": "used"},
                {"title": "Kampanyalar", "url": unused_url, "snippet": "unused"},
            ],
        }),
    )
    handoff = agent_tools._final_text({"messages": [
        message,
        AIMessage(content=f"Konut bilgisi [Konut]({used_url})."),
    ]})

    assert used_url in handoff
    assert unused_url not in handoff


def test_qdrant_and_web_sources_keep_separate_machine_provenance():
    indexed_url = "https://www.vakifkatilim.com.tr/tr/bilgi-bankasi"
    web_url = "https://www.vakifkatilim.com.tr/tr/guncel-bilgi"
    messages = [
        ToolMessage(
            name="search_bank",
            tool_call_id="index-1",
            content=f"[1] point_id=abc url={indexed_url}\nIndexed fact",
        ),
        ToolMessage(
            name="search_bank_web",
            tool_call_id="web-1",
            content=json.dumps({
                "bank": "vakif",
                "source_type": "web_search",
                "status": "ok",
                "results": [{"title": "Güncel Bilgi", "url": web_url}],
            }),
        ),
        AIMessage(content=(
            f"Arşiv bilgisi [Bilgi Bankası]({indexed_url}); "
            f"güncel bilgi [Güncel Bilgi]({web_url})."
        )),
    ]
    handoff = agent_tools._final_text({"messages": messages})
    public_message = ToolMessage(
        name="ask_vakif", tool_call_id="delegate-1", content=handoff
    )

    sources = agent_tools.used_sources_from_tool_message(public_message)

    assert {(source["url"], source["provenance"]) for source in sources} == {
        (indexed_url, "knowledge_base"),
        (web_url, "live_web"),
    }
    assert next(
        source for source in sources if source["url"] == indexed_url
    )["source_type"] == "indexed_document"


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
    monkeypatch.setattr(specialists, "usable_context_window", lambda *args: 100_000)
    monkeypatch.setattr(specialists, "build_compaction", lambda *args, **kwargs: object())

    first = specialists.build_specialist("kuveytturk")
    second = specialists.build_specialist("kuveytturk")

    assert first is graphs[0]
    assert second is graphs[1]
    assert first is not second
    assert model_calls == [
        ("chat", {"disable_streaming": True}),
        ("chat", {"disable_streaming": True}),
    ]


def test_required_web_specialist_prompt_makes_search_bank_web_mandatory(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(specialists, "get_llm", lambda *args, **kwargs: object())
    monkeypatch.setattr(specialists, "build_bank_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(specialists, "get_checkpointer", lambda: object())
    monkeypatch.setattr(specialists, "prompt_for", lambda bank: bank)
    monkeypatch.setattr(specialists, "usable_context_window", lambda *args: 100_000)
    monkeypatch.setattr(specialists, "build_compaction", lambda *args, **kwargs: object())

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(specialists, "create_agent", fake_create_agent)

    specialists.build_specialist(
        "kuveytturk",
        web_research_enabled=True,
        web_research_required=True,
    )

    prompt = " ".join(captured["system_prompt"].split())
    assert "REQUIRES web discovery" in prompt
    assert "search_bank_web" in prompt
    assert "at least once" in prompt
    assert "Indexed search_bank" in prompt


def test_specialist_prompt_prioritizes_live_and_indexed_tools_without_web(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(specialists, "get_llm", lambda *args, **kwargs: object())
    monkeypatch.setattr(specialists, "build_bank_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(specialists, "get_checkpointer", lambda: object())
    monkeypatch.setattr(specialists, "prompt_for", lambda bank: bank)
    monkeypatch.setattr(specialists, "usable_context_window", lambda *args: 100_000)
    monkeypatch.setattr(specialists, "build_compaction", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        specialists,
        "create_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    specialists.build_specialist("kuveytturk", web_research_enabled=False)

    prompt = " ".join(captured["system_prompt"].split())
    assert "live endpoint tools first" in prompt
    assert "Use search_bank, expand_chunk, and read_full_page" in prompt
    assert "Web search being absent never removes" in prompt


def test_main_agent_is_rebuilt_without_losing_checkpoint_backed_memory(monkeypatch):
    """The graph is disposable; its checkpointer remains the state authority."""
    checkpointer = object()
    graphs: list[object] = []

    # Takes the keyword too: the supervisor now passes the composer's thinking
    # switch through to the factory.
    monkeypatch.setattr(main_agent, "get_llm", lambda role, **kwargs: object())
    monkeypatch.setattr(main_agent, "build_specialist_tools", lambda: [])
    monkeypatch.setattr(main_agent, "get_checkpointer", lambda: checkpointer)
    monkeypatch.setattr(main_agent, "usable_context_window", lambda *args: 100_000)
    monkeypatch.setattr(main_agent, "build_compaction", lambda *args, **kwargs: object())

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
        "chat-1:main",
        "chat-1:recommendation",
        *[f"chat-1:bank:{spec.bank}" for spec in SPECS],
    ]


# --- compare/retrieval: the live collection's metadata names -------------------
class TestCompareRetrievalMetadata:
    """`search_bank` reads the chunk payloads the corpus embedder wrote.

    Two naming generations exist. Every one of the 7030 points in the live
    `campaigns` collection carries `url` / `validity_status` /
    `gecerlilik_bitis`; **none** carries the `source_url` / `campaign_end` /
    `campaign_status` names `dataprep/embed.py` writes. Reading only the latter
    returned an empty url on every hit and made the expired-campaign filter a
    no-op that hid nothing -- 905 expired chunks, 13% of the collection, were
    being offered to the research subagents as current.
    """

    def test_url_is_read_from_either_generation(self):
        from dataprep.compare.retrieval import _source_url

        assert _source_url({"url": "https://a/new"}) == "https://a/new"
        assert _source_url({"source_url": "https://a/old"}) == "https://a/old"
        assert _source_url({"pdf_url": "https://a/pdf"}) == "https://a/pdf"
        assert _source_url({"source_page": "https://a/page"}) == "https://a/page"
        assert _source_url({}) == ""

    def test_new_name_wins_when_both_are_present(self):
        from dataprep.compare.retrieval import _source_url

        assert _source_url({"url": "https://new", "source_url": "https://old"}) == "https://new"

    def test_an_expired_end_date_is_filtered(self):
        from dataprep.compare.retrieval import _end_date, _expired

        meta = {"gecerlilik_bitis": "2020-01-01"}
        assert _expired(meta, _end_date(meta)) is True

    def test_a_future_end_date_is_kept(self):
        from dataprep.compare.retrieval import _end_date, _expired

        meta = {"gecerlilik_bitis": "2099-12-31"}
        assert _expired(meta, _end_date(meta)) is False

    def test_no_date_falls_back_to_the_producer_verdict(self):
        """Both generations spell it differently, and neither means 'no date is
        an expiry' -- a page that never stated a deadline stays."""
        from dataprep.compare.retrieval import _end_date, _expired

        for meta in ({"validity_status": "suresi_gecmis"}, {"campaign_status": "bitti"}):
            assert _expired(meta, _end_date(meta)) is True
        for meta in ({"validity_status": "bilinmiyor"}, {"validity_status": "gecerli"}, {}):
            assert _expired(meta, _end_date(meta)) is False

    def test_a_live_date_outranks_a_stale_expiry_stamp(self):
        """The stamp was written when the crawl ran; the date keeps being true
        afterwards, so the date decides."""
        from dataprep.compare.retrieval import _end_date, _expired

        meta = {"gecerlilik_bitis": "2099-12-31", "validity_status": "suresi_gecmis"}
        assert _expired(meta, _end_date(meta)) is False
