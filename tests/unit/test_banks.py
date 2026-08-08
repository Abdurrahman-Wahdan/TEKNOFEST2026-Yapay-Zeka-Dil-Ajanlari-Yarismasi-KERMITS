"""Bank registry, response parsing and refusals.

No network. The transport is replaced with recorded payloads so the real
parsing code runs against what the banks actually returned; the fixtures in
tests/fixtures/banks were captured from the live endpoints, because the probe
captures in docs/discovery truncate every response at 6000 characters and the
large ones there are not valid JSON.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from banks import build_tools, get_bank, list_banks
from banks.providers import BANKS, albaraka, get_provider, kuveytturk
from banks.providers.base import UnsupportedProduct, fold

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "banks"


def load(bank: str, name: str):
    text = (FIXTURES / bank / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


def serve(monkeypatch, module, payloads: list, text: str = ""):
    """Answer each call from `payloads` in order, then repeat the last one.

    Repeating matters: the profit-share providers try more than one reading of a
    term, and an all-zero fixture has to stay zero on every attempt.
    """
    queue = list(payloads)

    def fake_json(*args, **kwargs):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(module, "request_json", fake_json)
    if hasattr(module, "request_text"):
        monkeypatch.setattr(module, "request_text", lambda *a, **k: text)


# ----- registry -----


def test_both_banks_resolve():
    assert get_bank("kuveytturk").name == "kuveytturk"
    assert get_bank("albaraka").name == "albaraka"
    assert get_bank("KuveytTurk").name == "kuveytturk"


def test_unknown_bank_lists_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        get_provider("garanti")
    message = str(exc.value)
    assert "garanti" in message
    for bank in BANKS:
        assert bank.name in message


def test_capabilities_are_honest():
    """A bank that does not publish something must say so, not answer nothing."""
    assert "card" in list_banks()["kuveytturk"]
    assert "card" not in list_banks()["albaraka"]


def test_a_bank_without_a_card_calculator_refuses():
    with pytest.raises(UnsupportedProduct, match="does not publish"):
        get_bank("albaraka").card_installment_quote("any", 1000, 3)


def test_a_bank_without_a_category_refuses():
    with pytest.raises(UnsupportedProduct, match="card"):
        get_bank("albaraka").products("card")


# ----- Turkish name resolution -----


def test_turkish_folding_survives_dotted_and_dotless_i():
    """"İ".lower() leaves a combining dot, so a model typing ASCII never matches."""
    assert fold("İhtiyaç Finansmanı") == fold("IHTIYAC FINANSMANI")
    assert fold("ihtiyaç finansmanı") == fold("İHTİYAÇ FİNANSMANI")
    assert fold("Sağlam Kart Troy") == "saglamkarttroy"


def test_find_product_accepts_the_code_and_the_turkish_name(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "catalogue_finance.json")])
    by_code = bank.find_product("finance", "SAGLIKFINANSMANI")
    for spelling in ("İhtiyaç Finansmanı", "ihtiyac finansmani", "IHTIYAC FINANSMANI"):
        assert bank.find_product("finance", spelling) == by_code


def test_find_product_lists_the_alternatives_when_nothing_matches(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "catalogue_finance.json")])
    with pytest.raises(UnsupportedProduct) as exc:
        bank.find_product("finance", "mortgage")
    assert "İhtiyaç Finansmanı" in str(exc.value)


# ----- Kuveyt Türk parsing -----


def test_kuveytturk_catalogue_maps_onto_products(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "catalogue_finance.json")])
    products = bank.products("finance")

    assert len(products) == 19
    assert all(p.code and p.name and p.category == "finance" for p in products)
    shopping = next(p for p in products if p.code == "ECOMMERCE")
    assert shopping.name == "Alışveriş Finansmanı"
    assert (shopping.min_term, shopping.max_term) == (1, 36)
    assert (shopping.min_amount, shopping.max_amount) == (1000.0, 5000000.0)


def test_kuveytturk_profit_share_products_carry_currencies(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "catalogue_profit_share.json")])
    accounts = {p.name: p for p in bank.products("profit_share")}

    # TL-only, and the FEC list is the only place that is stated.
    assert accounts["Hoş Geldin Katılma Hesabı"].currencies == ("TRY",)
    assert accounts["Katılma Hesabı"].currencies == ("TRY", "USD", "EUR")
    # Three accounts publish no code of their own and are still nameable.
    assert accounts["Katılma Hesabı"].code


def test_kuveytturk_finance_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [
        load("kuveytturk", "catalogue_finance.json"),
        load("kuveytturk", "finance_quote.json"),
    ])
    quote = bank.finance_quote("IHTIYACKART", 100000, 24)

    assert quote.bank == "kuveytturk"
    assert quote.installment == 7136.18
    assert quote.total == 171268.23
    # InstallmentCount comes back as a string.
    assert quote.term == 24 and isinstance(quote.term, int)
    assert len(quote.schedule) == 24
    assert quote.fees["allocation"] == 575.0
    first = quote.schedule[0]
    assert first.order == 1
    assert first.taxes == pytest.approx(first.amount - first.principal - first.profit)
    assert first.due_date == "2026-08-10"


def test_kuveytturk_profit_share_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [
        load("kuveytturk", "catalogue_profit_share.json"),
        load("kuveytturk", "profit_share_quote.json"),
    ])
    quote = bank.profit_share_quote("Katılma Hesabı", 100000, 12, "TRY", "day")

    assert quote.ratio == 86.0
    assert quote.net_profit == pytest.approx(831.059622)
    assert quote.term_unit == "day"
    assert quote.term == 12


def test_kuveytturk_months_are_sent_as_days(monkeypatch):
    """The endpoint counts days whatever the day/month flag says.

    p3=12 returns the same 12-day profit with the flag either way, so a term
    given in months has to be converted before it is sent or a year's profit
    comes back as twelve days' worth.
    """
    sent = []
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [
        load("kuveytturk", "catalogue_profit_share.json"),
        load("kuveytturk", "profit_share_quote.json"),
    ])
    real = kuveytturk.request_json

    def spy(*args, **kwargs):
        sent.append(kwargs.get("json"))
        return real(*args, **kwargs)

    monkeypatch.setattr(kuveytturk, "request_json", spy)
    quote = bank.profit_share_quote("Katılma Hesabı", 100000, 12, "TRY", "month")

    assert sent[-1]["p3"] == "360"
    assert quote.term == 360


def test_kuveytturk_all_zero_profit_share_raises(monkeypatch):
    """An unsupported combination answers 200 with every field zero.

    Returning that as a quote would report a real product as paying nothing.
    """
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [
        load("kuveytturk", "catalogue_profit_share.json"),
        load("kuveytturk", "profit_share_zeros.json"),
    ])
    with pytest.raises(UnsupportedProduct, match="no profit-share rate"):
        bank.profit_share_quote("Katılma Hesabı", 100000, 31, "TRY", "day")


def test_kuveytturk_refuses_yuvam_without_calling(monkeypatch):
    """The bank publishes no Yuvam rate at all, on its own page too."""
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "catalogue_profit_share.json")])
    with pytest.raises(UnsupportedProduct, match="Yuvam"):
        bank.profit_share_quote("Yuvam TL Katılma Hesabı", 100000, 31, "USD")


def test_kuveytturk_refuses_a_currency_the_product_does_not_take(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "catalogue_profit_share.json")])
    with pytest.raises(UnsupportedProduct, match="TRY"):
        bank.profit_share_quote("Hoş Geldin Katılma Hesabı", 100000, 31, "EUR")


def test_kuveytturk_card_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [
        load("kuveytturk", "catalogue_card.json"),
        load("kuveytturk", "card_quote.json"),
    ])
    quote = bank.card_installment_quote("Sağlam Kart Troy", 10000, 6)

    # The bank's own field name for the instalment carries a typo.
    assert quote.installment == 1900.64
    assert quote.total == 11403.64
    assert quote.profit_rate == 2.99
    assert quote.card.code == "SK"


def test_kuveytturk_rates_mark_metals_by_the_gram(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "rates.json")])
    rates = {r.code: r for r in bank.rates()}

    assert len(rates) >= 20
    assert rates["USD"].sell > rates["USD"].buy
    assert rates["ALT (gr)"].unit == "gram"
    assert rates["ZCeyrek"].unit == "coin"
    assert rates["USD"].unit == "1"


def test_kuveytturk_conversion_is_flagged_as_derived(monkeypatch):
    """Kuveyt Türk has no converter, so the multiplication happens here.

    That is the one agreed exception to never computing a number, and the
    caller has to be able to tell it apart from a bank-calculated figure.
    """
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "rates.json")])
    result = bank.convert("XAU", "TRY", 10)

    assert result.derived is True
    assert isinstance(result.result, Decimal)
    gold = next(r for r in load("kuveytturk", "rates.json") if r["CurrencyCode"] == "ALT (gr)")
    assert result.result == Decimal(str(gold["BuyRate"])) * 10


def test_kuveytturk_refuses_a_currency_it_does_not_quote(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "rates.json")])
    with pytest.raises(UnsupportedProduct, match="does not quote"):
        bank.convert("BTC", "TRY", 1)


# ----- Albaraka parsing -----


def test_albaraka_number_parsing_handles_both_separators():
    """Amounts and rates are formatted differently in the same response.

    money() on a rate gives 36731684, and a string test against "0,00 TRY"
    silently passes "0,00 USD", so both have to be parsed as numbers.
    """
    assert albaraka.money("18.114,26 TRY") == 18114.26
    assert albaraka.money("0,00 USD") == 0.0
    assert albaraka.money("6.684,28 TL") == 6684.28
    assert albaraka.rate("% 36.731684") == pytest.approx(36.731684)
    assert albaraka.rate("% 0,175") == 0.175
    assert albaraka.rate("% 64,46") == 64.46
    assert albaraka.rate("3,21") == 3.21


def test_albaraka_catalogue_parses_out_of_the_page(monkeypatch):
    """The option attribute is single-quoted around HTML-escaped JSON.

    A double-quote pattern matches nothing, which reads as "no products here"
    rather than as a parsing bug.
    """
    bank = get_bank("albaraka")
    serve(monkeypatch, albaraka, [{}], text=load("albaraka", "finance_page_options.html"))
    products = bank.products("finance")

    assert len(products) == 16
    # Nine products share the code IHTKRED; the campaign code is the identity.
    assert len({p.code for p in products}) == 16
    konut = next(p for p in products if p.code == "YKKNT0B")
    assert konut.name == "İLK EVİM KONUT FİNANSMANI"
    assert konut.max_term == 120


def test_albaraka_account_types_parse_out_of_the_page(monkeypatch):
    bank = get_bank("albaraka")
    serve(monkeypatch, albaraka, [{}],
          text=load("albaraka", "profit_share_page_select.html"))
    accounts = bank.products("profit_share")

    # Kur Korumalı is listed twice, bireysel and ticari, under one code.
    assert [a.code for a in accounts] == ["KTLMHSP", "KTLARDM", "KURKTLMHSP"]
    assert accounts[0].name == "Katılma Hesabı"
    assert accounts[1].currencies == ("TRY", "USD", "EUR")


def test_albaraka_finance_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("albaraka")
    serve(monkeypatch, albaraka, [load("albaraka", "finance_quote.json")],
          text=load("albaraka", "finance_page_options.html"))
    quote = bank.finance_quote("SIFIR KM TAŞIT FİNANSMANI", 100000, 24)

    assert quote.installment == 6684.28
    assert quote.total == 160422.77
    assert quote.profit_rate == 3.21
    assert quote.annual_cost_rate == 64.46
    assert quote.fees == {"ARTAO": 575.0}
    assert len(quote.schedule) == 24
    first = quote.schedule[0]
    assert first.amount == 6684.28
    assert first.taxes == pytest.approx(first.amount - first.principal - first.profit)


def test_albaraka_all_zero_profit_share_raises(monkeypatch):
    """Zeros mean "not offered" here too, and arrive with Result: true."""
    bank = get_bank("albaraka")
    serve(monkeypatch, albaraka, [load("albaraka", "profit_share_zeros.json")],
          text=load("albaraka", "profit_share_page_select.html"))
    with pytest.raises(UnsupportedProduct, match="no profit-share rate"):
        bank.profit_share_quote("Kur Korumalı Katılma Hesabı (Bireysel)", 100000, 6)


def test_albaraka_profit_share_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("albaraka")
    serve(monkeypatch, albaraka, [load("albaraka", "profit_share_quote.json")],
          text=load("albaraka", "profit_share_page_select.html"))
    quote = bank.profit_share_quote("Katılma Hesabı", 100000, 6, "TRY", "month")

    assert quote.net_profit == 14944.26
    assert quote.gross_profit == 18114.26
    assert quote.gross_annual_rate == pytest.approx(36.731684)
    # Albaraka publishes rates, not a participation ratio.
    assert quote.ratio is None


def test_albaraka_conversion_is_not_derived(monkeypatch):
    """Albaraka converts server-side, gold included, so nothing is computed."""
    bank = get_bank("albaraka")
    serve(monkeypatch, albaraka, [load("albaraka", "converter.json")])
    result = bank.convert("USD", "TRY", 1000)

    assert result.derived is False
    assert result.result == Decimal("47250")
    assert result.rate == Decimal("47.25")


def test_albaraka_rates_quote_gold_by_the_gram(monkeypatch):
    bank = get_bank("albaraka")
    serve(monkeypatch, albaraka, [load("albaraka", "rates.json")])
    rates = {r.code: r for r in bank.rates()}

    assert set(rates) == {"USD", "EUR", "XAU", "GBP"}
    assert rates["XAU"].unit == "gram"
    assert rates["USD"].sell >= rates["USD"].buy


# ----- tools -----


def test_the_tool_set_is_fixed_and_names_a_bank_as_an_argument():
    """Adding a bank must not add a tool: ten banks would be forty-plus tools."""
    tools = build_tools()
    assert [t.name for t in tools] == [
        "list_banks",
        "list_products",
        "finance_quote",
        "profit_share_quote",
        "exchange_rates",
        "card_installment_quote",
        "convert_currency",
    ]
    for tool in tools:
        if tool.name != "list_banks":
            assert "bank" in tool.args


def test_every_tool_description_names_the_live_banks():
    """Descriptions are prompt text and must not go stale as banks are added."""
    for tool in build_tools():
        if tool.name == "list_banks":
            continue
        assert "{banks}" not in tool.description
        for bank in BANKS:
            assert bank.name in tool.description


def test_a_tool_returns_json(monkeypatch):
    serve(monkeypatch, kuveytturk, [load("kuveytturk", "catalogue_card.json")])
    tool = next(t for t in build_tools() if t.name == "list_products")
    payload = json.loads(tool.invoke({"bank": "kuveytturk", "category": "card"}))

    assert [p["code"] for p in payload] == ["SK", "BP", "TK", "BP", "KK"]


def test_a_refusal_reaches_the_agent_as_a_sentence():
    """A traceback would end the agent's turn; a sentence lets it answer."""
    tool = next(t for t in build_tools() if t.name == "card_installment_quote")
    answer = tool.invoke(
        {"bank": "albaraka", "card": "any", "amount": 1000, "installments": 3}
    )

    assert answer.startswith("Albaraka")
    assert "does not publish" in answer
    with pytest.raises(json.JSONDecodeError):
        json.loads(answer)


def test_an_unknown_bank_reaches_the_agent_as_a_sentence():
    tool = next(t for t in build_tools() if t.name == "exchange_rates")
    answer = tool.invoke({"bank": "garanti", "codes": None})

    assert "Unknown bank" in answer
    assert "kuveytturk" in answer
