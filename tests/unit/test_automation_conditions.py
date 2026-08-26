"""Typed live-value conditions: validation, evaluation and citations."""

import pytest
from pydantic import ValidationError

from api.automations import conditions
from api.schemas.automations import ConditionSpec
from banks.compare import Comparison
from banks.models import FinanceQuote, Product, Rate

pytestmark = pytest.mark.unit


class _Bank:
    def __init__(self, name: str, display_name: str, rate: Rate | None = None):
        self.name = name
        self.display_name = display_name
        self._rate = rate

    def find_rates(self, _codes):
        return [self._rate] if self._rate else []


def finance_quote(bank: str, installment: float) -> FinanceQuote:
    return FinanceQuote(
        bank=bank,
        product=Product(code="car", name="0 km Taşıt", category="finance"),
        amount=500_000,
        term=36,
        installment=installment,
        total=installment * 36,
        profit_rate=2.5,
        annual_cost_rate=35.0,
        fees={},
        schedule=[],
        raw={},
    )


def test_rejects_incompatible_live_operands():
    with pytest.raises(ValidationError):
        ConditionSpec.model_validate(
            {
                "left": {
                    "source": "finance",
                    "bank": "kuveytturk",
                    "family": "tasit-0km",
                    "amount": 500_000,
                    "term_months": 36,
                    "metric": "monthly_installment",
                },
                "operator": "lt",
                "right": {
                    "source": "finance",
                    "bank": "albaraka",
                    "family": "tasit-0km",
                    "amount": 500_000,
                    "term_months": 48,
                    "metric": "monthly_installment",
                },
            }
        )


def test_finance_comparison_asks_only_the_two_named_banks(monkeypatch):
    captured = {}

    def compare_finance(family, amount, term, banks):
        captured.update(family=family, amount=amount, term=term, banks=banks)
        return Comparison(
            "finance",
            family,
            quotes=[
                finance_quote("kuveytturk", 20_000),
                finance_quote("albaraka", 21_000),
            ],
        )

    displays = {
        "kuveytturk": "Kuveyt Türk",
        "albaraka": "Albaraka Türk",
    }
    monkeypatch.setattr(conditions.compare, "finance", compare_finance)
    monkeypatch.setattr(
        conditions,
        "get_bank",
        lambda bank: _Bank(bank, displays[bank]),
    )
    spec = ConditionSpec.model_validate(
        {
            "left": {
                "source": "finance",
                "bank": "kuveytturk",
                "family": "tasit-0km",
                "amount": 500_000,
                "term_months": 36,
            },
            "operator": "lt",
            "right": {
                "source": "finance",
                "bank": "albaraka",
                "family": "tasit-0km",
                "amount": 500_000,
                "term_months": 36,
            },
        }
    )

    result = conditions.evaluate_condition(spec)

    assert result.matched is True
    assert captured["banks"] == ["albaraka", "kuveytturk"]
    assert result.observations["left"]["value"] == 20_000
    assert result.observations["right"]["value"] == 21_000
    assert len(result.citations) == 2


def test_gold_threshold_carries_unit_and_bank_source(monkeypatch):
    gold = Rate(
        code="ALT (gr)",
        name="Gram Altın",
        buy=6900,
        sell=7100,
        quote_currency="TRY",
    )
    monkeypatch.setattr(
        conditions,
        "get_bank",
        lambda bank: _Bank(bank, "Kuveyt Türk", gold),
    )
    spec = ConditionSpec.model_validate(
        {
            "left": {
                "source": "bank_rate",
                "bank": "kuveytturk",
                "code": "XAU",
                "side": "sell",
            },
            "operator": "gte",
            "right": {"source": "constant", "value": 7000},
        }
    )

    result = conditions.evaluate_condition(spec)

    assert result.matched is True
    assert result.observations["right"]["display_value"] == "7.000,00 TRY"
    assert result.citations[0]["cite_url"].startswith("https://www.kuveytturk.com.tr")


def test_rejects_two_live_rates_with_different_units(monkeypatch):
    rates = {
        "kuveytturk": Rate(
            code="ALT (gr)", name="Gram Altın", buy=6900, sell=7100,
            unit=1, quote_currency="TRY",
        ),
        "albaraka": Rate(
            code="XAU", name="Ons Altın", buy=2400, sell=2450,
            unit=1, quote_currency="USD",
        ),
    }
    monkeypatch.setattr(
        conditions,
        "get_bank",
        lambda bank: _Bank(bank, bank, rates[bank]),
    )
    spec = ConditionSpec.model_validate(
        {
            "left": {
                "source": "bank_rate", "bank": "kuveytturk",
                "code": "XAU", "side": "sell",
            },
            "operator": "lt",
            "right": {
                "source": "bank_rate", "bank": "albaraka",
                "code": "XAU", "side": "sell",
            },
        }
    )

    with pytest.raises(ValueError, match="different units"):
        conditions.evaluate_condition(spec)
