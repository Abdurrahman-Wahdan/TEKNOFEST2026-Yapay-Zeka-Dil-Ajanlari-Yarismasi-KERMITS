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
from banks.providers.base import UnsupportedProduct
from banks.models import FinanceQuote, PaymentRow, ProfitShareQuote, Product
from banks.parse import fold, money, rate
from banks.providers import BANKS, base, get_provider
from banks.providers.base import CAPABILITY_METHODS, TRANSPORTS, BaseBank

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "banks"


def load(bank: str, name: str):
    text = (FIXTURES / bank / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


def no_network(monkeypatch):
    """Fail loudly if anything reaches for the network.

    Used on the paths that must answer without a request: a bank with no
    endpoints, and a limit the bank states up front.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError(f"unexpected request: {args} {kwargs}")

    monkeypatch.setattr(base, "request_json", forbidden)
    monkeypatch.setattr(base, "request_text", forbidden)


def serve(
    monkeypatch,
    payloads: list,
    text: str = "",
    spy: list | None = None,
    routes: dict | None = None,
):
    """Answer each call from `payloads` in order, then repeat the last one.

    Every provider reaches the network through BaseBank._json / _text, so this
    is the one seam for all ten banks. Repeating the last payload matters: the
    profit-share providers try more than one reading of a term, and an all-zero
    fixture has to stay zero on every attempt.

    `routes` matches a URL fragment to a payload, for providers that call one
    endpoint per product and cannot be served by counting. `spy` collects the
    request bodies and parameters, for the cases where what we send is the
    thing under test.
    """
    queue = list(payloads)

    def fake_json(*args, **kwargs):
        if spy is not None:
            spy.append(kwargs)
        url = " ".join(a for a in args if isinstance(a, str))
        for fragment, payload in (routes or {}).items():
            if fragment in url:
                return payload
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(base, "request_json", fake_json)
    monkeypatch.setattr(base, "request_text", lambda *a, **k: text)
    monkeypatch.setattr(base, "csrf_token", lambda *a, **k: "test-token")


# ----- registry -----


ALL_BANKS = (
    "kuveytturk", "albaraka", "vakif", "emlak", "dunya",
    "ziraat", "turkiyefinans", "hayat", "tom", "adil",
)


def test_every_bank_in_the_list_resolves():
    """All ten, including the two with nothing to call."""
    assert [b.name for b in BANKS] == list(ALL_BANKS)
    for name in ALL_BANKS:
        assert get_bank(name).name == name
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
    listed = list_banks()
    assert set(listed) == set(ALL_BANKS)
    assert "card" in listed["kuveytturk"]["publishes"]
    assert "card" not in listed["albaraka"]["publishes"]
    assert listed["turkiyefinans"]["publishes"] == ["products"]


def test_every_capability_is_really_implemented():
    """capabilities is a promise, and this is what keeps it honest.

    Declaring a capability without overriding its method would refuse through
    the base class while claiming to work; overriding without declaring would
    answer while list_banks says it cannot.
    """
    for bank in BANKS:
        for capability, method in CAPABILITY_METHODS.items():
            own = getattr(type(bank), method)
            # An override marked @refusal explains a gap; it does not fill one.
            answers = own is not getattr(BaseBank, method) and not getattr(
                own, "is_refusal", False
            )
            assert answers == (capability in bank.capabilities), (
                f"{bank.name}: {capability} declared={capability in bank.capabilities} "
                f"answered={answers}"
            )


def test_every_bank_declares_a_known_transport():
    """The health checker reads this to know which banks are cheap to poll."""
    for bank in BANKS:
        assert bank.transport in TRANSPORTS, f"{bank.name}: {bank.transport}"
        assert (bank.impersonate is not None) == (bank.transport == "impersonate")


def test_banks_with_nothing_to_call_still_answer(monkeypatch):
    """Adil and T.O.M. are providers, not absent banks.

    "This bank does not publish a calculator" is a correct answer for a user;
    an unknown-bank error, a timeout or an empty result is not.
    """
    no_network(monkeypatch)
    for name in ("adil", "tom"):
        bank = get_bank(name)
        assert bank.capabilities == frozenset()
        assert bank.notes, f"{name} must say why it publishes nothing"
        with pytest.raises(UnsupportedProduct) as exc:
            bank.finance_quote("anything", 100000, 24)
        assert bank.display_name in str(exc.value)


def test_the_two_silent_banks_give_different_reasons():
    """Same answer today, different remedies: one needs a credential."""
    assert "credential" in get_bank("tom").notes
    assert "credential" not in get_bank("adil").notes


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
    serve(monkeypatch, [load("kuveytturk", "catalogue_finance.json")])
    by_code = bank.find_product("finance", "SAGLIKFINANSMANI")
    for spelling in ("İhtiyaç Finansmanı", "ihtiyac finansmani", "IHTIYAC FINANSMANI"):
        assert bank.find_product("finance", spelling) == by_code


def test_find_product_lists_the_alternatives_when_nothing_matches(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [load("kuveytturk", "catalogue_finance.json")])
    with pytest.raises(UnsupportedProduct) as exc:
        bank.find_product("finance", "mortgage")
    assert "İhtiyaç Finansmanı" in str(exc.value)


# ----- Kuveyt Türk parsing -----


def test_kuveytturk_catalogue_maps_onto_products(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [load("kuveytturk", "catalogue_finance.json")])
    products = bank.products("finance")

    assert len(products) == 19
    assert all(p.code and p.name and p.category == "finance" for p in products)
    shopping = next(p for p in products if p.code == "ECOMMERCE")
    assert shopping.name == "Alışveriş Finansmanı"
    assert (shopping.min_term, shopping.max_term) == (1, 36)
    assert (shopping.min_amount, shopping.max_amount) == (1000.0, 5000000.0)


def test_kuveytturk_profit_share_products_carry_currencies(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [load("kuveytturk", "catalogue_profit_share.json")])
    accounts = {p.name: p for p in bank.products("profit_share")}

    # TL-only, and the FEC list is the only place that is stated.
    assert accounts["Hoş Geldin Katılma Hesabı"].currencies == ("TRY",)
    assert accounts["Katılma Hesabı"].currencies == ("TRY", "USD", "EUR")
    # Three accounts publish no code of their own and are still nameable.
    assert accounts["Katılma Hesabı"].code


def test_kuveytturk_finance_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [
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
    serve(monkeypatch, [
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
    serve(monkeypatch, [
        load("kuveytturk", "catalogue_profit_share.json"),
        load("kuveytturk", "profit_share_year.json"),
    ], spy=sent)
    quote = get_bank("kuveytturk").profit_share_quote(
        "Katılma Hesabı", 100000, 12, "TRY", "month"
    )

    assert sent[-1]["json"]["p3"] == "360"
    assert quote.term == 360


def test_kuveytturk_all_zero_profit_share_raises(monkeypatch):
    """An unsupported combination answers 200 with every field zero.

    Returning that as a quote would report a real product as paying nothing.
    """
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [
        load("kuveytturk", "catalogue_profit_share.json"),
        load("kuveytturk", "profit_share_zeros.json"),
    ])
    with pytest.raises(UnsupportedProduct, match="no profit-share rate"):
        bank.profit_share_quote("Katılma Hesabı", 100000, 31, "TRY", "day")


def test_kuveytturk_refuses_yuvam_without_calling(monkeypatch):
    """The bank publishes no Yuvam rate at all, on its own page too."""
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [load("kuveytturk", "catalogue_profit_share.json")])
    with pytest.raises(UnsupportedProduct, match="Yuvam"):
        bank.profit_share_quote("Yuvam TL Katılma Hesabı", 100000, 31, "USD")


def test_kuveytturk_refuses_a_currency_the_product_does_not_take(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [load("kuveytturk", "catalogue_profit_share.json")])
    with pytest.raises(UnsupportedProduct, match="TRY"):
        bank.profit_share_quote("Hoş Geldin Katılma Hesabı", 100000, 31, "EUR")


def test_kuveytturk_card_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [
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
    serve(monkeypatch, [load("kuveytturk", "rates.json")])
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
    serve(monkeypatch, [load("kuveytturk", "rates.json")])
    result = bank.convert("XAU", "TRY", 10)

    assert result.derived is True
    assert isinstance(result.result, Decimal)
    gold = next(r for r in load("kuveytturk", "rates.json") if r["CurrencyCode"] == "ALT (gr)")
    assert result.result == Decimal(str(gold["BuyRate"])) * 10


def test_kuveytturk_refuses_a_currency_it_does_not_quote(monkeypatch):
    bank = get_bank("kuveytturk")
    serve(monkeypatch, [load("kuveytturk", "rates.json")])
    with pytest.raises(UnsupportedProduct, match="does not quote"):
        bank.convert("BTC", "TRY", 1)


# ----- Albaraka parsing -----


def test_albaraka_number_parsing_handles_both_separators():
    """Amounts and rates are formatted differently in the same response.

    money() on a rate gives 36731684, and a string test against "0,00 TRY"
    silently passes "0,00 USD", so both have to be parsed as numbers.
    """
    assert money("18.114,26 TRY") == 18114.26
    assert money("0,00 USD") == 0.0
    assert money("6.684,28 TL") == 6684.28
    assert rate("% 36.731684") == pytest.approx(36.731684)
    assert rate("% 0,175") == 0.175
    assert rate("% 64,46") == 64.46
    assert rate("3,21") == 3.21


def test_albaraka_catalogue_parses_out_of_the_page(monkeypatch):
    """The option attribute is single-quoted around HTML-escaped JSON.

    A double-quote pattern matches nothing, which reads as "no products here"
    rather than as a parsing bug.
    """
    bank = get_bank("albaraka")
    serve(monkeypatch, [{}], text=load("albaraka", "finance_page_options.html"))
    products = bank.products("finance")

    assert len(products) == 16
    # Nine products share the code IHTKRED; the campaign code is the identity.
    assert len({p.code for p in products}) == 16
    konut = next(p for p in products if p.code == "YKKNT0B")
    assert konut.name == "İLK EVİM KONUT FİNANSMANI"
    assert konut.max_term == 120


def test_albaraka_account_types_parse_out_of_the_page(monkeypatch):
    bank = get_bank("albaraka")
    serve(monkeypatch, [{}],
          text=load("albaraka", "profit_share_page_select.html"))
    accounts = bank.products("profit_share")

    # Kur Korumalı is listed twice, bireysel and ticari, under one code.
    assert [a.code for a in accounts] == ["KTLMHSP", "KTLARDM", "KURKTLMHSP"]
    assert accounts[0].name == "Katılma Hesabı"
    assert accounts[1].currencies == ("TRY", "USD", "EUR")


def test_albaraka_finance_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("albaraka")
    serve(monkeypatch, [load("albaraka", "finance_quote.json")],
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
    serve(monkeypatch, [load("albaraka", "profit_share_zeros.json")],
          text=load("albaraka", "profit_share_page_select.html"))
    with pytest.raises(UnsupportedProduct, match="no profit-share rate"):
        bank.profit_share_quote(
            "Kur Korumalı Katılma Hesabı (Bireysel)", 100000, 6, "TRY", "month"
        )


def test_albaraka_profit_share_quote_maps_onto_the_dataclass(monkeypatch):
    bank = get_bank("albaraka")
    serve(monkeypatch, [load("albaraka", "profit_share_quote.json")],
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
    serve(monkeypatch, [load("albaraka", "converter.json")])
    result = bank.convert("USD", "TRY", 1000)

    assert result.derived is False
    assert result.result == Decimal("47250")
    assert result.rate == Decimal("47.25")


def test_albaraka_rates_quote_gold_by_the_gram(monkeypatch):
    bank = get_bank("albaraka")
    serve(monkeypatch, [load("albaraka", "rates.json")])
    rates = {r.code: r for r in bank.rates()}

    assert set(rates) == {"USD", "EUR", "XAU", "GBP"}
    assert rates["XAU"].unit == "gram"
    assert rates["USD"].sell >= rates["USD"].buy


# ----- tools -----


def test_the_tool_set_is_fixed_and_names_a_bank_as_an_argument():
    """Adding a bank must not add a tool: ten banks would be forty-plus tools.

    The compare tools take `banks` rather than `bank` — comparing across banks
    is the one thing that cannot name a single one — so the invariant is that a
    tool says which banks it is about, either way round.
    """
    tools = build_tools()
    assert [t.name for t in tools] == [
        "list_banks",
        "list_products",
        "finance_quote",
        "profit_share_quote",
        "exchange_rates",
        "card_installment_quote",
        "convert_currency",
        "compare_finance",
        "compare_profit_share",
        "compare_exchange",
        "check_bank_health",
    ]
    for tool in tools:
        if tool.name != "list_banks":
            assert "bank" in tool.args or "banks" in tool.args


def test_every_tool_description_names_the_live_banks():
    """Descriptions are prompt text and must not go stale as banks are added.

    The compare tools name families instead: they cover every bank offering the
    family, so listing bank names in their description would be noise that goes
    stale on its own.
    """
    for tool in build_tools():
        if tool.name == "list_banks" or tool.name.startswith("compare_"):
            continue
        assert "{banks}" not in tool.description
        for bank in BANKS:
            assert bank.name in tool.description


def test_compare_descriptions_name_the_live_families():
    """Same guarantee for the compare tools, against the family table."""
    from banks import families

    described = {t.name: t.description for t in build_tools()}
    for name, category in (
        ("compare_finance", "finance"),
        ("compare_profit_share", "profit_share"),
    ):
        description = described[name]
        assert "{" not in description, f"{name} has an unfilled placeholder"
        for family in families.families(category):
            assert family in description


def test_a_tool_returns_json(monkeypatch):
    serve(monkeypatch, [load("kuveytturk", "catalogue_card.json")])
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


# ----- Vakıf parsing -----


def test_vakif_finance_catalogue_and_quote(monkeypatch):
    serve(monkeypatch, [], text=load("vakif", "finance_page_select.html"), routes={
        "FinancingInstallment": load("vakif", "installments.json"),
        "FinancingComputationExecute": load("vakif", "finance_quote.json"),
        "InstallmentPayBack": load("vakif", "payment_plan.json"),
    })
    bank = get_bank("vakif")
    products = {p.code: p for p in bank.products("finance")}

    assert set(products) == {"IF", "K", "K2", "BO", "BO2", "I", "A"}
    # The label is HTML-escaped in the page and must not reach the agent that way.
    assert products["IF"].name == "İhtiyaç Finansmanı"
    assert products["IF"].max_term == 36

    quote = bank.finance_quote("IF", 100000, 24)
    assert quote.installment == 7159.22
    assert quote.profit_rate == 3.75


def test_vakif_empty_body_is_not_a_price(monkeypatch):
    """Gold past one year answers 200 with nothing at all.

    Decoding that raises a JSON error that reads like a broken endpoint, so it
    becomes None and then a refusal.
    """
    serve(monkeypatch, [None], text=load("vakif", "finance_page_select.html"))
    with pytest.raises(UnsupportedProduct):
        get_bank("vakif").profit_share_quote("KAH", 100000, 366, "XAU", "day")


def test_vakif_error_message_inside_a_200_is_surfaced(monkeypatch):
    serve(monkeypatch, [{"errorMessage": "Tutar limitin altındadır."}],
          text=load("vakif", "finance_page_select.html"))
    with pytest.raises(UnsupportedProduct, match="limitin"):
        get_bank("vakif").profit_share_quote("KAH", 1, 31, "TRY", "day")


def test_vakif_converter_reads_the_turkish_figure(monkeypatch):
    serve(monkeypatch, [], routes={
        "DetailCurrencyListData": load("vakif", "currencies.json"),
        "CurrencyConverter": load("vakif", "converter.json"),
    })
    result = get_bank("vakif").convert("USD", "TRY", 1000)

    assert result.derived is False
    assert result.result > 1000


# ----- Emlak parsing -----


def test_emlak_reads_its_selects_rather_than_assuming(monkeypatch):
    serve(monkeypatch, [], text=load("emlak", "page_selects.html"),
          routes={"SelectLoansProperty": load("emlak", "loan_property.json")})
    bank = get_bank("emlak")
    products = {p.code: p for p in bank.products("finance")}

    assert "ARACBINEK2EL" in products
    assert products["ARACBINEK2EL"].name == "2. El Taşıt Finansmanı"
    account = bank.products("profit_share")[0]
    assert account.currencies == ("TL", "USD", "EUR", "ALT (gr)", "GMS (gr)")
    assert (account.min_term, account.max_term) == (31, 366)


def test_emlak_finance_quote_reads_the_plan(monkeypatch):
    """There is no top-level instalment field; it comes off the payment plan."""
    serve(monkeypatch, [], text=load("emlak", "page_selects.html"), routes={
        "SelectLoansProperty": load("emlak", "loan_property.json"),
        "CalculateLoansProduct": load("emlak", "finance_quote.json"),
    })
    quote = get_bank("emlak").finance_quote("ARACBINEK2EL", 100000, 24)

    assert quote.total == 183820.42
    assert quote.profit_rate == 4.29
    assert quote.installment == 7659.18
    assert len(quote.schedule) == 24
    first = quote.schedule[0]
    assert first.taxes == pytest.approx(first.amount - first.principal - first.profit)


def test_emlak_gold_past_six_months_raises(monkeypatch):
    serve(monkeypatch, [load("emlak", "profit_share_zeros.json")],
          text=load("emlak", "page_selects.html"))
    with pytest.raises(UnsupportedProduct, match="no profit-share rate"):
        get_bank("emlak").profit_share_quote("KATILMA", 100000, 364, "XAU", "day")


# ----- Dünya parsing -----


def test_dunya_reads_both_catalogues_off_the_homepage(monkeypatch):
    serve(monkeypatch, [], text=load("dunya", "home_selects.html"),
          routes={"LoanInstallmentValues": load("dunya", "loan_limits.json")})
    bank = get_bank("dunya")

    finance = {p.code: p for p in bank.products("finance")}
    assert "KONUTTUKETICI" in finance
    assert finance["KONUTTUKETICI"].name == "Konut Yeni"

    accounts = {p.code: p for p in bank.products("profit_share")}
    assert set(accounts) == {"KTLMHSP", "GNSHSP", "ALTKTLMHSP"}
    assert accounts["ALTKTLMHSP"].currencies == ("XAU",)


def test_dunya_amount_is_sent_without_separators(monkeypatch):
    """This endpoint strips dots as thousands separators.

    "100000.00" is read as ten million and answers with a plausible instalment a
    hundred times too large, with no error to warn anyone.
    """
    sent = []
    serve(monkeypatch, [], text=load("dunya", "home_selects.html"), spy=sent, routes={
        "LoanInstallmentValues": load("dunya", "loan_limits.json"),
        "LoanCheckRate": load("dunya", "finance_quote.json"),
    })
    get_bank("dunya").finance_quote("KONUTTUKETICI", 100000, 24)

    assert sent[-1]["data"]["amount"] == "100000"


def test_dunya_finance_quote_maps_onto_the_dataclass(monkeypatch):
    serve(monkeypatch, [], text=load("dunya", "home_selects.html"), routes={
        "LoanInstallmentValues": load("dunya", "loan_limits.json"),
        "LoanCheckRate": load("dunya", "finance_quote.json"),
    })
    quote = get_bank("dunya").finance_quote("KONUTTUKETICI", 100000, 24)

    # "monthlyInterest" is the instalment, despite the name.
    assert quote.installment == 5898.38
    assert quote.profit_rate == 2.99


def test_dunya_explains_its_own_refusal(monkeypatch):
    """Alone among the ten, this bank writes a usable error message."""
    serve(monkeypatch, [load("dunya", "profit_share_error.json")],
          text=load("dunya", "home_selects.html"))
    with pytest.raises(UnsupportedProduct) as exc:
        get_bank("dunya").profit_share_quote("GNSHSP", 100000, 31, "TRY", "day")
    assert "mevcut" in str(exc.value)


# ----- Ziraat parsing -----


def test_ziraat_catalogue_carries_the_rate_and_the_ceiling(monkeypatch):
    serve(monkeypatch, [], text=load("ziraat", "home_select.html"),
          routes={"get-vade": load("ziraat", "get_vade.json")})
    products = get_bank("ziraat").products("finance")

    assert len(products) == 17
    assert all(p.code.isdigit() for p in products), "the opaque eid is the identity"
    assert products[0].rate and products[0].rate > 0


def test_ziraat_reads_the_plan_out_of_drupal_markup(monkeypatch):
    serve(monkeypatch, [], text=load("ziraat", "home_select.html"), routes={
        "get-vade": load("ziraat", "get_vade.json"),
        "finansmanhesapla": load("ziraat", "finance_plan.json"),
    })
    quote = get_bank("ziraat").finance_quote("64356287", 100000, 24)

    assert quote.amount == 100000.0
    assert quote.installment == 8330.01
    assert quote.total == 199920.24
    assert len(quote.schedule) == 24
    assert quote.schedule[0].order == 1
    assert quote.schedule[-1].order == 24


def test_ziraat_will_not_swap_one_product_for_another(monkeypatch):
    """"İhtiyaç Finansmanı" is also a prefix of "İhtiyaç Finansmanı Hac / Umre".

    Quoting the pilgrimage product to someone who asked for the ordinary one
    would be a wrong answer, not a near miss.
    """
    serve(monkeypatch, [], text=load("ziraat", "home_select.html"),
          routes={"get-vade": load("ziraat", "get_vade.json")})
    with pytest.raises(UnsupportedProduct, match="matches several"):
        get_bank("ziraat").finance_quote("konut", 100000, 24)


def test_ziraat_refuses_its_browser_only_calculators(monkeypatch):
    """Kâr payı answers 493 to any non-browser client, so it is never called."""
    no_network(monkeypatch)
    with pytest.raises(UnsupportedProduct, match="browser-only"):
        get_bank("ziraat").profit_share_quote("any", 100000, 31)


# ----- Türkiye Finans -----


def test_turkiyefinans_publishes_tables_not_answers(monkeypatch):
    serve(monkeypatch, [load("turkiyefinans", "credit_types.json")])
    products = get_bank("turkiyefinans").products("finance")

    assert len(products) == 18
    # The same Code repeats under different CreditIDs with different fees.
    assert len({p.code for p in products}) == 18


def test_turkiyefinans_rate_table_becomes_products(monkeypatch):
    serve(monkeypatch, [load("turkiyefinans", "rate_table.json")])
    rows = get_bank("turkiyefinans").products("profit_share")

    assert rows and all(r.rate and r.rate > 0 for r in rows)
    assert all(r.min_amount for r in rows)


def test_turkiyefinans_refusal_still_names_the_rate(monkeypatch):
    """It cannot give a payment, but it can give what it does publish."""
    serve(monkeypatch, [load("turkiyefinans", "credit_types.json")])
    with pytest.raises(UnsupportedProduct) as exc:
        get_bank("turkiyefinans").finance_quote("1", 100000, 24)
    message = str(exc.value)
    assert "no instalment figure" in message
    assert "monthly profit rate" in message


# ----- Hayat -----


def test_hayat_account_types_come_from_the_page(monkeypatch):
    serve(monkeypatch, [], text=load("hayat", "home_accounts.txt"))
    accounts = {p.name: p for p in get_bank("hayat").products("profit_share")}

    # The API takes `accountType`, which is not the option's `value`; they
    # differ by one, so reading the wrong field prices the wrong account.
    assert accounts["Katılma Hesabı"].raw["AccountType"] == 0
    assert accounts["Avantajlı Hesap"].raw["AccountType"] == 1


def test_hayat_minimum_balance_is_checked_before_calling(monkeypatch):
    """Below 50 000 TL the answer is the reason, not a zero to interpret."""
    serve(monkeypatch, [], text=load("hayat", "home_accounts.txt"))
    bank = get_bank("hayat")
    bank.products("profit_share")

    no_network(monkeypatch)
    with pytest.raises(UnsupportedProduct, match="50,000"):
        bank.profit_share_quote("Katılma Hesabı", 49_999, 32)


def test_hayat_prices_only_turkish_lira(monkeypatch):
    serve(monkeypatch, [], text=load("hayat", "home_accounts.txt"))
    with pytest.raises(UnsupportedProduct, match="TRY"):
        get_bank("hayat").profit_share_quote("Katılma Hesabı", 100_000, 32, "USD")


def test_hayat_conversion_is_derived(monkeypatch):
    serve(monkeypatch, [load("hayat", "fxrate.json")])
    result = get_bank("hayat").convert("USD", "TRY", 1000)

    assert result.derived is True
    assert isinstance(result.result, Decimal)


# ----- the shared parser -----


def test_the_shared_parser_handles_every_bank_style():
    """Six banks send formatted strings, four send numbers, some send both."""
    assert money("6.684,28 TL") == 6684.28
    assert money("18.114,26 TRY") == 18114.26
    assert money(183820.42) == 183820.42
    assert money("0,00 USD") == 0.0
    assert money(None) == 0.0

    assert rate("% 36.731684") == pytest.approx(36.731684)
    assert rate("%31,80") == 31.80
    assert rate(4.11) == 4.11
    assert rate("4.99") == 4.99


def test_dunya_converter_refuses_a_fractional_amount(monkeypatch):
    """It drops separators instead of parsing them.

    "1000.0" is read as 10 000 and "10,5" as 105, each answered with a
    plausible figure and no error, so a fraction cannot be stated truthfully.
    """
    no_network(monkeypatch)
    with pytest.raises(UnsupportedProduct, match="whole amounts only"):
        get_bank("dunya").convert("USD", "TRY", 10.5)


def test_dunya_converter_checks_what_the_bank_read(monkeypatch):
    """The bank echoes the amount it understood; disagreeing with it is an error."""
    serve(monkeypatch, [{"result": "SUCCESS", "sourceAmount": 10000.0,
                         "destinationAmount": 475195.0}])
    with pytest.raises(UnsupportedProduct, match="would not be to the question"):
        get_bank("dunya").convert("USD", "TRY", 1000)


# ----- term bands: the defect that answered a different question -----


_ANY_PROFIT = {
    "vakif": {"grossProfit": "38.000,00 TL", "netProfit": "31.323,29 TL",
              "grossRate": "%38,00", "netRate": "%31,00"},
    "emlak": {"Success": True, "Data": {"GrossProfitShare": 42000.0,
              "NetProfitShare": 35255.56, "GrossProfitShareYearly": 42.0,
              "NetProfitShareYearly": 35.2}},
    "dunya": {"result": "SUCCESS", "grossProfitAmount": 41000.0,
              "netProfitAmount": 33928.72, "grossProfitRate": 41.0,
              "netProfitRate": 33.9},
}

BAND_BANKS = [
    ("vakif", "KAH", "vakif/finance_page_select.html"),
    ("emlak", "KATILMA", "emlak/page_selects.html"),
    ("dunya", "KTLMHSP", "dunya/home_selects.html"),
]


@pytest.mark.parametrize("name,account,page", BAND_BANKS)
def test_a_year_reaches_the_yearly_band(monkeypatch, name, account, page):
    """Snapping down to the nearest band at or below the request was wrong.

    These banks price a fixed list of month-labelled terms. A year is 360 days,
    which falls short of the 364/365 yearly band and used to land on the
    six-month one — returning about 44% of the right figure as a confident,
    well-formed quote with only `term` to hint at it.
    """
    sent = []
    bank = get_bank(name)
    fixture = page.split("/")[0]
    serve(monkeypatch, [], text=load(fixture, page.split("/")[1]), spy=sent, routes={
        "SelectLoansProperty": load("emlak", "loan_property.json"),
        "LoanInstallmentValues": load("dunya", "loan_limits.json"),
        "FinancingInstallment": load("vakif", "installments.json"),
    })
    bank.products("profit_share")

    serve(monkeypatch, [_ANY_PROFIT[name]], text=load(fixture, page.split("/")[1]), spy=sent)
    quote = bank.profit_share_quote(account, 100_000, 12, "TRY", "month")

    assert quote.term >= 364, f"{name} answered a {quote.term}-day question"


@pytest.mark.parametrize("name,account,page", BAND_BANKS)
def test_a_term_no_band_comes_near_is_refused(monkeypatch, name, account, page):
    """`term_unit` defaults to empty, so a bare 12 must not become 31 days.

    Silently pricing 31 days for someone who said 12 is the same failure in a
    smaller disguise.
    """
    bank = get_bank(name)
    fixture = page.split("/")[0]
    serve(monkeypatch, [], text=load(fixture, page.split("/")[1]), routes={
        "SelectLoansProperty": load("emlak", "loan_property.json"),
        "LoanInstallmentValues": load("dunya", "loan_limits.json"),
        "FinancingInstallment": load("vakif", "installments.json"),
    })
    bank.products("profit_share")

    no_network(monkeypatch)
    with pytest.raises(UnsupportedProduct, match="fixed terms only"):
        bank.profit_share_quote(account, 100_000, 12, "TRY", None)


def test_band_choice_is_nearest_not_next_lowest():
    """The rule itself, without a bank in the way."""
    bank = get_bank("vakif")
    bands = (31, 91, 180, 364, 366)

    assert bank._band(360, bands) == 364      # a year, ×30
    assert bank._band(30, bands) == 31        # a month, ×30
    assert bank._band(90, bands) == 91
    assert bank._band(180, bands) == 180
    assert bank._band(720, bands) == 366      # past the last band, which is open-ended
    for hopeless in (12, 60, 250):
        with pytest.raises(UnsupportedProduct, match="fixed terms only"):
            bank._band(hopeless, bands)


# ----- the other review findings -----


def test_rate_filter_resolves_standard_codes_to_bank_names(monkeypatch):
    """XAU must find gold at a bank that calls it "ALT (gr)".

    Comparing the requested code against the bank's own name matched nothing,
    so the answer read as "this bank does not quote gold" while quoting it.
    """
    serve(monkeypatch, [load("kuveytturk", "rates.json")])
    found = get_bank("kuveytturk").find_rates(["XAU"])

    assert [r.code for r in found] == ["ALT (gr)"]
    assert found[0].unit == "gram"


def test_rate_filter_leaves_standard_feeds_alone(monkeypatch):
    serve(monkeypatch, [load("albaraka", "rates.json")])
    assert [r.code for r in get_bank("albaraka").find_rates(["XAU"])] == ["XAU"]


def test_conversion_answers_in_the_codes_that_were_asked_for(monkeypatch):
    """The bank's internal name for gold is not the caller's question."""
    serve(monkeypatch, [load("kuveytturk", "rates.json")])
    result = get_bank("kuveytturk").convert("XAU", "TRY", 10)

    assert (result.source, result.target) == ("XAU", "TRY")


def test_a_missing_sell_rate_does_not_divide_by_zero(monkeypatch):
    serve(monkeypatch, [[
        {"CurrencyCode": "USD", "CurrencyDescription": "Dolar", "BuyRate": 40.0, "SellRate": 41.0},
        {"CurrencyCode": "TL", "CurrencyDescription": "Lira", "BuyRate": 0.0, "SellRate": 0.0},
    ]])
    with pytest.raises(UnsupportedProduct, match="no sell rate"):
        get_bank("kuveytturk").convert("USD", "TRY", 1000)


def test_hayat_refuses_a_figure_that_ignores_the_term(monkeypatch):
    """Avantajlı Günlük Hesap returns one day's profit for any term.

    32, 60, 90 and 365 days all come back 79,95 TL on 100 000, so passing it
    through would have the agent say "365 gün için 79,95 TL".
    """
    serve(monkeypatch, [], text=load("hayat", "home_accounts.txt"))
    bank = get_bank("hayat")
    bank.products("profit_share")

    serve(monkeypatch, [{"isSuccessful": True, "data": {
        "grossProfitShare": 96.91, "netProfitShare": 79.95,
        "grossProfitShareYearly": 35.37, "netProfitShareYearly": 29.18}}])
    with pytest.raises(UnsupportedProduct, match="does not follow"):
        bank.profit_share_quote("Avantajlı Günlük Hesap", 100_000, 365, "TRY", "day")


def test_an_unexpected_failure_still_reaches_the_agent_as_words(monkeypatch):
    """Only a refusal is a ValueError. Anything else is our bug — and must
    still not end the agent's turn with a traceback."""
    def explode(*args, **kwargs):
        raise KeyError("Meta")

    monkeypatch.setattr(base, "request_json", explode)
    monkeypatch.setattr(base, "request_text", explode)
    tool = next(t for t in build_tools() if t.name == "exchange_rates")
    answer = tool.invoke({"bank": "kuveytturk", "codes": None})

    assert "failed unexpectedly" in answer
    assert "KeyError" in answer


def test_vakif_returns_a_payment_schedule(monkeypatch):
    """The plan is a second call at this bank; it used to be left empty."""
    serve(monkeypatch, [], text=load("vakif", "finance_page_select.html"), routes={
        "FinancingInstallment": load("vakif", "installments.json"),
        "FinancingComputationExecute": load("vakif", "finance_quote.json"),
        "InstallmentPayBack": load("vakif", "payment_plan.json"),
    })
    quote = get_bank("vakif").finance_quote("IF", 100000, 24)

    assert len(quote.schedule) == 24
    first = quote.schedule[0]
    assert first.order == 1
    # "bsmfTutari" is the bank's own spelling of BSMV.
    assert first.taxes == pytest.approx(first.amount - first.principal - first.profit)


def test_vakif_keeps_the_quote_when_the_plan_call_fails(monkeypatch):
    """The instalment is what most questions want; a missing plan is not fatal."""
    def maybe(*args, **kwargs):
        url = " ".join(a for a in args if isinstance(a, str))
        if "InstallmentPayBack" in url:
            raise ValueError("plan unavailable")
        if "FinancingInstallment" in url:
            return load("vakif", "installments.json")
        return load("vakif", "finance_quote.json")

    monkeypatch.setattr(base, "request_json", maybe)
    monkeypatch.setattr(base, "request_text", lambda *a, **k: load("vakif", "finance_page_select.html"))
    monkeypatch.setattr(base, "csrf_token", lambda *a, **k: "t")
    quote = get_bank("vakif").finance_quote("IF", 100000, 24)

    assert quote.installment == 7159.22
    assert quote.schedule == []


# ----- limits the bank itself declared -----


def _ziraat(monkeypatch):
    serve(monkeypatch, [], text=load("ziraat", "home_select.html"),
          routes={"get-vade": load("ziraat", "get_vade.json"),
                  "finansmanhesapla": load("ziraat", "finance_plan.json")})
    return get_bank("ziraat")


def test_ziraat_checks_the_band_even_when_named_exactly(monkeypatch):
    """The exact-name path used to skip the fit check entirely.

    list_products teaches the model the exact names, so that was the likely
    path, not the rare one. Asking 200 000 TL of a 124 999 band answered
    200 000,16 — a principal-only schedule, 0,16 TL of profit — while reporting
    a 4,99% rate. Self-contradictory, and returned as a valid quote.
    """
    bank = _ziraat(monkeypatch)
    exact = next(p for p in bank.products("finance") if p.max_amount == 124999.0)

    with pytest.raises(UnsupportedProduct, match="band covering"):
        bank.finance_quote(exact.name, 200_000, 36)


def test_ziraat_checks_the_term_even_when_named_exactly(monkeypatch):
    """A 1–12 month product must not be quoted over 36 months at its rate."""
    bank = _ziraat(monkeypatch)
    product = bank.products("finance")[0]
    beyond = (product.max_term or 36) + 12

    with pytest.raises(UnsupportedProduct, match="band covering"):
        bank.finance_quote(product.name, 1000, beyond)


def test_an_amount_over_a_declared_ceiling_is_refused(monkeypatch):
    """Dünya declares a ceiling per product and will still quote past it.

    The answer is arithmetically consistent, so nothing downstream can catch
    it — only the bank's own declared limit can.
    """
    serve(monkeypatch, [], text=load("dunya", "home_selects.html"),
          routes={"LoanInstallmentValues": load("dunya", "loan_limits.json")})
    bank = get_bank("dunya")
    ceiling = bank.products("finance")[0].max_amount

    no_network(monkeypatch)
    with pytest.raises(UnsupportedProduct, match="capped at"):
        bank.finance_quote("KONUTTUKETICI", ceiling * 5, 24)


def test_a_card_will_not_take_more_instalments_than_it_offers(monkeypatch):
    serve(monkeypatch, [], text=load("vakif", "finance_page_select.html"))
    with pytest.raises(UnsupportedProduct, match="runs to 12 instalments"):
        get_bank("vakif").card_installment_quote("Ferah Kart", 10_000, 99)


@pytest.mark.parametrize("amount,term,match", [
    (0, 24, "positive number of currency units"),
    (-5, 24, "positive number of currency units"),
    (100_000, 0, "positive number of months"),
    (100_000, -3, "positive number of months"),
])
def test_non_positive_input_is_refused_before_the_call(monkeypatch, amount, term, match):
    """A zero term used to reach the endpoint and come back as a bare 404."""
    serve(monkeypatch, [load("kuveytturk", "catalogue_finance.json")])
    bank = get_bank("kuveytturk")
    bank.products("finance")

    no_network(monkeypatch)
    with pytest.raises(UnsupportedProduct, match=match):
        bank.finance_quote("IHTIYACKART", amount, term)


# ----- identity, currency and caching -----


def test_a_repeated_product_code_is_ambiguous_not_first_wins(monkeypatch):
    """Kuveyt Türk lists ELKTRARACSARJUNITE twice with different real limits.

    Returning the first made the second unreachable by code and quoted the
    wrong ceiling for it.
    """
    serve(monkeypatch, [load("kuveytturk", "catalogue_finance.json")])
    bank = get_bank("kuveytturk")

    with pytest.raises(UnsupportedProduct, match="more than once"):
        bank.find_product("finance", "ELKTRARACSARJUNITE")
    # Both remain reachable by name, which is unique.
    for name in ("Bisiklet Finansmanı", "Elektrikli Araç Şarj Ünitesi Finansmanı"):
        assert bank.find_product("finance", name).code == "ELKTRARACSARJUNITE"


def test_the_advertised_term_ceiling_is_the_entry_s_own(monkeypatch):
    """Both entries declare MaturityTermMax 36; one really stops at 1."""
    serve(monkeypatch, [load("kuveytturk", "catalogue_finance.json")])
    limits = {
        p.name: p.max_term
        for p in get_bank("kuveytturk").products("finance")
        if p.code == "ELKTRARACSARJUNITE"
    }
    assert limits["Bisiklet Finansmanı"] == 36
    assert limits["Elektrikli Araç Şarj Ünitesi Finansmanı"] == 1


def test_converting_a_currency_to_itself_keeps_the_amount(monkeypatch):
    """Otherwise the buy/sell spread is applied against itself: 10 USD -> 9,09."""
    no_network(monkeypatch)
    result = get_bank("kuveytturk").convert("USD", "USD", 10)

    assert result.result == Decimal("10")
    assert result.rate == Decimal(1)


def test_currency_codes_are_not_case_sensitive(monkeypatch):
    """"usd" used to be refused with a list that visibly contained USD."""
    serve(monkeypatch, [load("kuveytturk", "rates.json")])
    lower = get_bank("kuveytturk").convert("usd", "try", 1000)

    serve(monkeypatch, [load("kuveytturk", "rates.json")])
    upper = get_bank("kuveytturk").convert("USD", "TRY", 1000)
    assert lower.result == upper.result


def test_profit_share_limits_are_per_currency(monkeypatch):
    """A single pair would show the lira figures against a USD request."""
    serve(monkeypatch, [load("kuveytturk", "catalogue_profit_share.json")])
    account = next(
        p for p in get_bank("kuveytturk").products("profit_share")
        if p.name == "Katılma Hesabı"
    )

    assert account.min_amount is None, "a flat limit would be the TL one mislabelled"
    assert account.raw["_limits"]["USD"] != account.raw["_limits"]["TRY"]


def test_a_catalogue_does_not_outlive_its_ttl(monkeypatch):
    """Two catalogues carry live rates inside them, so they cannot be forever."""
    import banks.providers.base as base_module

    serve(monkeypatch, [load("kuveytturk", "catalogue_finance.json")])
    bank = get_bank("kuveytturk")
    bank.products("finance")
    assert bank._cached("finance") is not None

    monkeypatch.setattr(base_module, "CATALOGUE_TTL_SECONDS", -1)
    assert bank._cached("finance") is None


def test_list_products_omits_what_the_bank_left_blank(monkeypatch):
    """55 rate rows carrying null limits is prompt weight for no meaning."""
    serve(monkeypatch, [load("kuveytturk", "catalogue_profit_share.json")])
    tool = next(t for t in build_tools() if t.name == "list_products")
    rows = json.loads(tool.invoke({"bank": "kuveytturk", "category": "profit_share"}))

    assert all("min_amount" not in r or r["min_amount"] is not None for r in rows)
    assert all(None not in r.values() for r in rows)


@pytest.mark.parametrize("spelling", ["ay", "AY", "aylık", "month", "months"])
def test_a_turkish_term_unit_is_understood(monkeypatch, spelling):
    """A Turkish-speaking model asked about "12 ay" may well send "ay"."""
    sent = []
    serve(monkeypatch, [
        load("kuveytturk", "catalogue_profit_share.json"),
        load("kuveytturk", "profit_share_year.json"),
    ], spy=sent)
    get_bank("kuveytturk").profit_share_quote("Katılma Hesabı", 100000, 12, "TRY", spelling)

    assert sent[-1]["json"]["p3"] == "360"


def test_an_unknown_term_unit_says_what_is_accepted():
    from banks.parse import term_unit

    with pytest.raises(ValueError, match="days or months"):
        term_unit("haftalık")


def test_an_endpoint_url_never_reaches_the_answer():
    """The URL carries the bank's opaque calculator hash."""
    from banks.http import request

    class Refused:
        status_code = 404
        text = ""

        @staticmethod
        def json():
            raise ValueError("no body")

    import banks.http as http_module
    original = http_module.get_client
    http_module.get_client = lambda impersonate=None: type(
        "C", (), {"request": staticmethod(lambda *a, **k: Refused())}
    )()
    try:
        with pytest.raises(ValueError) as exc:
            request("POST", "https://www.kuveytturk.com.tr/ck0d84?30134915811C6D92")
    finally:
        http_module.get_client = original

    assert "ck0d84" not in str(exc.value)
    assert "404" in str(exc.value)


# ----- the generic guard: a quote has to add up -----


def _finance_quote(**overrides):
    """A well-formed quote, so each test can break exactly one thing."""
    product = Product(code="X", name="Test Finansmanı", category="finance")
    fields = dict(
        bank="test", product=product, amount=100_000.0, term=24,
        installment=7_000.0, total=168_000.0, profit_rate=4.0,
        annual_cost_rate=None, fees={}, schedule=[], raw={},
    )
    fields.update(overrides)
    return FinanceQuote(**fields)


def test_a_well_formed_quote_passes_the_guard():
    assert get_bank("kuveytturk")._check_quote(_finance_quote()) is not None


def test_a_total_no_higher_than_the_advance_is_not_a_quote():
    with pytest.raises(UnsupportedProduct, match="not more than what is borrowed"):
        get_bank("kuveytturk")._check_quote(_finance_quote(total=100_000.0))


def test_a_rate_that_contradicts_the_total_is_refused():
    """The Ziraat 0,16 TL case, in the shape the guard sees it.

    A 200 000 advance came back as a 200 000,16 total — every schedule row
    principal-only — while reporting a 4,99% monthly rate. Arithmetically
    self-contradictory, and returned as a valid quote.
    """
    with pytest.raises(UnsupportedProduct, match="contradict each other"):
        get_bank("ziraat")._check_quote(
            _finance_quote(amount=200_000.0, total=200_000.16, term=36,
                           installment=5_555.56, profit_rate=4.99)
        )


def test_a_plan_that_does_not_match_the_term_is_refused():
    rows = [PaymentRow(order=i, amount=1.0, principal=1.0, profit=0.0,
                       taxes=0.0, remaining=0.0) for i in range(1, 13)]
    with pytest.raises(UnsupportedProduct, match="does not match the term"):
        get_bank("kuveytturk")._check_quote(_finance_quote(term=24, schedule=rows))


def test_a_zero_instalment_is_refused():
    with pytest.raises(UnsupportedProduct, match="no instalment"):
        get_bank("kuveytturk")._check_quote(_finance_quote(installment=0.0))


def _profit_quote(**overrides):
    product = Product(code="X", name="Test Hesabı", category="profit_share")
    fields = dict(
        bank="test", product=product, amount=100_000.0, term=365,
        currency="TRY", term_unit="day", ratio=None,
        gross_profit=40_000.0, net_profit=33_000.0,
        gross_annual_rate=40.0, net_annual_rate=33.0, raw={},
    )
    fields.update(overrides)
    return ProfitShareQuote(**fields)


def test_a_profit_that_follows_from_the_rate_passes():
    assert get_bank("kuveytturk")._check_profit_share(_profit_quote()) is not None


def test_a_profit_that_ignores_the_term_is_refused():
    """Hayat's daily account: one day's profit returned for any term.

    Now caught in the base layer for every bank, not just the one where it was
    found.
    """
    with pytest.raises(UnsupportedProduct, match="does not follow"):
        get_bank("hayat")._check_profit_share(_profit_quote(net_profit=79.95))


def test_a_net_above_the_gross_is_refused():
    with pytest.raises(UnsupportedProduct, match="net profit above the gross"):
        get_bank("kuveytturk")._check_profit_share(
            _profit_quote(gross_profit=100.0, net_profit=200.0)
        )


# ----- what the agent can now ask for -----


def test_the_payment_schedule_is_available_on_request(monkeypatch):
    """It was parsed and then discarded; "ödeme planını göster" had no answer."""
    routes = {
        "FinancingInstallment": load("vakif", "installments.json"),
        "FinancingComputationExecute": load("vakif", "finance_quote.json"),
        "InstallmentPayBack": load("vakif", "payment_plan.json"),
    }
    tool = next(t for t in build_tools() if t.name == "finance_quote")
    call = {"bank": "vakif", "product": "IF", "amount": 100000, "term_months": 24}

    serve(monkeypatch, [], text=load("vakif", "finance_page_select.html"), routes=routes)
    lean = json.loads(tool.invoke(call))
    serve(monkeypatch, [], text=load("vakif", "finance_page_select.html"), routes=routes)
    full = json.loads(tool.invoke({**call, "include_schedule": True}))

    assert "payment_schedule" not in lean, "the plan must stay off by default"
    assert lean["schedule_rows"] == 24
    assert len(full["payment_schedule"]) == 24
    assert full["payment_schedule"][0]["principal"] > 0


def test_rates_carry_the_moment_they_were_quoted(monkeypatch):
    """FX moves intraday; without this the agent cannot say how fresh a rate is."""
    serve(monkeypatch, [load("albaraka", "rates.json")])
    assert all(r.as_of for r in get_bank("albaraka").rates())

    # Kuveyt Türk's feed publishes no date, and an empty string is dropped
    # rather than shown as a null.
    serve(monkeypatch, [load("kuveytturk", "rates.json")])
    tool = next(t for t in build_tools() if t.name == "exchange_rates")
    rows = json.loads(tool.invoke({"bank": "kuveytturk", "codes": ["USD"]}))
    assert "as_of" not in rows[0]
