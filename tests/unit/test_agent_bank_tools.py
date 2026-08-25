"""The specialist-only live tool surface never leaks a bank selector."""

import json

import pytest

from agents.shared.bank_tools import build_bank_tools
from agents.shared.results import live_result
from agents.shared.registry import SPECS, prompt_for
from banks.factory import get_bank

pytestmark = pytest.mark.unit


def test_every_registered_bank_has_a_private_prompt():
    assert len(SPECS) == 10
    for spec in SPECS:
        assert spec.bank
        assert prompt_for(spec.bank).strip()


def test_bank_bound_tools_have_no_bank_argument_and_only_advertised_capabilities():
    capability_tools = {
        "products": "list_products",
        "finance": "finance_quote",
        "profit_share": "profit_share_quote",
        "rates": "exchange_rates",
        "card": "card_installment_quote",
        "convert": "convert_currency",
        "mile_rates": "mile_earning_rates",
    }
    forbidden = {"compare_finance", "compare_profit_share", "compare_card", "compare_exchange", "list_banks"}

    for spec in SPECS:
        bank = get_bank(spec.bank)
        tools = build_bank_tools(spec.bank)
        names = {tool.name for tool in tools}
        assert "check_live_endpoint_health" in names
        assert not names & forbidden
        for capability, tool_name in capability_tools.items():
            assert (tool_name in names) == (capability in bank.capabilities)
        for tool in tools:
            schema = tool.args_schema.model_json_schema()
            assert "bank" not in schema.get("properties", {})


def test_only_calculators_that_accept_a_custom_rate_expose_that_input():
    custom_rate_banks = {
        "kuveytturk", "albaraka", "tom", "vakif", "dunya", "emlak",
        "ziraat", "turkiyefinans",
    }
    for spec in SPECS:
        finance = next(
            (tool for tool in build_bank_tools(spec.bank) if tool.name == "finance_quote"),
            None,
        )
        if finance is None:
            continue
        schema = finance.args_schema.model_json_schema()
        properties = schema["properties"]
        assert ("monthly_profit_rate" in properties) == (spec.bank in custom_rate_banks)
        if spec.bank in custom_rate_banks:
            assert "monthly_profit_rate" in schema["required"]


def test_live_result_is_compact_timestamped_and_keeps_refusals_honest():
    good = json.loads(live_result(
        "vakif",
        "exchange_rates",
        lambda: [{"code": "USD"}],
        source_url="https://www.vakifkatilim.com.tr/tr/yardimci-sayfalar/hesaplama-araclari",
        source_title="Vakıf Katılım Hesaplama Araçları",
    ))
    assert good == {
        "bank": "vakif",
        "tool": "exchange_rates",
        "retrieved_at": good["retrieved_at"],
        "source_type": "live_endpoint",
        "source_url": "https://www.vakifkatilim.com.tr/tr/yardimci-sayfalar/hesaplama-araclari",
        "source_title": "Vakıf Katılım Hesaplama Araçları",
        "status": "ok",
        "data": [{"code": "USD"}],
    }
    # Turkey time, not UTC. A model repeats a timestamp verbatim, so a UTC stamp
    # here is what made a rate fetched at 11:04 read as 08:04 to a Turkish user.
    assert good["retrieved_at"].endswith("+03:00")

    def refused():
        raise ValueError("This bank does not publish that calculator.")

    unavailable = json.loads(live_result("adil", "finance_quote", refused))
    assert unavailable["status"] == "unavailable"
    assert unavailable["bank"] == "adil"
    assert "does not publish" in unavailable["message"]


def test_every_live_capability_has_an_official_public_source_page():
    from agents.shared.bank_tools import _live_source

    capability_tools = {
        "products": "list_products",
        "finance": "finance_quote",
        "profit_share": "profit_share_quote",
        "rates": "exchange_rates",
        "card": "card_installment_quote",
        "convert": "convert_currency",
        "mile_rates": "mile_earning_rates",
    }
    for spec in SPECS:
        bank = get_bank(spec.bank)
        for capability in bank.capabilities:
            url, title = _live_source(spec.bank, capability_tools[capability])
            assert url.startswith("https://"), (spec.bank, capability)
            assert title, (spec.bank, capability)


