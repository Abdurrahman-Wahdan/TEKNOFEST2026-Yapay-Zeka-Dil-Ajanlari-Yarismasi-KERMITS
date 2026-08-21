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
    good = json.loads(live_result("vakif", "exchange_rates", lambda: [{"code": "USD"}]))
    assert good == {
        "bank": "vakif",
        "tool": "exchange_rates",
        "retrieved_at": good["retrieved_at"],
        "status": "ok",
        "data": [{"code": "USD"}],
    }
    assert good["retrieved_at"].endswith("Z")

    def refused():
        raise ValueError("This bank does not publish that calculator.")

    unavailable = json.loads(live_result("adil", "finance_quote", refused))
    assert unavailable["status"] == "unavailable"
    assert unavailable["bank"] == "adil"
    assert "does not publish" in unavailable["message"]