# --- corpus retrieval on the specialist surface ------------------------------


class _Recorder:
    """A Qdrant stand-in that keeps the filter it was handed."""

    def __init__(self):
        self.filters = []

    def query_points(self, **kwargs):
        self.filters.append(kwargs.get("query_filter"))
        return type("R", (), {"points": []})()

    def scroll(self, **kwargs):
        self.filters.append(kwargs.get("scroll_filter"))
        return [], None


def _bank_values(condition) -> set[str]:
    """Every value a filter demands of `metadata.bank`, at any depth."""
    from qdrant_client import models

    if condition is None:
        return set()
    if isinstance(condition, models.Filter):
        return set().union(*(
            _bank_values(c) for c in (*(condition.must or ()), *(condition.should or ()))
        ), set())
    key = getattr(condition, "key", None)
    match = getattr(condition, "match", None)
    return {match.value} if key == "metadata.bank" and match is not None else set()


def test_every_specialist_can_read_what_its_own_bank_published():
    for spec in SPECS:
        names = {tool.name for tool in build_bank_tools(spec.bank)}
        assert {"search_bank", "expand_chunk", "read_full_page"} <= names, spec.bank


def test_retrieval_is_bound_to_the_name_the_store_holds_not_the_provider_key(monkeypatch):
    """The two disagree for seven of the ten banks, and a wrong filter here
    returns nothing rather than raising -- so the specialist would be told,
    forever and plausibly, that its bank has published nothing."""
    from corpus import search
    from corpus.sites import get_site

    for spec in SPECS:
        recorder = _Recorder()
        monkeypatch.setattr(search, "_shared", lambda: (None, recorder))
        monkeypatch.setattr(search, "embed_query", lambda q, task=None: [0.0] * 1024)

        tools = {tool.name: tool for tool in build_bank_tools(spec.bank)}
        tools["search_bank"].invoke({"query": "kâr payı", "intent": "oran bul"})
        tools["expand_chunk"].invoke({"point_id": "whatever"})
        tools["read_full_page"].invoke({"url": "https://example.test/x"})

        expected = get_site(spec.bank).corpus_slug
        assert recorder.filters, spec.bank
        for used in recorder.filters:
            assert _bank_values(used) == {expected}, f"{spec.bank}: {_bank_values(used)}"


def test_a_specialist_prunes_before_it_compacts(monkeypatch):
    """Order is load-bearing: compaction must measure the thread the model
    actually keeps, not one still carrying passages it asked to drop."""
    from agents.shared import specialists
    from agents.shared.retrieval_memory import RetrievalPruning

    captured = {}
    monkeypatch.setattr(specialists, "get_llm", lambda *a, **k: object())
    monkeypatch.setattr(specialists, "get_checkpointer", lambda: None)
    monkeypatch.setattr(specialists, "resolve_model_key", lambda key: "gemma")
    monkeypatch.setattr(specialists, "usable_context_window", lambda *a, **k: 100_000)
    monkeypatch.setattr(specialists, "build_compaction",
                        lambda window, specialist: "COMPACTION")
    monkeypatch.setattr(specialists, "create_agent",
                        lambda **kwargs: captured.update(kwargs))

    specialists.build_specialist("vakif")
    order = captured["middleware"]
    assert isinstance(order[0], RetrievalPruning)
    assert order[-1] == "COMPACTION"
    assert [m.tool_name for m in order[1:-1]] == [
        "search_bank", "expand_chunk", "read_full_page"]


def test_the_specialist_prompt_says_a_published_page_is_not_a_live_quote():
    from agents.shared.specialists import CORPUS_GUIDANCE

    assert "not live data" in CORPUS_GUIDANCE
    assert "expand_chunk" in CORPUS_GUIDANCE
    assert "not_useful" in CORPUS_GUIDANCE
